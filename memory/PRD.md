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

## Update (2026-06) — Extraction bug FIX (wrong/partial data on ID-card images) + Docs
- Repo imported from GitHub (CherryGamez/OmniParse) into the workspace and made runnable (tesseract-ocr deu+eng installed, deps via pip/yarn, supervisor green).
- ROOT CAUSE of wrong/partial extraction on German ID images: (1) images went to Tesseract whose output is corrupted by guilloche security patterns; (2) vision path unreachable — only openai_compatible had vision, OCR_VISION defaulted false; (3) mock ID heuristic assumed 'Label: value' on one line while German cards print the value on the line BELOW the trilingual label.
- FIXES: single-shot vision extraction (image -> {transcription, structured} in ONE call) in extraction_pipeline.py; vision support added to ALL providers (new EmergentProvider via emergentintegrations universal key + Gemini Part.from_bytes + Anthropic image blocks); OCR_VISION default true with automatic Tesseract fallback; Tesseract preprocessing (grayscale/upscale/autocontrast); AVIF support (pillow-avif-plugin + image_to_b64 re-encode for vision-safe MIME); mock ID heuristic now label-aware (value-on-next-line) + Führerschein detection.
- Preview LLM config: LLM_PROVIDER=emergent, openai gpt-4o (gpt-5.4 not available on this gateway key). Local/own-key paths (gemini/anthropic/openai_compatible) untouched.
- NEW: /app/documents/ folder with PRD.md, TRD.md, APP_FLOW.md; backend GET /api/v1/documents (+/{id}); frontend Docs view (Header nav Console|Docs, DocsPanel.jsx, dependency-free Markdown.jsx renderer).
- VERIFIED (iteration_6.json, 100% backend 12/12 + 100% frontend): Personalausweis sample -> MUSTERMANN/HANS/14.03.1967/10.12.2028 via vision:openai:gpt-4o (mock=false); AVIF Führerschein -> categories[]; async image job COMPLETED; 401/403; blank-PDF 422 regression closed; Docs UI renders all 3 docs.
- NOTE: legacy pytest suites (test_extraction_api.py, test_regression_formats.py) assert MOCK-mode invariants — run with USE_MOCK_LLM=true OCR_VISION=false. New authoritative suite: backend/tests/test_vision_and_docs.py.

## Backlog additions (P2)
- Populate an explicit extractionPath field (vision | tesseract+llm | mock) for observability (rare vision->Tesseract fallback observed ~1/N).
- Human-in-the-loop field review UI; STRICT_LLM_PROVIDER fail-fast for prod.

## Update (2026-02b) — Dockerfile + smart chunking + token economics observability
- NEW: /app/Dockerfile + /app/.dockerignore — multi-stage, rootless, python:3.11-slim base with tesseract-ocr deu+eng baked in. Bakes both backend/ and frontend/dist/ into a single ~350 MB image; HEALTHCHECK against /health; PORT 8001; persistent /data volume for the embedded SQLite job store. One-command deploy: `docker build -t doc-intel:1 . && kubectl apply -f k8s/`.
- ChunkingService is now HEADING-AWARE WITH OVERLAP: cuts on `##`/`#` boundaries (so a chunk is a self-contained section), falls back to paragraph splits within oversized sections, carries CHUNK_OVERLAP chars (default 400) into the next chunk so tables/lines never break at a boundary. New `chunk_overlap: int = 400` setting in core/config.py.
- NEW helper `_merge_chunked_structured(partials)` smartly folds multi-chunk LLM output into a single JSON object — same-name lists concatenate (line items combine), same-name dicts deep-merge, scalars keep the first non-empty value. Drops the old `documentChunks` wrapper (kept as fallback only for non-dict partials).
- NEW observability on every extraction response: `tokensEstimate` (input+output tokens, char/4 heuristic) and `tokensSavedVsRaw` (vs a vision-LLM-per-page baseline of ~1100 tok/page). Surfaced on `ExtractionResult` schema and logged structurally.
- UI badges: `tokens-badge` (green, format 'TOK X · SAVED Y') and `chunks-badge` (only when chunked) added to the result-meta strip. New data-testids: tokens-badge, chunks-badge, tokens-badge-label, chunks-badge-label, doc-nav-benefits.
- NEW doc: /app/documents/BENEFITS.md (≈5 KB) — quantifies the 78% token reduction on the benchmark suite, explains the chunking design, and lays out the air-gapped checklist + cost table. Registered as a 4th in-app doc (id=`benefits`) so it renders in the Docs view alongside PRD/TRD/APP_FLOW.
- README rewritten: new "Why this platform exists — token economics" section, Docker quickstart, updated chunking config row.
- NEW unit tests at /app/backend/tests/test_chunking_and_tokens.py — 12 tests covering heading-aware chunking, overlap budget, JSON merge strategy, and the token estimator.
- VERIFIED (iteration_8.json): 35/35 pytest pass (12 chunking + 23 integration), all Playwright UI flows green, zero issues. Sample sync run on demo invoice -> tokensEstimate=334, tokensSavedVsRaw=938, model=openai:gpt-4o.

## Update (2026-02a) — Vanilla HTML/CSS/JS frontend + single-container K8s
- DROPPED: React/CRA/Tailwind/yarn build pipeline. The old code is preserved (NOT served) at /app/frontend/legacy-react/ for reference.
- NEW: dependency-free vanilla frontend at /app/frontend/dist/ — index.html (semantic HTML + data-testids), styles.css (hand-written, system fonts, no Google Fonts, no CDNs), app.js (IIFE, XSS-safe DOM via textContent/createElement, RELATIVE /api/* URLs only — no REACT_APP_BACKEND_URL anywhere).
- backend/main.py mounts `StaticFiles(directory='../frontend/dist', html=True)` at "/" AFTER all API routes — single container serves both the API and the UI.
- /app/frontend/package.json kept as a thin supervisor shim: `yarn start` -> `python3 -m http.server 3000 --directory dist`. Zero Node deps; supervisor still happy in dev.
- NEW: /app/k8s/{deployment.yaml,service.yaml,README.md} — single Deployment, single container, /health + /ready probes, ClusterIP svc 80→8001, default env points at an in-cluster openai_compatible LLM gateway (air-gapped).
- README rewritten: single uvicorn command for local dev (Windows + macOS), Optional static-server section, K8s deploy section.
- Env fixups (unrelated to migration, but blocking startup): bumped pydantic to >=2.13 to match installed pydantic_core 2.46.x; reinstalled magika~=0.6.1, defusedxml, markdownify, docstring-parser, starlette>=0.40,<0.42.
- VERIFIED (iteration_7.json): 22/22 new pytest backend regression suite PASS (test_vanilla_frontend_integration.py), all Playwright UI flows GREEN — token mint, source/mode/output tab toggles, sync S3 extraction, async polling PENDING→COMPLETED, docs view rendering PRD/TRD/APP_FLOW. Single-container `curl http://localhost:8001/` returns index.html and `/api/health` returns 200 from the SAME port; preview ingress also routes `/` to :3000 static and `/api/*` to :8001.

## Update (2025-07) — PaddleOCR (PP-OCRv5) replaces Tesseract; multilingual (de+en)
- Replaced the Tesseract/pytesseract OCR engine with PaddleOCR PP-OCRv5 models.
- Runtime: models executed via ONNXRuntime (`rapidocr-onnxruntime`) because the
  platform is ARM64/aarch64 where native `paddlepaddle` inference segfaults
  (unsupported upstream). ONNXRuntime runs the identical PP-OCR models reliably.
- Multilingual: bundled `latin` PP-OCRv5 recognition model (backend/models/ocr/
  latin_rec.onnx + latin_dict.txt, ~8MB) covers German + English + ~35 more
  Latin-script languages, including German diacritics ä/ö/ü/ß. Detector + angle
  models ship inside the wheel -> fully offline, no runtime downloads.
- ocr_service.py rewritten (public API unchanged). ocrEngine now = "paddleocr:latin".
- config: ocr_languages default "latin" (+ optional ocr_model_dir override).
- requirements: removed pytesseract; added rapidocr-onnxruntime, onnxruntime, opencv-python.
- Verified: backend 5/5 tests pass — sync/async extraction regression OK; image-only
  German+English PDF -> ocrUsed=true, ocrEngine="paddleocr:latin", umlauts extracted.
