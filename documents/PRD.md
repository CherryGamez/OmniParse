# Product Requirement Document (PRD)

## OmniParse — Enterprise Document Intelligence Platform

| | |
|---|---|
| **Version** | 1.1 |
| **Status** | Active |
| **Last updated** | June 2026 |
| **Owner** | Product / Platform Engineering |

---

## 1. Product Vision

OmniParse turns **any messy document — office files, scans, photos and identity
documents — into clean, structured JSON** that downstream systems (BPMN engines,
ERPs, case-management tools) can consume directly. It is built to run
**self-hosted and air-gapped**: every component (OCR, LLM, storage, auth) can
operate without outbound internet access.

### Problem statement
Enterprises receive business-critical data locked inside unstructured documents:
invoices as PDFs, contracts as DOCX, ID cards and driver's licences as phone
photos. Manual transcription is slow, error-prone, and does not scale.
Cloud-only extraction SaaS is often prohibited for compliance reasons
(identity documents, GDPR, BaFin/KRITIS environments).

### Solution
A single extraction API + demo console that:
1. Accepts a document (upload or S3 URI).
2. Converts it to an intermediate text/Markdown representation.
3. Uses a **pluggable LLM** (self-hosted or cloud) to emit structured JSON.
4. For **images (ID cards, licences, receipts)** uses a **vision-capable LLM**
   that reads the image directly — falling back to offline PaddleOCR (PP-OCRv5)
   when no vision model is available.

---

## 2. Target Users / Personas

| Persona | Goal |
|---|---|
| **Integration engineer** | Wire document extraction into BPMN orchestration (Camunda 8) via async jobs + callbacks. |
| **Back-office operator** | Upload an ID card / invoice in the console and get verified structured fields. |
| **Compliance officer** | Ensure no document data leaves the company network (air-gapped LLM + offline OCR). |
| **Developer / reviewer** | Explore the API via Swagger and the demo console; read PRD/TRD/App-Flow docs in-app. |

---

## 3. Core Features (What's in)

### F1 — Universal document ingestion
- **Upload** via `multipart/form-data` or **S3 object URI** via JSON body (S3 mocked for demo).
- Supported formats:
  - **Office/text**: PDF, DOCX, PPTX, XLS/XLSX, MSG, TXT, MD, HTML, CSV, JSON
  - **Images**: JPG/JPEG, PNG, TIFF, BMP, WEBP, GIF, HEIC/HEIF, **AVIF**
  - **Scanned/image-only PDFs** (auto-detected via missing text layer)

### F2 — Two extraction modes
- **Sync** (`POST /api/v1/extract/sync`) — inline result for small documents (≤5 MB).
- **Async** (`POST /api/v1/extract/async`) — `202 Accepted` + `jobId`, job polling,
  and optional **callbackUrl** webhook on completion (Camunda-ready).

### F3 — AI Vision extraction for images (KEY FEATURE)
- Images are sent **directly to a vision-capable LLM** in a single shot that
  returns both a faithful transcription and structured JSON.
- Solves the accuracy problem on **German ID cards (Personalausweis), passports,
  residence permits and driver's licences (Führerschein)** where classic OCR is
  defeated by guilloche security backgrounds.
- Automatic **fallback to offline PaddleOCR (PP-OCRv5, multilingual `latin` =
  German + English + more)** when no vision model is configured or the vision
  call fails.

### F4 — German identity-document schema
Auto-detected German/EU identity documents map to a dedicated schema:
`documentType, country, surname, givenNames, nameAtBirth, dateOfBirth,
placeOfBirth, nationality, documentNumber, dateOfIssue, dateOfExpiry,
issuingAuthority, address, accessNumber (CAN), categories (licence classes),
mrz[]`. German characters (ä ö ü ß) are preserved end-to-end.

### F5 — Pluggable, self-hostable LLM layer
Selected via `LLM_PROVIDER` env var, **no code change**:

| Provider | Use case |
|---|---|
| `openai_compatible` | **Air-gapped**: self-hosted vLLM / Ollama / TGI / LiteLLM gateway (incl. vision models like Qwen2-VL) |
| `emergent` | Hosted preview: one universal key for OpenAI / Anthropic / Gemini models |
| `gemini` / `anthropic` | Local testing with own vendor keys |
| *(none / no key)* | Deterministic mock extractor — demo always works offline |

### F6 — Enterprise platform features
- Mock OIDC/JWT auth + **RBAC** (`extractor` / `admin` roles).
- End-to-end **correlationId** tracing (`X-Correlation-Id` echo, structured JSON logs).
- **RFC 7807** `problem+json` errors (400/401/403/404/413/422/502).
- Kubernetes `/health` + `/ready` probes.
- Async job persistence (SQLite, swappable to PostgreSQL).

### F7 — Demo console (React)
- Token mint, file dropzone / S3 URI input, custom extraction instructions,
  sync/async switch, JSON + intermediate-Markdown viewers, OCR/model badges,
  live job-status polling.
- **In-app Docs view** rendering this PRD, the TRD and the App Flow document.

---

## 4. Out of Scope (current release)
- Real S3/boto3 ingestion (stubbed; interface ready).
- Real OIDC/JWKS validation (mock JWT for demo).
- Human-in-the-loop field review/correction UI.
- Multi-tenant usage metering / billing.

---

## 5. Success Metrics

| Metric | Target |
|---|---|
| Field-level accuracy on German ID samples (vision path) | ≥ 95% |
| Sync extraction latency (image, vision path) | < 20 s p95 |
| Supported file formats | ≥ 18 |
| Extraction failure surfaced as actionable 4xx (not 500) | 100% |
| Demo runs fully offline (mock + PaddleOCR) | Yes |

---

## 6. Key User Stories

1. **As an operator**, I upload a photo of a Personalausweis and receive surname,
   given names, date of birth, nationality, document number and expiry date as
   JSON — with umlauts intact.
2. **As an integration engineer**, I POST an S3 URI with a `callbackUrl` and
   receive the structured result on my webhook when the job completes.
3. **As a compliance officer**, I configure `LLM_PROVIDER=openai_compatible`
   pointing at our internal vLLM cluster and verify no outbound traffic.
4. **As a reviewer**, I open the console, run the demo invoice, flip to the
   Markdown tab, and read the product docs in the Docs view.

---

## 7. Release Notes (delta in this release)

- **FIXED**: Wrong/partial extraction on attached ID-card images. Root causes:
  (1) images were read by an OCR engine whose output was corrupted by ID-card
  security patterns; (2) the vision path was unreachable for the default
  provider (no vision support implemented for Gemini/Anthropic, `OCR_VISION`
  defaulted to false); (3) the heuristic fallback assumed `Label: value` on one
  line, while German cards print values on the line **below** the label.
- **NEW**: Single-shot vision extraction (image → transcription + structured JSON).
- **NEW**: Vision support for all providers (Emergent / Gemini / Anthropic / OpenAI-compatible).
- **NEW**: AVIF image support; OCR image pre-processing (orientation/exif handling).
- **NEW (2025-07)**: OCR engine migrated from Tesseract to **PaddleOCR (PP-OCRv5)**
  run via ONNXRuntime — multilingual `latin` model (German + English + ~35 more
  Latin-script languages), no system binary, models bundled, `ocrEngine=paddleocr:latin`.
- **NEW**: Führerschein (driving licence) added to ID detection + schema (categories).
- **NEW**: `documents/` folder with PRD, TRD, App Flow + in-app Docs view + API.
