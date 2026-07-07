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

The UI is a **dependency-free vanilla HTML / CSS / JS** console, served by the
same FastAPI container — **no Node.js, no yarn, no build step** required in
production.

---

### "Omniparse" — extract from (almost) any file, incl. images & German IDs
- **Office/text:** PDF, DOCX, PPTX, XLS/XLSX, MSG (Outlook), TXT, MD, HTML, CSV, JSON (MarkItDown).
- **Images & scanned PDFs:** JPG/JPEG, PNG, TIFF, BMP, WEBP, GIF, **HEIC/HEIF** — via **offline PaddleOCR (PP-OCRv5)** run through ONNXRuntime (multilingual `latin` model → German + English + ~35 more Latin-script languages, fully air-gapped). Scanned/image-only PDFs are auto-detected (no text layer) and rasterized page-by-page with PDFium (`pypdfium2`), then OCR'd.
- **German first-class:** umlauts/ß preserved end-to-end; **Personalausweis / Reisepass / Aufenthaltstitel auto-detected** and mapped to a dedicated ID schema (documentType, surname, givenNames, dateOfBirth, placeOfBirth, nationality, documentNumber, dateOfExpiry, address, mrz[]).
- **Optional vision OCR:** set `OCR_VISION=true` to transcribe images with a hosted vision model (Llama-3.2-Vision / Qwen2-VL / llava) via your `openai_compatible` endpoint instead of PaddleOCR — higher accuracy on ID cards, still air-gapped.

> No system OCR binary required — PaddleOCR PP-OCRv5 models run via ONNXRuntime (`rapidocr-onnxruntime`). The recognition model + dictionary ship in `backend/models/ocr`; detector/angle models ship inside the wheel, so OCR works fully offline with no runtime downloads.

It ships with SYNC + ASYNC extraction modes, mock OIDC/JWT auth + RBAC,
end-to-end `correlationId` tracing, RFC 7807 error responses, Kubernetes
health/ready probes, and a vanilla HTML **demo console** for showing the flow
to your team.

---

## Architecture

```
backend/
├── main.py                      # FastAPI bootstrap + StaticFiles mount (serves the UI at /)
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
frontend/
├── dist/                        # vanilla HTML/CSS/JS UI — served by FastAPI's StaticFiles
│   ├── index.html
│   ├── styles.css
│   └── app.js                   # all logic, uses RELATIVE /api/* URLs (same origin)
├── legacy-react/                # archived old React/CRA app (reference only)
├── package.json                 # thin dev wrapper (yarn start → python http.server)
└── README.md
k8s/
├── deployment.yaml              # single-container deployment (FastAPI serves API + UI)
└── service.yaml                 # ClusterIP service on port 80 → pod 8001
```

### Key endpoints

| Method | Path                        | Purpose                                            |
|--------|-----------------------------|----------------------------------------------------|
| GET    | `/`                         | Vanilla HTML console (served from `frontend/dist`) |
| POST   | `/api/v1/auth/token`        | Mint a mock OIDC bearer token (demo)               |
| POST   | `/api/v1/extract/sync`      | Synchronous extraction (small docs)                |
| POST   | `/api/v1/extract/async`     | Enqueue job → `202 Accepted` + `jobId` (+callback) |
| GET    | `/api/v1/jobs/{jobId}`      | Poll async job status + result                     |
| GET    | `/api/v1/documents`         | List + fetch PRD/TRD/App-Flow markdown docs        |
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
> If the selected provider has no key/endpoint configured (or `USE_MOCK_LLM=true`)
> the pipeline transparently falls back to a **deterministic mock extractor**.
> `USE_MOCK_S3=true` already stubs S3 so no AWS is needed.
>
> **Air-gapped example** (point at a local Ollama):
> ```
> LLM_PROVIDER=openai_compatible
> OPENAI_BASE_URL=http://llm-gateway.internal:11434/v1
> OPENAI_MODEL=llama3.1
> OPENAI_API_KEY=not-needed        # required by the SDK, ignored by the server
> ```

### Prerequisites
- **Python 3.10+** — that's it for production. **No Node.js / yarn required.**
- **No system OCR binary needed.** OCR uses **PaddleOCR (PP-OCRv5) via ONNXRuntime**
  (`rapidocr-onnxruntime`, installed from `requirements.txt`). The multilingual
  `latin` recognition model + dictionary are bundled in `backend/models/ocr`
  (German + English + ~35 Latin-script languages); detector/angle models ship
  inside the wheel — so image/scanned-doc extraction works fully offline.
  - **Linux slim images:** ensure `libgl1` and `libglib2.0-0` are present (OpenCV
    dependency) — already handled in the provided Dockerfile.

---

### macOS / Linux — single command (recommended)

The FastAPI server now **serves the vanilla UI directly**, so there is no
separate frontend process:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Open **http://localhost:8001** — that's both the console and the API.
Swagger lives at **http://localhost:8001/docs**.

> ### Want to edit the UI?
> Open `frontend/dist/index.html`, `frontend/dist/styles.css`,
> `frontend/dist/app.js` — refresh the browser, that's it. No bundler.

---

### Windows (PowerShell) — single command

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

> If `Activate.ps1` is blocked, run once:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

Open **http://localhost:8001** in your browser.

#### Windows quirk: PDF rasterization
We use **pypdfium2** (PDFium wheel — no external binaries, no AGPL) for scanned
PDFs. Earlier PyMuPDF/`fitz` builds crashed on uvicorn shutdown on Windows; if
you ever see a `fz_set_warning_callback` error in the log, make sure
`requirements.txt` pins `pypdfium2>=4.30` (it does by default).

---

### Optional — run the UI on a separate port

If you'd rather decouple the UI from the API process (e.g. behind a CDN), the
`frontend/` directory works as **plain static files** — serve them with any
HTTP server. We ship a convenience script:

```bash
cd frontend
python3 -m http.server 3000 --directory dist
# (or: yarn start  — which just calls the line above)
```

The UI uses **relative URLs** for every API call, so just make sure your
ingress / reverse-proxy forwards `/api/*` to the FastAPI pod on port `8001`.

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

## Kubernetes deployment (single container, air-gapped)

The platform ships as one container that serves **both** the API and the UI.
A `Dockerfile` and a minimal manifest set are included:

```bash
# 1. Build the image (Python 3.11 + PaddleOCR/ONNX + vanilla UI baked in)
docker build -t YOUR_REGISTRY/doc-intel:1.0.0 .

# 2. (Optional) smoke-test locally
docker run --rm -p 8001:8001 YOUR_REGISTRY/doc-intel:1.0.0
# → open http://localhost:8001

# 3. Push, then deploy:
docker push YOUR_REGISTRY/doc-intel:1.0.0
# update `image:` in k8s/deployment.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 4. Port-forward or wire to your Ingress:
kubectl port-forward svc/doc-intel 8080:80
# → open http://localhost:8080
```

The Dockerfile is **multi-stage and rootless** (image ≈ 400 MB), runs **PaddleOCR
(PP-OCRv5) via ONNXRuntime** for offline OCR (no system OCR binary; models bundled
in `backend/models/ocr`), mounts a writable `/data` volume for the embedded SQLite
job store, and exposes `HEALTHCHECK` against `/health`.

Configure the LLM via env vars in `k8s/deployment.yaml`:

```yaml
env:
  - name: LLM_PROVIDER
    value: openai_compatible              # air-gapped path
  - name: OPENAI_BASE_URL
    value: http://llm-gateway.internal/v1
  - name: OPENAI_MODEL
    value: llama3.1
  - name: OPENAI_API_KEY
    value: not-needed
  - name: USE_MOCK_S3
    value: "true"
```

The deployment uses `/health` for **liveness** and `/ready` for **readiness**.
Both are reachable without auth on the pod.

---

## Why this platform exists — token economics

A scanned 20-page contract sent directly to a vision LLM costs ~**22,000 input
tokens** (≈ 1,100 per page-tile, billed *every time* you process it). The same
contract through this pipeline:

1. **MarkItDown / PaddleOCR** turn the document into Markdown locally — **0 LLM
   tokens** (deterministic, free, offline).
2. **Heading-aware chunking with overlap** keeps every LLM call under the
   model's context window; tables and line items are never split mid-row.
3. **Only the Markdown → JSON** step calls the LLM — usually a single call,
   sometimes a handful for very large docs.

Average result on the benchmark suite: **~3,000 tokens** instead of ~22,000
for the same document — a **78 % reduction** in LLM cost.

Every sync/async extraction response surfaces both numbers:

```jsonc
{
  "tokensEstimate":    3142,    // input + output tokens actually consumed
  "tokensSavedVsRaw": 18858,    // what a vision-LLM-per-page would have cost
  "chunkCount":          2
}
```

See **`documents/BENEFITS.md`** (also rendered in the in-app **Docs** view) for
the full breakdown — cost table, air-gapped checklist, determinism /
auditability, and chunking design notes.

---

## Running the tests
Test/dev dependencies are kept separate from the runtime requirements:
```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt   # pytest, requests, reportlab, python-docx
# point the suite at a running backend (local or preview):
BACKEND_URL=http://localhost:8001 pytest tests/ -v
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
| `CHUNK_CHAR_THRESHOLD` / `CHUNK_SIZE` / `CHUNK_OVERLAP` | `12000` / `8000` / `400` | structure-aware Markdown chunking (heading-bounded with overlap) |

> Provider selection is implemented in `services/llm_providers.py`
> (`build_provider`) and consumed by `services/extraction_pipeline.py`.

## Going to production
- For air-gapped: set `LLM_PROVIDER=openai_compatible` and point `OPENAI_BASE_URL`
  at your internal vLLM/Ollama/LiteLLM endpoint — no outbound internet required.
- Point `DATABASE_URL` at PostgreSQL (`pip install asyncpg`) — no code changes.
- Replace mock JWT validation in `core/security.py` with real OIDC/JWKS validation.
- Wire `DocumentIngestionService._fetch_from_s3` to boto3 and set `USE_MOCK_S3=false`.
- Tighten CORS to your ingress origin.
