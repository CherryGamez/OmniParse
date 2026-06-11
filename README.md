# Enterprise Document Intelligence Platform

A FastAPI preprocessing & extraction layer that turns messy documents (PDF/DOCX/…)
into **structured JSON** via a two-step pipeline:

```
Document  ──MarkItDown──▶  Markdown  ──LLM (pluggable)──▶  Structured JSON
```

The LLM step is **provider-pluggable and self-hostable** — switch between native
Gemini, native Claude, or any **OpenAI-compatible** endpoint (vLLM / Ollama / TGI
/ LiteLLM / corporate gateway) with a single env var and **no external proxy
dependency**, so it runs fully **air-gapped**.

### "Omniparse" — extract from (almost) any file, incl. images & German IDs
- **Office/text:** PDF, DOCX, PPTX, XLS/XLSX, MSG (Outlook), TXT, MD, HTML, CSV, JSON (MarkItDown).
- **Images & scanned PDFs:** JPG/JPEG, PNG, TIFF, BMP, WEBP, GIF, **HEIC/HEIF** — via **offline Tesseract OCR** (`deu`+`eng`, fully air-gapped). Scanned/image-only PDFs are auto-detected (no text layer) and rasterized page-by-page with PDFium (`pypdfium2`), then OCR'd.
- **German first-class:** umlauts/ß preserved end-to-end; **Personalausweis / Reisepass / Aufenthaltstitel auto-detected** and mapped to a dedicated ID schema (documentType, surname, givenNames, dateOfBirth, placeOfBirth, nationality, documentNumber, dateOfExpiry, address, mrz[]).
- **Optional vision OCR:** set `OCR_VISION=true` to transcribe images with a hosted vision model (Llama-3.2-Vision / Qwen2-VL / llava) via your `openai_compatible` endpoint instead of Tesseract — higher accuracy on ID cards, still air-gapped.

> Requires the Tesseract binary + German data: `tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng` (see setup below).

It ships with SYNC + ASYNC extraction modes, mock OIDC/JWT auth + RBAC,
end-to-end `correlationId` tracing, RFC 7807 error responses, Kubernetes
health/ready probes, and a React **demo console** for showing the flow to your team.

---

## Architecture

```
backend/
├── main.py                      # FastAPI bootstrap, middleware, health/ready, routers
├── server.py                    # entrypoint shim (re-exports app from main)
├── core/
│   ├── config.py                # pydantic-settings (type-safe env vars)
│   ├── context.py               # request-scoped correlationId (ContextVar)
│   ├── logging_config.py        # structured JSON logging (every line has correlationId)
│   ├── security.py              # mock OIDC/JWT validation + RBAC (require_roles)
│   └── problem_details.py       # RFC 7807 problem+json exception handlers
├── schemas/payloads.py          # strictly-typed Pydantic request/response models
├── api/routes.py                # sync + async controllers, job status, token mint
├── services/
│   ├── extraction_pipeline.py   # Ingestion → MarkItDown → Chunking → LLM (tenacity retries)
│   └── job_repository.py        # async SQLAlchemy repository (SQLite → swap to Postgres)
└── requirements.txt
frontend/                        # React demo console (Vite-style CRA)
```

### Key endpoints

| Method | Path                        | Purpose                                            |
|--------|-----------------------------|----------------------------------------------------|
| POST   | `/api/v1/auth/token`        | Mint a mock OIDC bearer token (demo)               |
| POST   | `/api/v1/extract/sync`      | Synchronous extraction (small docs)                |
| POST   | `/api/v1/extract/async`     | Enqueue job → `202 Accepted` + `jobId` (+callback) |
| GET    | `/api/v1/jobs/{jobId}`      | Poll async job status + result                     |
| GET    | `/health`, `/ready`         | Kubernetes liveness / readiness probes             |
| GET    | `/api/health`, `/api/ready` | Same probes, reachable through the ingress         |
| GET    | `/docs`                     | Swagger / OpenAPI UI                               |

Both extraction endpoints accept **either** `multipart/form-data` (file upload)
**or** `application/json` (S3 object URI):

```jsonc
// application/json
{ "s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf",
  "instructions": "Extract invoiceNumber, total, lineItems[]",
  "callbackUrl": "https://camunda.example.com/..."  /* async only */ }
```

Every request may carry an `X-Correlation-Id` header; if absent one is generated.
It is echoed back on the response and stamped on every structured log line.

---

## Local development — step by step

> ### LLM providers (air-gapped + local testing)
> The model call is delegated to a **pluggable provider** selected by
> `LLM_PROVIDER` (no code change to switch):
>
> | `LLM_PROVIDER`       | Use case                         | Key vars                                                   |
> |----------------------|----------------------------------|------------------------------------------------------------|
> | `openai_compatible`  | **AIR-GAPPED** — self-hosted vLLM / Ollama / TGI / internal LiteLLM gateway | `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY` |
> | `gemini`             | Local testing — native Gemini    | `GEMINI_API_KEY`, `GEMINI_MODEL` (+ optional `GEMINI_BASE_URL`)   |
> | `anthropic`          | Local testing — native Claude    | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (+ optional `ANTHROPIC_BASE_URL`) |
>
> There is **no Emergent/Universal-Key/SaaS proxy** — every provider talks
> directly to the vendor SDK using your own key/endpoint. If the selected
> provider has no key/endpoint configured (or `USE_MOCK_LLM=true`), the pipeline
> transparently falls back to a **deterministic mock extractor** so the demo
> always works offline. `USE_MOCK_S3=true` already stubs S3 so no AWS is needed.
>
> **Air-gapped example** (point at a local Ollama):
> ```
> LLM_PROVIDER=openai_compatible
> OPENAI_BASE_URL=http://llm-gateway.internal:11434/v1
> OPENAI_MODEL=llama3.1
> OPENAI_API_KEY=not-needed        # required by the SDK, ignored by the server
> ```
> **Local testing with Gemini / Claude** — set `LLM_PROVIDER=gemini` (or
> `anthropic`) and paste your own key into `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`.

### Prerequisites
- **Python 3.10+** and **Node.js 18+** with **Yarn**
- **Tesseract OCR + German language data** (for image/scanned-doc extraction):
  - **macOS:** `brew install tesseract tesseract-lang` (includes `deu`)
  - **Debian/Ubuntu:** `sudo apt-get install -y tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng`
  - **Windows:** install the UB-Mannheim Tesseract build, tick **German** during setup, and add it to `PATH` (or set `pytesseract.pytesseract.tesseract_cmd`). Verify with `tesseract --list-langs` (should list `deu eng`).

---

### macOS / Linux

**1. Backend**
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env is already provided. Default LLM_PROVIDER=gemini (falls back to mock if no key).
# Air-gapped: set LLM_PROVIDER=openai_compatible + OPENAI_BASE_URL to your internal endpoint.

uvicorn main:app --reload --host 0.0.0.0 --port 8001
```
Backend is now at http://localhost:8001 — open http://localhost:8001/docs

**2. Frontend** (new terminal)
```bash
cd frontend
# Point the UI at your local backend:
echo "REACT_APP_BACKEND_URL=http://localhost:8001" > .env
yarn install
yarn start
```
Open http://localhost:3000

---

### Windows (PowerShell)

**1. Backend**
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# (Optional) air-gapped: set LLM_PROVIDER=openai_compatible in .env

uvicorn main:app --reload --host 0.0.0.0 --port 8001
```
> If `Activate.ps1` is blocked, run once:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

**2. Frontend** (new PowerShell window)
```powershell
cd frontend
Set-Content .env "REACT_APP_BACKEND_URL=http://localhost:8001"
yarn install
yarn start
```
Open http://localhost:3000

---

## Demo script (90 seconds)

1. Open the console — the **Health/Ready** badges turn green and a **mock OIDC
   token** is auto-minted.
2. Keep **S3 URI** selected (`s3://demo-bucket/contracts/invoice-001.pdf`) and
   **Sync** mode → click **Run Extraction**. The structured invoice JSON appears
   with the model badge + processing time. Flip to the **Markdown** tab to show
   the intermediate MarkItDown output.
3. Switch to **Async (202)** mode → **Run Extraction**. Watch the **job status
   badge** go `PENDING → PROCESSING → COMPLETED`, then the result renders.
4. Drag a real **PDF/DOCX** onto the **File Upload** dropzone to extract it live.

### curl cheat-sheet
```bash
BASE=http://localhost:8001
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"sub":"demo","roles":["extractor","admin"]}' | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")

# SYNC via S3 URI
curl -s -X POST $BASE/api/v1/extract/sync \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "X-Correlation-Id: demo-001" \
  -d '{"s3Uri":"s3://demo-bucket/contracts/invoice-001.pdf"}'

# SYNC via file upload
curl -s -X POST $BASE/api/v1/extract/sync \
  -H "Authorization: Bearer $TOKEN" -F "file=@/path/to/doc.pdf"

# ASYNC + poll
JOB=$(curl -s -X POST $BASE/api/v1/extract/async \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"s3Uri":"s3://demo-bucket/contracts/invoice-001.pdf"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['jobId'])")
curl -s $BASE/api/v1/jobs/$JOB -H "Authorization: Bearer $TOKEN"
```

---

---

## Running the tests
Test/dev dependencies are kept separate from the runtime requirements:
```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt   # pytest, requests, reportlab, python-docx
# point the suite at a running backend (local or preview):
REACT_APP_BACKEND_URL=http://localhost:8001 pytest tests/ -v
```
(The API tests hit a live server, so make sure the backend is running first.)

---

## Configuration (`backend/.env`)

| Variable             | Default                         | Notes                                              |
|----------------------|---------------------------------|----------------------------------------------------|
| `LLM_PROVIDER`       | `gemini`                        | `gemini` \| `anthropic` \| `openai_compatible`     |
| `USE_MOCK_LLM`       | `false`                         | `true` → deterministic offline extractor           |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | _empty_ / `gemini-3-pro-preview` | native Gemini (own key)               |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | _empty_ / `claude-sonnet-4-6` | native Claude (own key)         |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | `http://localhost:11434/v1` / `llama3.1` | **air-gapped** OpenAI-compatible endpoint |
| `OPENAI_API_KEY`     | `not-needed`                    | required by SDK; ignored by vLLM/Ollama            |
| `OPENAI_JSON_MODE`   | `true`                          | `false` if your server rejects `response_format`   |
| `USE_MOCK_S3`        | `true`                          | `true` → stubbed S3 (no AWS needed)                |
| `AUTH_DISABLED`      | `false`                         | local-only auth bypass                             |
| `DATABASE_URL`       | `sqlite+aiosqlite:///./jobs.db` | swap for `postgresql+asyncpg://…`                  |
| `MAX_SYNC_FILE_MB`   | `5`                             | larger files must use async                        |
| `CHUNK_CHAR_THRESHOLD` / `CHUNK_SIZE` | `12000` / `8000`| large-Markdown chunking                            |

> Provider selection is implemented in `services/llm_providers.py`
> (`build_provider`) and consumed by `services/extraction_pipeline.py`.

## Going to production
- For air-gapped: set `LLM_PROVIDER=openai_compatible` and point `OPENAI_BASE_URL`
  at your internal vLLM/Ollama/LiteLLM endpoint — no outbound internet required.
- Point `DATABASE_URL` at PostgreSQL (`pip install asyncpg`) — no code changes.
- Replace mock JWT validation in `core/security.py` with real OIDC/JWKS validation.
- Wire `DocumentIngestionService._fetch_from_s3` to boto3 and set `USE_MOCK_S3=false`.
- Tighten CORS to your ingress origin.
