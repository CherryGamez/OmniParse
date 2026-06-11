# App Flow Document

## OmniParse — Enterprise Document Intelligence Platform

| | |
|---|---|
| **Version** | 1.1 |
| **Last updated** | June 2026 |

This document walks through every user-facing flow of the application, screen
by screen and request by request.

---

## 1. Application Entry

```
User opens the app (/)
   │
   ├─▶ Header renders: title, Console|Docs navigation, Health & Ready badges
   │      • GET /api/health  → badge green when status=healthy
   │      • GET /api/ready   → badge green when DB check passes
   │
   └─▶ A mock OIDC token is auto-minted on load
          • POST /api/v1/auth/token  {sub:"demo-user", roles:["extractor","admin"]}
          • JWT stored in the token textarea (editable, re-mintable)
```

The UI is a two-column console: **Input Panel** (left) and **Output Panel**
(right), plus a **Docs** view reachable from the header navigation.

---

## 2. Flow A — Sync extraction of an image (ID card / licence / photo)

**Actor**: back-office operator • **Goal**: structured fields from a document photo

1. User stays on **File Upload** tab and drops e.g. `personalausweis.jpg`
   (accepted: pdf, docx, pptx, xls(x), msg, txt, md, html, csv, json, jpg,
   jpeg, png, tif(f), bmp, webp, gif, heic, heif, avif).
2. (Optional) edits **Extraction Instructions**, e.g.
   *"Extract surname, given names, date of birth, document number, expiry."*
3. Mode = **Sync** → clicks **Run Extraction**.
4. Frontend: `POST /api/v1/extract/sync` (multipart) with
   `Authorization: Bearer <jwt>` and a generated `X-Correlation-Id: ui-...`.
5. Backend pipeline:
   ```
   materialize temp file
     └─ image? ──yes──▶ OCR_VISION + vision provider?
                           ├─ yes ─▶ ONE vision-LLM call
                           │         → {transcription, structured}
                           │         ocrEngine = "vision:openai:gpt-5.4"
                           └─ no/failed ─▶ Tesseract (preprocessed, deu+eng)
                                           → text LLM → structured
   temp file deleted (always)
   ```
6. Response renders in the Output Panel:
   - **Structured JSON** tab — extracted fields (German ID schema for
     Personalausweis/Reisepass/Aufenthaltstitel/Führerschein).
   - **Markdown (intermediate)** tab — the transcription.
   - Meta strip: correlation id, `OCR vision:<model>` badge, model badge,
     processing time in ms.

**Error paths**
| Condition | UX |
|---|---|
| Blank/corrupt/unreadable file | 422 → red error panel: "no extractable text…" |
| File > 5 MB on sync | 413 → error panel suggests async endpoint |
| LLM provider outage | 502 after retries → error panel |
| Missing/expired token | 401 → error panel; user clicks **Re-mint Demo Token** |

---

## 3. Flow B — Sync extraction via S3 URI (JSON body)

1. User switches source to **S3 URI** (prefilled demo URI
   `s3://demo-bucket/contracts/invoice-001.pdf`).
2. **Run Extraction** → `POST /api/v1/extract/sync` with JSON
   `{s3Uri, instructions}`.
3. Mocked S3 returns the demo invoice; MarkItDown → LLM (or mock) → invoice
   JSON (documentType, parties, line items, totals) renders.

---

## 4. Flow C — Async extraction with job polling (+ optional callback)

**Actor**: integration engineer (Camunda 8 pattern)

1. User selects mode **Async (202)**; an optional **callbackUrl** field appears.
2. **Run Extraction** → `POST /api/v1/extract/async` (multipart or JSON).
3. Immediate response: `202 {jobId, status: PENDING, statusUrl, correlationId}`.
   The job badge appears: `PENDING` (pulsing).
4. Frontend polls `GET /api/v1/jobs/{jobId}` every 1.5 s:
   `PENDING → PROCESSING → COMPLETED | FAILED`.
5. On **COMPLETED**: result renders exactly like the sync flow.
   On **FAILED**: friendly error from the job record is shown.
6. If a `callbackUrl` was supplied, the backend POSTs
   `{jobId, status, correlationId, result|error}` to it (best-effort).

---

## 5. Flow D — Reading the project documents (Docs view)

1. User clicks **Docs** in the header navigation.
2. Frontend: `GET /api/v1/documents` → sidebar lists
   *Product Requirement Document*, *Technical Requirement Document*,
   *App Flow Document*.
3. Selecting an entry fetches `GET /api/v1/documents/{id}` and renders the
   Markdown (headings, tables, code blocks, lists) in the reading pane.
   Content is served from the repository's `documents/` folder, so the docs
   shipped with the code are always the docs shown in the app.

---

## 6. Flow E — Developer flows

### Swagger exploration
`GET /docs` (FastAPI UI) → try endpoints with the minted bearer token.

### curl quick test
```bash
BASE=<backend-url>
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"sub":"demo","roles":["extractor","admin"]}' | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")

curl -s -X POST $BASE/api/v1/extract/sync \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@id_card.jpg" \
  -F "instructions=Extract all identity fields"
```

### Provider switching (no code change)
```
backend/.env →  LLM_PROVIDER=openai_compatible   # air-gapped vLLM/Ollama
                LLM_PROVIDER=emergent             # hosted preview (universal key)
                LLM_PROVIDER=gemini|anthropic     # own vendor key
                USE_MOCK_LLM=true                 # fully offline demo
```

---

## 7. State Machine — Async Job

```
            create (202)
                │
            ┌───▼────┐   worker picks up   ┌────────────┐
            │PENDING │ ──────────────────▶ │ PROCESSING │
            └────────┘                     └─────┬──────┘
                                  pipeline ok    │    pipeline raises
                              ┌──────────────────┴──────────────────┐
                        ┌─────▼─────┐                         ┌─────▼────┐
                        │ COMPLETED │                         │  FAILED  │
                        │ +result   │                         │ +error   │
                        └─────┬─────┘                         └─────┬────┘
                              └────────── callbackUrl POST ─────────┘
```

---

## 8. Data Flow Summary (what is stored where)

| Data | Location | Lifetime |
|---|---|---|
| Uploaded document bytes | temp file (`docintel_*`) | deleted immediately after conversion (guaranteed by context manager) |
| Job metadata + result JSON | `jobs.db` (SQLite) | persistent |
| JWT | browser memory (React state) | session |
| Documents (PRD/TRD/Flow) | `documents/*.md` in repo | versioned with code |
| LLM payloads | sent to configured provider only | not persisted |
