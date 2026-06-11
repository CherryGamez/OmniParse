"""
Offline OCR service (air-gapped).

Uses Tesseract (via ``pytesseract``) with German + English language data to turn
images and image-only/scanned PDFs into text. PDFs are rasterized page-by-page
with PDFium (``pypdfium2``) — a self-contained wheel with no external binaries
and no interpreter-shutdown issues (reliable on Windows). HEIC/HEIF phone photos
are supported via ``pillow-heif``; AVIF via ``pillow-avif-plugin``. Everything
runs locally — no cloud OCR.

Images are pre-processed before Tesseract (grayscale -> upscale small scans ->
autocontrast) which significantly improves recognition on ID cards and noisy
phone photos with guilloche/security-pattern backgrounds.
"""
from __future__ import annotations

import base64
import io
import logging
import os

import pypdfium2 as pdfium
import pytesseract
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


class OCRService:
    """Tesseract-backed OCR for images and scanned PDFs."""

    def __init__(self) -> None:
        self.lang = settings.ocr_languages  # e.g. "deu+eng"
        self.dpi = settings.ocr_dpi

    @staticmethod
    def is_image(ext: str) -> bool:
        return ext.lower() in IMAGE_EXTS

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
    # Tesseract path
    # ------------------------------------------------------------------
    @staticmethod
    def _preprocess(img: Image.Image) -> Image.Image:
        """Grayscale + upscale small scans + autocontrast for better OCR on IDs."""
        img = img.convert("L")
        width, height = img.size
        longest = max(width, height)
        if longest and longest < 1600:
            scale = 1600 / longest
            img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
        return ImageOps.autocontrast(img)

    def ocr_image(self, path: str) -> str:
        """OCR a single image file to text (German + English)."""
        try:
            with Image.open(path) as img:
                text = pytesseract.image_to_string(self._preprocess(img), lang=self.lang)
            return text.strip()
        except Exception as exc:
            raise OCRError(f"OCR failed for image: {exc}") from exc

    def ocr_pdf(self, path: str) -> str:
        """Rasterize each PDF page and OCR it (for scanned / image-only PDFs).

        Uses PDFium (via ``pypdfium2``) — a self-contained, cross-platform wheel
        with no external binaries and none of PyMuPDF's interpreter-shutdown
        callback issues, which makes it reliable on Windows + ``uvicorn --reload``.
        """
        try:
            parts: list[str] = []
            pdf = pdfium.PdfDocument(path)
            try:
                total = len(pdf)
                for index in range(min(total, settings.ocr_max_pages)):
                    bitmap = pdf[index].render(scale=self.dpi / 72.0)
                    img = bitmap.to_pil().convert("RGB")
                    page_text = pytesseract.image_to_string(img, lang=self.lang).strip()
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
        except Exception as exc:
            raise OCRError(f"OCR failed for PDF: {exc}") from exc
