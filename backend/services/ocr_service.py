"""
Offline OCR service (air-gapped) — powered by PaddleOCR (PP-OCRv5) models.

This module runs the **PaddleOCR** PP-OCRv5 text-detection + text-recognition
models through :mod:`rapidocr_onnxruntime` (an ONNXRuntime execution wrapper
around the exact same PaddleOCR/PP-OCR models). ONNXRuntime is used instead of
the native ``paddlepaddle`` engine because the platform runs on ARM64/aarch64
hardware where PaddlePaddle's native inference predictor is unsupported and
crashes; ONNXRuntime executes the identical PP-OCR models reliably and is far
lighter (no multi-GB framework).

Multilingual: the bundled ``latin`` PP-OCRv5 recognition model covers ~37
Latin-script languages (German, English, French, Spanish, Italian, Portuguese,
Dutch, Polish, Turkish, ...) including German diacritics (ä ö ü ß) — so a single
model handles German + English (and more) offline.

PDFs are rasterized page-by-page with PDFium (``pypdfium2``) — a self-contained
wheel with no external binaries. HEIC/HEIF phone photos are supported via
``pillow-heif``; AVIF via ``pillow-avif-plugin``. Everything runs locally — no
cloud OCR, no runtime model downloads (models ship with the app / the wheel).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import threading
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from core.config import get_settings

logger = logging.getLogger("ocr")
settings = get_settings()

# Try to enable HEIC/HEIF support (phone photos).
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIC_ENABLED = True
except Exception:  # pragma: no cover
    _HEIC_ENABLED = False

# Try to enable AVIF support (modern web images).
try:
    import pillow_avif  # noqa: F401  (registers the AVIF plugin on import)

    _AVIF_ENABLED = True
except Exception:  # pragma: no cover
    _AVIF_ENABLED = False

# File extensions handled directly by OCR / vision.
IMAGE_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".gif",
    ".heic",
    ".heif",
    ".avif",
}

# Formats that vision LLM APIs accept natively (everything else is re-encoded).
_VISION_SAFE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_MAX_VISION_DIM = 2400  # px — larger images are downscaled before upload
_MAX_VISION_BYTES = 4 * 1024 * 1024


class OCRError(Exception):
    """Raised when OCR fails on an image or PDF."""


# ---------------------------------------------------------------------------
# Bundled PaddleOCR (PP-OCRv5) model resolution.
#
# The recognition model + character dictionary are shipped with the app under
# ``backend/models/ocr``. The text-detection + angle-classification models are
# bundled inside the ``rapidocr_onnxruntime`` wheel (PP-OCR mobile det/cls), so
# nothing is downloaded at runtime and the service is fully air-gapped.
# ---------------------------------------------------------------------------
def _model_dir() -> Path:
    if settings.ocr_model_dir:
        return Path(settings.ocr_model_dir)
    return Path(__file__).resolve().parent.parent / "models" / "ocr"


# Language/script -> bundled PP-OCRv5 recognition model + dictionary.
# Only the multilingual "latin" model is bundled (covers German + English +
# ~35 more Latin-script languages). Extra scripts can be added by dropping the
# matching ``<script>_rec.onnx`` / ``<script>_dict.txt`` files here.
_REC_MODELS = {
    "latin": ("latin_rec.onnx", "latin_dict.txt"),
}


class OCRService:
    """PaddleOCR (PP-OCRv5) OCR for images and scanned PDFs, via ONNXRuntime."""

    _engine = None
    _engine_lock = threading.Lock()
    _engine_desc = "paddleocr(unloaded)"

    def __init__(self) -> None:
        # e.g. "latin" (multilingual Latin: German + English + ...).
        self.lang = (settings.ocr_languages or "latin").strip().lower()
        self.dpi = settings.ocr_dpi

    @staticmethod
    def is_image(ext: str) -> bool:
        return ext.lower() in IMAGE_EXTS

    # ------------------------------------------------------------------
    # Engine (lazy, process-wide singleton — ONNXRuntime sessions are
    # thread-safe for inference and expensive to build).
    # ------------------------------------------------------------------
    @classmethod
    def _get_engine(cls):
        if cls._engine is not None:
            return cls._engine
        with cls._engine_lock:
            if cls._engine is not None:
                return cls._engine

            from rapidocr_onnxruntime import RapidOCR

            settings_local = get_settings()
            lang = (settings_local.ocr_languages or "latin").strip().lower()
            model_dir = _model_dir()

            rec_file, dict_file = _REC_MODELS.get(lang, _REC_MODELS["latin"])
            rec_path = model_dir / rec_file
            dict_path = model_dir / dict_file

            kwargs: dict = {}
            if rec_path.exists() and dict_path.exists():
                kwargs["rec_model_path"] = str(rec_path)
                kwargs["rec_keys_path"] = str(dict_path)
                cls._engine_desc = f"paddleocr:{lang}"
                logger.info(
                    "Loading PaddleOCR (PP-OCRv5) OCR engine",
                    extra={"extra_fields": {"lang": lang, "recModel": rec_file}},
                )
            else:
                # Fall back to the wheel's built-in PP-OCR (Chinese+English) models
                # so the service still works even if the Latin model is missing.
                cls._engine_desc = "paddleocr:builtin(ch+en)"
                logger.warning(
                    "Latin PP-OCRv5 model not found; using built-in PP-OCR models",
                    extra={"extra_fields": {"modelDir": str(model_dir)}},
                )

            cls._engine = RapidOCR(**kwargs)
            return cls._engine

    @classmethod
    def engine_name(cls) -> str:
        return cls._engine_desc

    # ------------------------------------------------------------------
    # Vision helper: normalize any image file into a base64 payload that
    # vision LLM APIs accept (JPEG/PNG/WEBP, bounded dimensions/size).
    # ------------------------------------------------------------------
    @staticmethod
    def image_to_b64(path: str) -> tuple[str, str]:
        """Return ``(base64, mime)`` for a vision-LLM-safe rendition of the image.

        JPEG/PNG/WEBP within size bounds pass through untouched; AVIF, HEIC,
        TIFF, BMP, GIF (first frame) and oversized images are re-encoded to JPEG.
        """
        ext = os.path.splitext(path)[1].lower()
        try:
            if (
                ext in _VISION_SAFE_MIME
                and os.path.getsize(path) <= _MAX_VISION_BYTES
            ):
                with Image.open(path) as img:
                    if max(img.size) <= _MAX_VISION_DIM:
                        with open(path, "rb") as handle:
                            data = handle.read()
                        return base64.b64encode(data).decode("ascii"), _VISION_SAFE_MIME[ext]

            with Image.open(path) as img:
                img = img.convert("RGB")  # also extracts the first frame of GIFs
                img.thumbnail((_MAX_VISION_DIM, _MAX_VISION_DIM), Image.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=92)
            return base64.b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"
        except Exception as exc:
            raise OCRError(f"Could not read/normalize image for vision input: {exc}") from exc

    # ------------------------------------------------------------------
    # PaddleOCR (PP-OCRv5) path
    # ------------------------------------------------------------------
    @staticmethod
    def _load_rgb(path: str) -> np.ndarray:
        """Open any supported image (incl. HEIC/AVIF/TIFF/GIF) as an RGB ndarray."""
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)  # respect phone-photo orientation
            return np.array(img.convert("RGB"))

    @staticmethod
    def _results_to_text(result) -> str:
        """Turn RapidOCR ``[[box, text, score], ...]`` into clean line-ordered text.

        Boxes are grouped into text lines by vertical position, then ordered
        left-to-right within each line, so multi-column detections read naturally.
        """
        if not result:
            return ""

        items = []
        for box, text, _score in result:
            text = (text or "").strip()
            if not text:
                continue
            ys = [pt[1] for pt in box]
            xs = [pt[0] for pt in box]
            y_center = sum(ys) / len(ys)
            height = max(ys) - min(ys)
            items.append((y_center, min(xs), height, text))

        if not items:
            return ""

        items.sort(key=lambda it: (it[0], it[1]))
        median_h = sorted(i[2] for i in items)[len(items) // 2] or 10
        threshold = max(6.0, median_h * 0.6)

        lines: list[list[tuple[float, str]]] = []
        current: list[tuple[float, str]] = []
        current_y: float | None = None
        for y_center, x_left, _h, text in items:
            if current_y is None or abs(y_center - current_y) <= threshold:
                current.append((x_left, text))
                current_y = y_center if current_y is None else (current_y + y_center) / 2
            else:
                lines.append(current)
                current = [(x_left, text)]
                current_y = y_center
        if current:
            lines.append(current)

        rendered = []
        for line in lines:
            line.sort(key=lambda it: it[0])
            rendered.append(" ".join(text for _x, text in line))
        return "\n".join(rendered).strip()

    def _run(self, image: np.ndarray) -> str:
        engine = self._get_engine()
        try:
            result, _elapse = engine(image)
        except Exception as exc:
            raise OCRError(f"OCR inference failed: {exc}") from exc
        return self._results_to_text(result)

    def ocr_image(self, path: str) -> str:
        """OCR a single image file to text (multilingual Latin: German + English)."""
        try:
            image = self._load_rgb(path)
        except Exception as exc:
            raise OCRError(f"OCR failed for image: {exc}") from exc
        return self._run(image)

    def ocr_pdf(self, path: str) -> str:
        """Rasterize each PDF page and OCR it (for scanned / image-only PDFs).

        Uses PDFium (via ``pypdfium2``) — a self-contained, cross-platform wheel
        with no external binaries.
        """
        try:
            parts: list[str] = []
            pdf = pdfium.PdfDocument(path)
            try:
                total = len(pdf)
                for index in range(min(total, settings.ocr_max_pages)):
                    bitmap = pdf[index].render(scale=self.dpi / 72.0)
                    image = np.array(bitmap.to_pil().convert("RGB"))
                    page_text = self._run(image).strip()
                    if page_text:
                        parts.append(f"## Page {index + 1}\n\n{page_text}")
                if total > settings.ocr_max_pages:
                    logger.warning(
                        "PDF exceeds OCR page cap; truncating",
                        extra={"extra_fields": {"maxPages": settings.ocr_max_pages, "pages": total}},
                    )
            finally:
                pdf.close()
            return "\n\n".join(parts).strip()
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(f"OCR failed for PDF: {exc}") from exc
