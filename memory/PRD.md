# PRD — Enterprise Document Intelligence Platform

## Original problem statement
Scaffold an Enterprise Document Intelligence Platform (FastAPI) that converts
messy documents (PDF/DOCX) into structured JSON via a two-step pipeline:
Document → Markdown (MarkItDown) → Structured JSON (LLM). Requirements: sync +
async (202 + jobId + callbackUrl for Camunda 8) extraction, OIDC/JWT auth + RBAC,
correlationId tracing, S3 URI or multipart upload, pydantic-settings config,
SQLAlchemy job-state repository (PostgreSQL target), structured JSON logging,
RFC 7807 problem details, /health + /ready probes. Plus local dev instructions
(Windows + Mac) and a simple frontend demo page.

## User choices
- LLM: Gemini 3 Pro (real, via Emergent Universal Key; mock fallback available)
- Async job state: lightweight SQLite repository
- Auth: mock JWT/OIDC validation
- S3: mocked/stubbed
- Frontend: simple demo console included

## Architecture / tech stack
- Backend: FastAPI, pydantic-settings, SQLAlchemy(async)+aiosqlite, MarkItDown,
  tenacity (retries), python-jose (JWT), emergentintegrations (Gemini 3 Pro).
- Modular layout: main.py, core/{config,context,logging_config,security,problem_details},
  schemas/payloads.py, api/routes.py, services/{extraction_pipeline,job_repository}.py.
- Frontend: React (CRA) + Tailwind, "Swiss/high-contrast control-room" design,
  IBM Plex Mono / Chivo. data-testid on all controls.

## User personas
- Platform/integration engineer wiring document extraction into BPMN (Camunda 8).
- Reviewer/stakeholder watching a local demo of the extraction flow.

## Core requirements (static)
Sync + async extraction, RBAC, correlationId, RFC7807, health/ready, job tracking,
LLM JSON enforcement w/ retries, safe temp-file cleanup.

## What's been implemented (2026-01)
- [x] Full modular FastAPI backend per requested file layout
- [x] SYNC `/api/v1/extract/sync` + ASYNC `/api/v1/extract/async` (202 + jobId + callbackUrl)
- [x] Multipart upload AND JSON S3-URI body on both endpoints
- [x] Mock OIDC token mint `/api/v1/auth/token` + RBAC (extractor/admin)
- [x] correlationId middleware (X-Correlation-Id echo) + structured JSON logging
- [x] RFC 7807 problem+json handlers (401/403/404/400/413/422/500)
- [x] /health /ready (+ /api aliases) probes; readiness checks DB
- [x] MarkItDown conversion, optional chunking, tenacity exponential-backoff retries
- [x] Async job tracking in SQLite (PENDING/PROCESSING/COMPLETED/FAILED) + callback POST
- [x] React demo console (S3/upload, sync/async, JSON viewer, markdown, job polling)
- [x] Local dev guide (Windows + Mac) in /app/README.md
- [x] Tested: backend 10/10, frontend 100% (iteration_1.json)

## Backlog / future (P1/P2)
- P1: Real OIDC/JWKS validation; PostgreSQL via asyncpg
- P1: Real boto3 S3 ingestion (USE_MOCK_S3=false)
- P2: Per-tenant API keys / usage metering; webhook signing for callbacks
- P2: Streaming progress for large multi-chunk docs; OpenTelemetry traces
- P2: Tighten CORS to ingress origin; add `errors` array to all problem docs

## Next tasks
- Awaiting user feedback / which production hardening item to tackle first.

## Update (2026-01) — Pluggable, air-gapped LLM layer
- Removed emergentintegrations / Emergent Universal Key entirely (air-gapped purity).
- New `services/llm_providers.py`: LLMProvider base + GeminiProvider (google-genai),
  AnthropicProvider (anthropic), OpenAICompatibleProvider (openai SDK, configurable
  base_url) + `build_provider` factory selected by env `LLM_PROVIDER`.
- `openai_compatible` = air-gapped path (vLLM/Ollama/TGI/LiteLLM/corporate gateway);
  `gemini`/`anthropic` = native SDKs with user's own keys for local testing.
- Auto mock-fallback when the selected provider has no key/endpoint (or USE_MOCK_LLM=true).
- LLM 4xx auth/config errors now non-retryable (tenacity only retries network/5xx/429).
- Verified: air-gapped openai_compatible path end-to-end against a local stub (mock=false);
  regression suite 10/10 backend + 100% frontend in mock-fallback mode (iteration_2.json).
- Models: gemini default `gemini-3-pro-preview`, anthropic default `claude-sonnet-4-6`.

## Backlog additions
- P1: STRICT_LLM_PROVIDER flag to fail-fast instead of silent mock in production.
- P2: map upstream LLM failures to 502 problem+json; gate mock `_note` behind debug flag.

## Update (2026-01) — Code-quality review fixes (iteration_3: 10/10 backend, 100% frontend)
- SECURITY: JsonViewer.jsx no longer uses dangerouslySetInnerHTML — JSON is tokenized
  into escaped React nodes (XSS-safe; verified no <script> injection).
- Python possibly-unbound vars fixed via try/except/else in core/security.py
  (`claims`) and services/extraction_pipeline.py `_attempt` (`raw`).
- Refactor: App.js (was 405 lines / very high complexity) split into
  hooks/useDocIntel.js + components Header/InputPanel/OutputPanel; App.js now ~22 lines.
- Refactor: ExtractionPipeline.run() split into _to_markdown() + _extract_structured().
- Tests: extracted _assert_mock_result() and _poll_until_terminal() helpers to lower
  cyclomatic complexity of the sync/async tests. ESLint + ruff both clean.

## Update (2026-01) — "Omniparse" OCR + German ID extraction (iteration_4: backend 27/27, frontend 100%)
- Offline air-gapped OCR via Tesseract (deu+eng): new services/ocr_service.py (ocr_image, ocr_pdf rasterize w/ PyMuPDF, HEIC via pillow-heif, OCR_MAX_PAGES cap).
- Pipeline now routes by file type: images->OCR; PDF->text-layer-then-OCR-fallback (scanned/image-only PDFs now work, previously 500); office/text->MarkItDown.
- German ID (Personalausweis/Reisepass/Aufenthaltstitel) auto-detected -> dedicated schema (documentType, surname, givenNames, dateOfBirth, placeOfBirth, nationality, documentNumber, dateOfExpiry, address, mrz[]). German chars preserved.
- ExtractionResult gains ocrUsed + ocrEngine; UI shows an 'ocr-badge'. File dropzone accepts images+HEIC.
- Optional vision-LLM OCR (OCR_VISION, default false) via openai_compatible provider.complete_vision (untested w/o hosted vision model).
- Fixed: image-only/empty docs -> ConversionError mapped to 422 (was 500); IngestionError->400; LLM errors->502. Fixed Ausweis mock find() regex (non-capturing group; None guard).
- Deps added: pytesseract, pillow, pillow-heif, PyMuPDF (+ system tesseract-ocr, tesseract-ocr-deu/eng).

## Open gaps / backlog
- P2: verify OCR_VISION path against a real hosted vision model; HEIC fixture smoke test; >5MB 413 path.
- P2: STRICT_PROVIDER env (fail-fast vs silent mock); debug-gate mock _note; tighten CORS for prod.

## Fix (2026-01) — Windows PyMuPDF atexit crash
- Replaced PyMuPDF/fitz with pypdfium2 (PDFium) for scanned-PDF rasterization in services/ocr_service.py.
  Reason: PyMuPDF's warning-callback fires during interpreter shutdown on Windows (fz_set_warning_callback
  in _atexit), crashing on uvicorn --reload. pypdfium2 is a self-contained wheel, no external binaries,
  no atexit callback, Apache/BSD-licensed (vs PyMuPDF AGPL). requirements.txt: PyMuPDF -> pypdfium2>=4.30.
- Removed unused pdf_has_text_layer(); ocr_pdf now uses pdfium render(scale=dpi/72).to_pil(). Verified
  scanned.pdf/jpg/ausweis OCR all ocrUsed=true, no shutdown errors.

## Fix (2026-01) — blank/corrupt PDF regression from PDFium swap (iteration_5 -> all 27 tests pass)
- pypdfium2 is stricter than PyMuPDF: PdfDocument(blank.pdf) raises PdfiumError -> wrapped as OCRError -> was hitting generic 500.
- Fix: _to_markdown now catches OCRError and re-raises ConversionError(_NO_TEXT_MESSAGE) -> mapped to 422 (sync) and stored as friendly job error (async). Shared _NO_TEXT_MESSAGE constant.
- Verified: blank.pdf sync=422 'no extractable text', async=FAILED friendly msg; scanned.pdf/jpg/ausweis=200. Full pytest: 27/27 PASS. Frontend: 100%.
