"""
The core extraction pipeline.

Two logical paths stitched together behind a modular service layer:

    Text documents:  Document (PDF/DOCX/...) --MarkItDown--> Markdown --LLM--> JSON
    Images:          Image --Vision LLM (single shot)--> {transcription, structured JSON}
                     (falls back to PaddleOCR (PP-OCRv5) -> text LLM when no vision model)

Each responsibility lives in its own service so they can be tested and evolved
independently:

  * ``DocumentIngestionService``  - fetch from S3 (mocked) or persist an upload to
    a temp file, with guaranteed cleanup via a context manager.
  * ``MarkdownConversionService`` - thin wrapper over the ``MarkItDown`` library.
  * ``ChunkingService``           - optional splitting of very large Markdown.
  * ``LLMExtractionService``      - prompt engineering + JSON enforcement, with
    ``tenacity`` exponential-backoff retries around the external API call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from markitdown import MarkItDown
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from services.ocr_service import OCRError, OCRService

logger = logging.getLogger("pipeline")
settings = get_settings()

# Shared message for documents that yield no usable text (empty/corrupt/blank scan).
_NO_TEXT_MESSAGE = (
    "The document produced no extractable text. It may be empty, corrupted, "
    "password-protected, or a blank/low-quality scan. Try a higher-resolution "
    "scan or a text-based document."
)


# ---------------------------------------------------------------------------
# Domain-specific exceptions
# ---------------------------------------------------------------------------
class IngestionError(Exception):
    """Raised when the source document cannot be fetched/materialized."""


class ConversionError(Exception):
    """Raised when MarkItDown fails to convert the document to Markdown."""


class LLMExtractionError(Exception):
    """Retryable error raised when the LLM call or JSON parsing fails."""


class LLMFatalError(Exception):
    """Non-retryable LLM error (auth / bad-request / other 4xx config issues)."""


def _http_status(exc: Exception) -> Optional[int]:
    """Best-effort extraction of an HTTP status code from a vendor SDK error."""
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


# A small in-memory stand-in for an S3 bucket used by the demo. Any unknown URI
# falls back to ``_DEFAULT_MOCK_DOC`` so reviewers can pass arbitrary s3:// URIs.
_DEFAULT_MOCK_DOC = """# ACME Corporation - Commercial Invoice

Invoice Number: INV-2026-00427
Invoice Date: 2026-01-12
Due Date: 2026-02-11
Customer: Globex Industries Ltd.
Billing Address: 42 Industrial Way, Springfield

## Line Items

| Description            | Qty | Unit Price | Amount   |
|------------------------|-----|------------|----------|
| Enterprise License     | 3   | 1200.00    | 3600.00  |
| Premium Support (12mo) | 1   | 4800.00    | 4800.00  |
| Onboarding Services    | 1   | 1500.00    | 1500.00  |

Subtotal: 9900.00
Tax (8%): 792.00
Total Amount: 10692.00 USD

Payment Terms: Net 30
Notes: Please reference the invoice number on all remittances.
"""

_MOCK_S3_BUCKET: dict[str, str] = {
    "s3://demo-bucket/contracts/invoice-001.pdf": _DEFAULT_MOCK_DOC,
}


# ---------------------------------------------------------------------------
# 1. Ingestion
# ---------------------------------------------------------------------------
class DocumentIngestionService:
    """Materialize a source document onto local disk for conversion.

    The ``materialize`` context manager guarantees the temp file is removed even
    if conversion raises, satisfying the requirement for explicit cleanup.
    """

    @contextmanager
    def materialize(
        self,
        *,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        s3_uri: Optional[str] = None,
    ) -> Iterator[tuple[str, str]]:
        tmp_path: Optional[str] = None
        try:
            if s3_uri:
                data, resolved_name = self._fetch_from_s3(s3_uri)
            elif file_bytes is not None:
                data, resolved_name = file_bytes, (filename or "upload.bin")
            else:
                raise IngestionError("No document source provided (need file or s3Uri).")

            suffix = os.path.splitext(resolved_name)[1] or ".txt"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="docintel_")
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)

            logger.info(
                "Document materialized to temp storage",
                extra={"extra_fields": {"tempPath": tmp_path, "filename": resolved_name}},
            )
            yield tmp_path, resolved_name
        finally:
            # Explicit, always-runs cleanup of local temp storage.
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    logger.info(
                        "Temp file cleaned up",
                        extra={"extra_fields": {"tempPath": tmp_path}},
                    )
                except OSError:  # pragma: no cover - best-effort cleanup
                    logger.warning(
                        "Failed to clean up temp file",
                        extra={"extra_fields": {"tempPath": tmp_path}},
                    )

    def _fetch_from_s3(self, s3_uri: str) -> tuple[bytes, str]:
        """Fetch object bytes from S3. Mocked unless ``use_mock_s3`` is False."""
        if settings.use_mock_s3:
            content = _MOCK_S3_BUCKET.get(s3_uri, _DEFAULT_MOCK_DOC)
            key = s3_uri.rstrip("/").split("/")[-1] or "object"
            # Mock content is Markdown-ish text; give it a .txt suffix so
            # MarkItDown's plain-text converter passes it through verbatim.
            name = key if key.endswith((".txt", ".md")) else f"{key}.txt"
            logger.info(
                "Fetched object from (mock) S3",
                extra={"extra_fields": {"s3Uri": s3_uri, "bytes": len(content)}},
            )
            return content.encode("utf-8"), name

        # Real S3 path (intentionally not wired up for the offline demo).
        raise IngestionError(
            "Real S3 access is disabled. Set USE_MOCK_S3=false and configure "
            "boto3 credentials to enable live fetching."
        )


# ---------------------------------------------------------------------------
# 2. Markdown conversion
# ---------------------------------------------------------------------------
class MarkdownConversionService:
    """Convert an on-disk document to Markdown using MarkItDown."""

    def __init__(self) -> None:
        self._markitdown = MarkItDown(enable_plugins=False)

    def convert(self, path: str) -> str:
        try:
            result = self._markitdown.convert(path)
            return (result.text_content or "").strip()
        except Exception as exc:  # MarkItDown raises a variety of types
            raise ConversionError(f"Failed to convert document to Markdown: {exc}") from exc


# ---------------------------------------------------------------------------
# 3. Chunking (optional middleware)
# ---------------------------------------------------------------------------
# Heading-aware Markdown splitter — keeps each chunk under ``chunk_size`` chars
# while preferring to cut on `##`/`#` boundaries (so a chunk is a self-contained
# section the LLM can reason about) and falling back to paragraph splits when
# a single section is itself too large.
_HEADING_RE = re.compile(r"^(#{1,3})\s+.*$", re.MULTILINE)


class ChunkingService:
    """Split large Markdown into smaller, structure-aware chunks with overlap.

    Why it matters:
      * Keeps every LLM call under the model's context window deterministically.
      * Cutting on headings (not arbitrary char offsets) preserves the local
        context the model needs to extract a table or invoice row correctly.
      * A small character overlap between consecutive chunks prevents a single
        line item or paragraph from being silently truncated at a boundary.
      * Each chunk is sent in its OWN LLM call, so input tokens scale O(N) with
        document length (no quadratic re-prompting) and partial failures are
        retried independently.
    """

    def chunk(self, markdown: str) -> list[str]:
        """Return one or more Markdown chunks suitable for downstream LLM calls."""
        if len(markdown) <= settings.chunk_char_threshold:
            return [markdown]

        max_chars = settings.chunk_size
        overlap = max(0, min(settings.chunk_overlap, max_chars // 4))

        # First, split the document into heading-bounded SECTIONS so we don't
        # cut tables/lists in half. Falls back to one big section when there
        # are no headings.
        sections = self._split_on_headings(markdown)

        chunks: list[str] = []
        current = ""
        for section in sections:
            # A single section can itself exceed `chunk_size` (e.g. a giant
            # table). In that case split it again on paragraph boundaries.
            pieces = self._split_section(section, max_chars)
            for piece in pieces:
                if current and len(current) + len(piece) + 2 > max_chars:
                    chunks.append(current)
                    # Carry the tail of the previous chunk into the next so the
                    # LLM doesn't lose context at a boundary.
                    tail = current[-overlap:] if overlap else ""
                    current = (tail + "\n\n" + piece).strip() if tail else piece
                else:
                    current = f"{current}\n\n{piece}" if current else piece
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _split_on_headings(markdown: str) -> list[str]:
        """Split a Markdown doc into heading-bounded sections (preserving the heading)."""
        starts = [m.start() for m in _HEADING_RE.finditer(markdown)]
        if not starts:
            return [markdown]
        # Include any preamble before the first heading.
        if starts[0] > 0:
            starts = [0] + starts
        starts.append(len(markdown))
        sections: list[str] = []
        for i in range(len(starts) - 1):
            section = markdown[starts[i] : starts[i + 1]].strip()
            if section:
                sections.append(section)
        return sections

    @staticmethod
    def _split_section(section: str, max_chars: int) -> list[str]:
        """Break an oversized section on blank-line paragraph boundaries."""
        if len(section) <= max_chars:
            return [section]
        pieces: list[str] = []
        current = ""
        for paragraph in section.split("\n\n"):
            if current and len(current) + len(paragraph) + 2 > max_chars:
                pieces.append(current)
                current = paragraph
            else:
                current = f"{current}\n\n{paragraph}" if current else paragraph
        if current:
            pieces.append(current)
        return pieces


# Approximate tokens-per-character ratio used for the observability fields.
# (Empirical: tiktoken/cl100k averages ~3.6-4.2 chars per token across English
# + German business docs. We use 4 for a conservative, predictable estimate.)
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN if text else 0


def _merge_chunked_structured(partials: list[Any]) -> Any:
    """Merge per-chunk JSON outputs into a single structured object.

    Strategy: when every partial is a dict, merge keys; same-name lists are
    concatenated, same-name dicts deep-merged, scalars are kept from the first
    chunk that supplied a non-empty value (later chunks won't override header
    fields like invoiceNumber). When merging is impossible we fall back to the
    legacy ``{"documentChunks": [...]}`` shape so nothing is lost.
    """
    if not partials:
        return {}
    if len(partials) == 1:
        return partials[0]
    if not all(isinstance(p, dict) for p in partials):
        return {"documentChunks": partials}

    merged: dict[str, Any] = {}
    for partial in partials:
        for key, value in partial.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
                continue
            existing = merged[key]
            if isinstance(existing, list) and isinstance(value, list):
                existing.extend(value)
            elif isinstance(existing, dict) and isinstance(value, dict):
                existing.update({k: v for k, v in value.items() if k not in existing})
            # else: keep the first non-empty value, ignore later overrides.
    return merged


# ---------------------------------------------------------------------------
# 4. LLM extraction
# ---------------------------------------------------------------------------
_ID_SCHEMA_HINT = (
    "If the document is a German or EU identity document or licence "
    "(Personalausweis, Reisepass, Aufenthaltstitel, Führerschein / driving licence), "
    "extract these fields: documentType, country, surname, givenNames, nameAtBirth, "
    "dateOfBirth, placeOfBirth, nationality, documentNumber, dateOfIssue, dateOfExpiry, "
    "issuingAuthority, address, accessNumber (CAN, if present), categories (driving "
    "licence classes, if present), and mrz (array of machine-readable-zone lines if "
    "present). Use null for fields that are not visible. "
    "ALWAYS preserve German characters (ä, ö, ü, ß) exactly. "
)

_SYSTEM_PROMPT = (
    "You are an enterprise document extraction engine. You convert Markdown/OCR "
    "text of business and identity documents into clean, structured JSON. "
    "Infer a sensible schema from the content (document type, header fields, "
    "tables/line items, totals, dates, parties). "
    + _ID_SCHEMA_HINT
    + "Respond with ONLY a single valid JSON object and no surrounding prose or code fences."
)

# Single-shot vision prompt: transcription + structured JSON in one call. This is
# far more accurate on ID cards / licences than plain OCR because the model reads
# the image directly (no OCR noise from guilloche security backgrounds).
_VISION_SYSTEM_PROMPT = (
    "You are an enterprise document extraction engine with vision capabilities. "
    "You read document images (identity documents, driver's licences, invoices, "
    "receipts, forms, contracts, brochures) and produce BOTH a faithful "
    "transcription and structured JSON. Infer a sensible schema from the content. "
    + _ID_SCHEMA_HINT
    + "Respond with ONLY one valid JSON object of the exact shape "
    '{"transcription": "<full plain-text/Markdown transcription of all visible text>", '
    '"structured": { ...extracted fields... }} '
    "with no surrounding prose or code fences."
)

# Markers used to auto-detect German identity documents in mock mode.
_ID_MARKERS = (
    "PERSONALAUSWEIS",
    "BUNDESREPUBLIK DEUTSCHLAND",
    "REISEPASS",
    "AUFENTHALTSTITEL",
    "IDENTITY CARD",
    "IDENTITÄTSKARTE",
    "FÜHRERSCHEIN",
    "DRIVING LICENCE",
)

# Lines that are field LABELS on German/EU identity documents. On the physical
# card the VALUE is printed on the line *below* the (often trilingual) label,
# so "Label: value" same-line matching alone produces wrong/partial data.
_LABEL_LINE = re.compile(
    r"(?:Name\s*/|Surname|/\s*Nom|Geburtsname|Name at birth|Vornamen|Given names?|"
    r"Pr[ée]noms|Geburtstag|Geburtsdatum|Date of birth|de naissance|Geburtsort|"
    r"Place of birth|Staatsangeh|Nationalit|G[üu]ltig bis|Date of expiry|"
    r"expiration|Unterschrift|Signature|Anschrift|Address|Wohnort|"
    r"IDENTITY CARD|CARTE D|PERSONALAUSWEIS|BUNDESREPUBLIK|FEDERAL REPUBLIC|"
    r"REPUBLIQUE|F[ÜU]HRERSCHEIN|DRIVING LICENCE)",
    re.IGNORECASE,
)


class LLMExtractionService:
    """Turn Markdown / images into structured JSON, with retries and a mock fallback.

    The actual model call is delegated to a pluggable provider (Emergent /
    Gemini / Anthropic / OpenAI-compatible). If no provider can be built (e.g. a
    cloud key is missing, or USE_MOCK_LLM=true) we fall back to a deterministic
    mock so the platform is always demoable.
    """

    def __init__(self) -> None:
        from services.llm_providers import build_provider

        self.provider = None if settings.use_mock_llm else build_provider(settings)
        self.use_mock: bool = self.provider is None

    @property
    def model_name(self) -> str:
        return "mock" if self.use_mock else self.provider.model_name

    @property
    def vision_available(self) -> bool:
        """True when a vision-capable provider is configured."""
        return (not self.use_mock) and getattr(self.provider, "supports_vision", False)

    # ------------------------------------------------------------------
    # Vision path: image -> {transcription, structured} in a single call
    # ------------------------------------------------------------------
    async def vision_extract(
        self, image_path: str, instructions: Optional[str]
    ) -> tuple[str, Any]:
        """Read a document image with the vision model; return ``(markdown, structured)``."""
        image_b64, mime = OCRService.image_to_b64(image_path)
        guidance = (
            f"\n\nExtraction instructions / target schema for 'structured':\n{instructions}"
            if instructions
            else ""
        )
        prompt = (
            "Transcribe this document image and extract its structured data "
            "into the required JSON shape." + guidance
        )
        parsed = await self._attempt_vision(prompt, image_b64, mime)

        if isinstance(parsed, dict) and "structured" in parsed:
            markdown = str(parsed.get("transcription") or "").strip()
            structured = parsed.get("structured")
        else:
            # Model ignored the envelope -> treat the whole object as structured.
            markdown, structured = "", parsed
        if not markdown:
            markdown = json.dumps(structured, ensure_ascii=False, indent=2)
        return markdown, structured

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(LLMExtractionError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _attempt_vision(self, prompt: str, image_b64: str, mime: str) -> Any:
        try:
            raw = await self.provider.complete_vision(
                _VISION_SYSTEM_PROMPT, prompt, image_b64, mime
            )
        except Exception as exc:
            status = _http_status(exc)
            if status is not None and 400 <= status < 500 and status != 429:
                raise LLMFatalError(f"Vision LLM request rejected ({status}): {exc}") from exc
            raise LLMExtractionError(f"Vision LLM call failed: {exc}") from exc
        else:
            return self._parse_json(raw)

    # Back-compat helper: transcription-only vision call.
    async def vision_transcribe(self, image_path: str) -> str:
        """Use the provider's vision model to transcribe an image to Markdown."""
        image_b64, mime = OCRService.image_to_b64(image_path)
        system = (
            "You are an OCR engine. Transcribe the document image to clean Markdown, "
            "preserving ALL text exactly, including German characters (ä, ö, ü, ß) and "
            "any machine-readable-zone (MRZ) lines. Output only the transcription."
        )
        raw = await self.provider.complete_vision(
            system, "Transcribe this document to Markdown.", image_b64, mime
        )
        return (raw or "").strip()

    # ------------------------------------------------------------------
    # Text path: Markdown -> structured JSON
    # ------------------------------------------------------------------
    async def extract(
        self, markdown: str, instructions: Optional[str], session_id: str
    ) -> tuple[Any, bool]:
        """Return ``(structured_json, used_mock)``."""
        if self.use_mock:
            return self._mock_extract(markdown), True

        prompt = self._build_prompt(markdown, instructions)
        structured = await self._attempt(prompt)
        return structured, False

    def _build_prompt(self, markdown: str, instructions: Optional[str]) -> str:
        guidance = (
            f"\n\nExtraction instructions / target schema:\n{instructions}\n"
            if instructions
            else ""
        )
        return (
            "Extract the following document into structured JSON."
            f"{guidance}\n\n--- BEGIN MARKDOWN ---\n{markdown}\n--- END MARKDOWN ---"
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(LLMExtractionError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _attempt(self, prompt: str) -> Any:
        """Single provider call + JSON parse; raises LLMExtractionError to retry."""
        try:
            raw = await self.provider.complete(_SYSTEM_PROMPT, prompt)
        except Exception as exc:
            status = _http_status(exc)
            # 4xx (except 429 rate-limit) are auth/config errors -> do NOT retry.
            if status is not None and 400 <= status < 500 and status != 429:
                raise LLMFatalError(f"LLM request rejected ({status}): {exc}") from exc
            # Network / 5xx / rate-limit errors are retryable.
            raise LLMExtractionError(f"LLM call failed: {exc}") from exc
        else:
            # `raw` is guaranteed bound here (the except branch always raises).
            return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> Any:
        """Extract a JSON document from the model's raw text response."""
        text = (raw or "").strip()

        # Strip ```json ... ``` fences if the model added them.
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Last resort: grab the outermost {...} or [...] block.
            match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError as exc:
                    raise LLMExtractionError(
                        f"Model returned non-JSON content: {exc}"
                    ) from exc
            raise LLMExtractionError("Model returned no parseable JSON content.")

    # ------------------------------------------------------------------
    # Deterministic mock extractors (no key / USE_MOCK_LLM=true)
    # ------------------------------------------------------------------
    @staticmethod
    def _mock_extract(markdown: str) -> dict[str, Any]:
        """Deterministic, dependency-free structured extraction for demos."""
        upper = markdown.upper()
        # Auto-detect German identity documents.
        if any(m in upper for m in _ID_MARKERS) or "<<" in markdown:
            return LLMExtractionService._mock_extract_id(markdown)

        lines = markdown.splitlines()
        title = next(
            (re.sub(r"^#+\s*", "", ln).strip() for ln in lines if ln.startswith("#")),
            "Untitled Document",
        )
        headings = [
            re.sub(r"^#+\s*", "", ln).strip() for ln in lines if ln.lstrip().startswith("#")
        ]

        fields: dict[str, str] = {}
        for ln in lines:
            kv = re.match(r"^\*?\*?\s*([A-Za-zÄÖÜäöüß][\wÄÖÜäöüß ./-]{1,40})\s*[:：]\s*(.+?)\*?\*?$", ln)
            if kv:
                fields[kv.group(1).strip()] = kv.group(2).strip()

        body_text = " ".join(
            ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith(("#", "|"))
        )

        return {
            "documentType": "generic",
            "title": title,
            "sections": headings[:25],
            "fields": fields,
            "summary": body_text[:300],
            "_note": "MOCK extraction (LLM mock mode / no key configured).",
        }

    @staticmethod
    def _mock_extract_id(text: str) -> dict[str, Any]:
        """Heuristic mock extraction for German identity documents (Ausweis).

        German/EU cards print trilingual LABELS (e.g. "Name/Surname/Nom") with
        the VALUE on the line(s) BELOW, so after a same-line match fails or
        returns more label text, we take the next non-label line as the value.
        """
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        upper = text.upper()

        def is_label(ln: str) -> bool:
            return bool(_LABEL_LINE.search(ln))

        def find(pattern: str) -> Optional[str]:
            for index, ln in enumerate(lines):
                m = re.search(r"(?:" + pattern + r")\s*[:\-]?\s*(.*)", ln, re.IGNORECASE)
                if m is None:
                    continue
                value = (m.group(1) or "").strip(" :/-")
                # Same-line remainder that is just more label text (e.g.
                # "Surname/Nom") -> the real value is on a following line.
                if value and not is_label(value):
                    return value
                for nxt in lines[index + 1 : index + 4]:
                    if nxt and not is_label(nxt) and len(nxt) > 1:
                        return nxt
                return None
            return None

        # MRZ lines: long runs of uppercase letters/digits/filler '<'.
        mrz = [ln for ln in lines if ln.count("<") >= 2 or re.fullmatch(r"[A-Z0-9<]{18,}", ln.replace(" ", ""))]

        if "PERSONALAUSWEIS" in upper:
            doc_type = "Personalausweis"
        elif "REISEPASS" in upper:
            doc_type = "Reisepass"
        elif "AUFENTHALTSTITEL" in upper:
            doc_type = "Aufenthaltstitel"
        elif "FÜHRERSCHEIN" in upper or "DRIVING LICENCE" in upper:
            doc_type = "Führerschein"
        else:
            doc_type = "Identity Document"

        return {
            "documentType": doc_type,
            "country": "DEU" if "DEUTSCHLAND" in upper else None,
            "surname": find(r"Name|Surname|Familienname"),
            "givenNames": find(r"Vornamen|Given names?|Vorname"),
            "dateOfBirth": find(r"Geburtstag|Geburtsdatum|Date of birth"),
            "placeOfBirth": find(r"Geburtsort|Place of birth"),
            "nationality": find(r"Staatsangeh\w*|Nationality"),
            "documentNumber": find(r"Ausweisnummer|Pass\s*No|Document\s*No|Nr|No"),
            "dateOfExpiry": find(r"G[üu]ltig bis|Date of expiry"),
            "address": find(r"Anschrift|Address|Wohnort"),
            "mrz": mrz[-3:] if mrz else [],
            "_note": "MOCK Ausweis extraction (heuristic; use a real/vision LLM for production accuracy).",
        }


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------
class ExtractionPipeline:
    """Compose the services into a single end-to-end extraction run."""

    def __init__(self) -> None:
        self.ingestion = DocumentIngestionService()
        self.converter = MarkdownConversionService()
        self.chunker = ChunkingService()
        self.extractor = LLMExtractionService()
        self.ocr = OCRService()

    async def _convert_path(self, path: str, name: str) -> tuple[str, bool]:
        """Convert a local file to Markdown, choosing the right engine by type.

        Returns ``(markdown, ocr_used)``. Images go to PaddleOCR (the vision
        path is handled earlier in ``run``); PDFs use MarkItDown's text layer and
        fall back to OCR when the page has none (scanned/image-only PDFs);
        everything else uses MarkItDown.
        """
        ext = os.path.splitext(name)[1].lower()

        # Images -> offline PaddleOCR (PP-OCRv5, ONNXRuntime).
        if self.ocr.is_image(ext):
            if not settings.ocr_enabled:
                raise ConversionError("OCR is disabled; cannot read image files.")
            return await asyncio.to_thread(self.ocr.ocr_image, path), True

        # PDFs -> try the text layer first, OCR-fallback for scanned PDFs.
        if ext == ".pdf":
            markdown = await asyncio.to_thread(self.converter.convert, path)
            if markdown.strip():
                return markdown, False
            if settings.ocr_enabled:
                logger.info("PDF has no text layer -> OCR fallback")
                return await asyncio.to_thread(self.ocr.ocr_pdf, path), True
            return markdown, False

        # Office / text / html / csv / json -> MarkItDown.
        return await asyncio.to_thread(self.converter.convert, path), False

    async def _extract_structured(
        self, chunks: list[str], instructions: Optional[str], correlation_id: str
    ) -> tuple[Any, bool]:
        """Run text LLM extraction over one or more chunks.

        Multi-chunk runs are merged into a single coherent object (see
        ``_merge_chunked_structured``). Per-chunk failures still surface via
        the retry/raise behaviour of ``LLMExtractionService.extract``.
        """
        if len(chunks) == 1:
            return await self.extractor.extract(chunks[0], instructions, correlation_id)

        partials: list[Any] = []
        used_mock = False
        for index, chunk in enumerate(chunks):
            partial, used_mock = await self.extractor.extract(
                chunk, instructions, f"{correlation_id}-{index}"
            )
            partials.append(partial)
        return _merge_chunked_structured(partials), used_mock

    async def run(
        self,
        *,
        correlation_id: str,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        s3_uri: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        source_type = "s3" if s3_uri else "upload"

        markdown: Optional[str] = None
        structured: Any = None
        used_mock = False
        ocr_used = False
        ocr_engine: Optional[str] = None

        with self.ingestion.materialize(
            file_bytes=file_bytes, filename=filename, s3_uri=s3_uri
        ) as (path, resolved_name):
            ext = os.path.splitext(resolved_name)[1].lower()

            # ---- Preferred path for images: single-shot vision extraction ----
            if (
                self.ocr.is_image(ext)
                and settings.ocr_vision
                and self.extractor.vision_available
            ):
                try:
                    markdown, structured = await self.extractor.vision_extract(
                        path, instructions
                    )
                    ocr_used = True
                    ocr_engine = f"vision:{self.extractor.model_name}"
                except (LLMExtractionError, LLMFatalError) as exc:
                    logger.warning(
                        "Vision extraction failed -> PaddleOCR fallback",
                        extra={"extra_fields": {"error": str(exc)}},
                    )
                    structured = None

            # ---- Classic path: convert to Markdown (MarkItDown / PaddleOCR) ----
            if structured is None:
                try:
                    markdown, ocr_used = await self._convert_path(path, resolved_name)
                except OCRError as exc:
                    # Empty/corrupt/blank scans surface as OCR failures -> treat as
                    # an unprocessable document (422), not an internal error (500).
                    raise ConversionError(_NO_TEXT_MESSAGE) from exc
                if ocr_used:
                    ocr_engine = self.ocr.engine_name()

        if structured is None:
            if not markdown:
                raise ConversionError(_NO_TEXT_MESSAGE)
            chunks = self.chunker.chunk(markdown)  # optional chunking
            structured, used_mock = await self._extract_structured(
                chunks, instructions, correlation_id
            )
        else:
            chunks = [markdown]

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # ----- Token / cost observability -------------------------------------
        # We can't ask the model for an exact token count without paying for an
        # extra round-trip; instead we report a deterministic *estimate* based
        # on the well-known ~4-chars-per-token heuristic (cl100k average). Two
        # numbers are returned:
        #
        #   tokensEstimate   = input + output tokens actually sent/received.
        #   tokensSavedVsRaw = what you would have paid if you'd sent the raw
        #                      document straight to a vision LLM (a typical
        #                      page of a scanned PDF costs ~1100 input tokens
        #                      JUST for the image, regardless of length).
        #
        # The savings come from the deterministic MarkItDown / PaddleOCR step,
        # which converts the document for FREE before the LLM ever sees it.
        structured_str = (
            json.dumps(structured, ensure_ascii=False) if structured is not None else ""
        )
        input_tokens = _estimate_tokens(markdown)
        output_tokens = _estimate_tokens(structured_str)
        # Conservative baseline: vision-LLM-per-page is ~1100 tokens of input
        # per page (1024px tile) + the same JSON output. We approximate "pages"
        # from char count (3500 chars ≈ 1 page) with a 1-page minimum so even
        # tiny docs reflect the vision-API floor.
        estimated_pages = max(1, len(markdown) // 3500)
        raw_input_tokens = 1100 * estimated_pages
        tokens_saved = max(0, raw_input_tokens - input_tokens)

        logger.info(
            "Extraction pipeline completed",
            extra={
                "extra_fields": {
                    "sourceType": source_type,
                    "chunkCount": len(chunks),
                    "mock": used_mock,
                    "ocrUsed": ocr_used,
                    "ocrEngine": ocr_engine,
                    "processingMs": elapsed_ms,
                    "tokensEstimate": input_tokens + output_tokens,
                    "tokensSavedVsRaw": tokens_saved,
                }
            },
        )

        return {
            "correlationId": correlation_id,
            "sourceType": source_type,
            "filename": resolved_name,
            "markdown": markdown,
            "markdownChars": len(markdown),
            "chunked": len(chunks) > 1,
            "chunkCount": len(chunks),
            "structured": structured,
            "model": self.extractor.model_name,
            "mock": used_mock,
            "ocrUsed": ocr_used,
            "ocrEngine": ocr_engine,
            "processingMs": elapsed_ms,
            "tokensEstimate": input_tokens + output_tokens,
            "tokensSavedVsRaw": tokens_saved,
        }


# A single shared pipeline instance for the application.
pipeline = ExtractionPipeline()
