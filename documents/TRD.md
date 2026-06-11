# Technical Requirement Document (TRD)

## OmniParse — Enterprise Document Intelligence Platform

| | |
|---|---|
| **Version** | 1.1 |
| **Last updated** | June 2026 |
| **Audience** | Backend / Frontend / DevOps engineers |

---

## 1. System Architecture

```
┌──────────────┐   multipart / JSON(s3Uri)    ┌─────────────────────────────┐
│ React Console │ ───────────────────────────▶ │ FastAPI  /api/v1            │
│ (CRA + TW)    │ ◀─────────────────────────── │  • auth/token (mock OIDC)   │
└──────────────┘        JSON / problem+json    │  • extract/sync             │
                                               │  • extract/async + jobs/{id}│
                                               │  • documents (PRD/TRD/Flow) │
                                               └──────────┬──────────────────┘
                                                          │
                              ┌────────────────────────────┴───────────────────┐
                              │              ExtractionPipeline                │
                              │ Ingestion → (route by type)                    │
                              │   images:  Vision LLM single-shot ──────────┐  │
                              │            └─fallback→ Tesseract OCR        │  │
                              │   pdf:     MarkItDown → OCR fallback        │  │
                              │   office:  MarkItDown                       │  │
                              │ → Chunking → LLM extraction (tenacity)  ◀───┘  │
                              └──────────────┬─────────────────────────────────┘
                                             │
              ┌──────────────────────────────┴────────────────────────┐
              │ Pluggable LLMProvider (LLM_PROVIDER env)              │
              │ emergent | gemini | anthropic | openai_compatible     │
              │ (all vision-capable) — or deterministic mock          │
              └───────────────────────────────────────────────────────┘
```

### Tech stack
| Layer | Technology |
|---|---|
| API | FastAPI 0.115, Uvicorn, Pydantic v2, pydantic-settings |
| Conversion | MarkItDown (pdf/docx/pptx/xls/xlsx/outlook extras) |
| OCR (offline) | Tesseract via pytesseract (`deu+eng`), pypdfium2 rasterization, pillow-heif (HEIC), pillow-avif-plugin (AVIF) |
| LLM SDKs | openai, anthropic, google-genai, emergentintegrations |
| Retries | tenacity (exponential backoff, 3 attempts, non-retryable 4xx) |
| Job store | SQLAlchemy 2 (async) + aiosqlite (PostgreSQL-ready) |
| Auth | python-jose JWT (mock OIDC), RBAC dependency `require_roles` |
| Frontend | React 18 (CRA), Tailwind 3, lucide-react |

---

## 2. Backend Module Layout

```
backend/
├── main.py                      # bootstrap, CORS, correlation middleware, RFC7807, health/ready
├── server.py                    # supervisor entrypoint shim (re-exports app)
├── core/
│   ├── config.py                # pydantic-settings — ALL env configuration
│   ├── context.py               # ContextVar correlationId
│   ├── logging_config.py        # structured JSON logging
│   ├── security.py              # mock JWT validation + RBAC
│   └── problem_details.py       # RFC 7807 handlers
├── schemas/payloads.py          # typed request/response models
├── api/routes.py                # controllers: extract sync/async, jobs, token, documents
└── services/
    ├── extraction_pipeline.py   # orchestrator + ingestion/conversion/chunking/LLM services
    ├── llm_providers.py         # provider abstraction (text + vision) + factory
    ├── ocr_service.py           # Tesseract OCR, image preprocessing, vision-safe b64
    └── job_repository.py        # async job persistence
```

---

## 3. Extraction Pipeline — Technical Behavior

### 3.1 Routing by file type (`ExtractionPipeline.run`)
1. `materialize()` writes the source to a temp file (guaranteed cleanup).
2. **Image extensions** (`.jpg .jpeg .png .tif .tiff .bmp .webp .gif .heic .heif .avif`):
   - If `OCR_VISION=true` **and** the provider supports vision →
     **single-shot vision extraction**: one multimodal call returns
     `{"transcription": "...", "structured": {...}}`. `ocrEngine="vision:<model>"`.
   - On vision failure (`LLMExtractionError`/`LLMFatalError` after retries) →
     log + fall back to Tesseract.
   - Tesseract path: pre-process (grayscale → upscale to ≥1600 px → autocontrast)
     → `image_to_string(lang="deu+eng")` → text LLM extraction. `ocrEngine="tesseract:deu+eng"`.
3. **PDF**: MarkItDown text layer; if empty and `OCR_ENABLED` → pypdfium2
   rasterization (300 DPI, ≤`OCR_MAX_PAGES`) + per-page Tesseract.
4. **Everything else**: MarkItDown.
5. Markdown > `CHUNK_CHAR_THRESHOLD` (12 000) is chunked (~8 000 chars, paragraph-aligned).
6. No extractable text → `ConversionError` → **422** problem+json (never 500).

### 3.2 Vision payload normalization (`OCRService.image_to_b64`)
- JPEG/PNG/WEBP ≤4 MB and ≤2400 px pass through raw.
- AVIF/HEIC/TIFF/BMP/GIF/oversized → decode with Pillow → RGB → thumbnail
  ≤2400 px → re-encode JPEG q92. MIME always matches actual bytes.

### 3.3 LLM contract
- **Text**: system prompt forces a single JSON object; German ID/licence schema
  hint included; response parsed with fence-stripping + outer-`{}` fallback.
- **Vision**: system prompt forces the
  `{"transcription": ..., "structured": ...}` envelope; if the model ignores
  the envelope the whole object is treated as `structured`.
- **Retries** (both paths): 3 attempts, exponential backoff 2–10 s. HTTP 4xx
  (except 429) → `LLMFatalError` (no retry, surfaces as 502). Network/5xx/429 →
  retryable `LLMExtractionError`.

### 3.4 Provider abstraction (`llm_providers.py`)
```python
class LLMProvider(ABC):
    supports_vision: bool
    async def complete(system, prompt) -> str
    async def complete_vision(system, prompt, image_b64, mime) -> str
    model_name: str
```
| Provider | Vision mechanism |
|---|---|
| `EmergentProvider` | emergentintegrations `LlmChat` + `ImageContent(image_base64=...)`; fresh stateless chat per call |
| `GeminiProvider` | `types.Part.from_bytes(data, mime_type)` + `response_mime_type="application/json"` |
| `AnthropicProvider` | base64 image content block |
| `OpenAICompatibleProvider` | standard `image_url` data-URI message part |

`build_provider(settings)` returns `None` (→ deterministic mock) when the
selected provider lacks a key/endpoint, keeping the demo always functional.

### 3.5 Mock extractor (offline demo)
- Generic docs: title/sections/key-value fields/summary heuristics.
- German ID/licence docs (marker-based detection incl. FÜHRERSCHEIN/DRIVING
  LICENCE): label-aware lookup that handles the physical card layout where the
  **value is printed on the line below the trilingual label**
  (e.g. `Name/Surname/Nom` ⏎ `MUSTERMANN`). MRZ lines detected by `<<` filler.

---

## 4. API Contract (summary)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/token` | – | mint mock JWT (`sub`, `roles[]`) |
| POST | `/api/v1/extract/sync` | extractor/admin | multipart **or** JSON `s3Uri`; ≤5 MB; returns `ExtractionResult` |
| POST | `/api/v1/extract/async` | extractor/admin | 202 + `jobId`, optional `callbackUrl` webhook |
| GET | `/api/v1/jobs/{jobId}` | extractor/admin | PENDING/PROCESSING/COMPLETED/FAILED + result |
| GET | `/api/v1/documents` | – | list PRD/TRD/App-Flow |
| GET | `/api/v1/documents/{id}` | – | `{id,title,content(markdown)}` |
| GET | `/health` `/ready` (+`/api` aliases) | – | k8s probes; ready checks DB |

`ExtractionResult`: `correlationId, sourceType, filename, markdown,
markdownChars, chunked, chunkCount, structured, model, mock, ocrUsed,
ocrEngine, processingMs`.

Errors are RFC 7807 `application/problem+json` with `correlationId`:
400 ingestion/missing-source · 401/403 auth · 404 job/doc · 413 sync size ·
422 unprocessable document · 502 LLM provider failure.

---

## 5. Configuration (env vars — `backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` (code) / `emergent` (preview .env) | provider selection |
| `EMERGENT_LLM_KEY` / `EMERGENT_MODEL_PROVIDER` / `EMERGENT_MODEL` | – / `openai` / `gpt-5.4` | universal-key gateway |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-3-pro-preview` | own Gemini key |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | – / `claude-sonnet-4-6` | own Claude key |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY` | `http://localhost:11434/v1` / `llama3.1` / `not-needed` | air-gapped endpoint |
| `USE_MOCK_LLM` | `false` | force deterministic mock |
| `OCR_VISION` | **`true`** | vision-LLM image reading (fallback: Tesseract) |
| `OCR_ENABLED` / `OCR_LANGUAGES` / `OCR_DPI` / `OCR_MAX_PAGES` | `true` / `deu+eng` / `300` / `25` | offline OCR |
| `USE_MOCK_S3` | `true` | stub S3 |
| `MAX_SYNC_FILE_MB` | `5` | sync size cap |
| `CHUNK_CHAR_THRESHOLD` / `CHUNK_SIZE` | `12000` / `8000` | chunking |
| `DATABASE_URL` | `sqlite+aiosqlite:///./jobs.db` | job store (PostgreSQL-ready) |
| `AUTH_DISABLED` | `false` | local smoke-test bypass only |

System packages required for the OCR fallback path:
`tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng`.

---

## 6. Non-Functional Requirements

- **Security**: documents handled only in temp files with guaranteed cleanup;
  no document content persisted (only job metadata + result JSON); JWT-gated
  extraction endpoints; XSS-safe JSON viewer (no `dangerouslySetInnerHTML`).
- **Air-gap**: with `openai_compatible` + Tesseract, zero outbound internet.
- **Observability**: structured JSON logs, every line stamped with correlationId;
  `X-Correlation-Id` echoed on every response.
- **Resilience**: tenacity retries; vision→Tesseract fallback; LLM failures map
  to 502, document problems to 4xx; callbacks are best-effort (never crash worker).
- **Portability**: Windows-safe PDF rasterization (pypdfium2, no PyMuPDF
  atexit crash); HEIC/AVIF wheels, no external binaries except Tesseract.

---

## 7. Testing

- `backend/tests/test_extraction_api.py` — health/ready, auth/RBAC (401/403),
  sync (S3 + multipart), async lifecycle, 404, correlation echo.
- `backend/tests/test_regression_formats.py` — format matrix (9 office/text
  types), blank-PDF 422/FAILED regression, OCR image tests, umlaut round-trip.
- **Note**: the pytest suites assert **mock-mode** invariants (`mock=true`,
  `ocrEngine=tesseract:*`). Run them with `USE_MOCK_LLM=true OCR_VISION=false`.
  Real-LLM accuracy is validated via the test reports in `/test_reports`.

---

## 8. Production Hardening Checklist (backlog)
- Real OIDC/JWKS validation; PostgreSQL (`asyncpg`); real boto3 S3.
- `STRICT_LLM_PROVIDER` fail-fast flag (no silent mock in prod).
- Webhook signing for callbacks; CORS tightened to ingress origin.
- OpenTelemetry traces; per-tenant API keys / usage metering.
