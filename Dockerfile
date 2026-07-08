# syntax=docker/dockerfile:1.6
# ============================================================================
#  Document Intelligence Platform — single-container, air-gapped image
#
#  • FastAPI serves BOTH the JSON API (`/api/*`) and the vanilla HTML UI (`/`)
#  • Offline OCR via PaddleOCR (PP-OCRv5) models run through ONNXRuntime —
#    pure-python wheels, no system OCR binary; models bundled under backend/models
#  • No Node.js, no yarn — the UI is plain dist/index.html + styles.css + app.js
#  • Final image size ≈ 400 MB (python-slim + onnxruntime + python wheels)
# ============================================================================

# ----------------------------------------------------------------------------
# Stage 1: build dependencies
# ----------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Compiler toolchain only needed at build time (some wheels build C extensions
# on minor python versions). Removed from the runtime image below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      gcc \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# ----------------------------------------------------------------------------
# Stage 2: runtime
# ----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    # Default to air-gapped path; override per environment.
    LLM_PROVIDER=openai_compatible \
    USE_MOCK_S3=true \
    DATABASE_URL=sqlite+aiosqlite:////data/jobs.db \
    PORT=8001

# Runtime system packages:
#   * libgl1 / libglib2.0-0      — OpenCV (PaddleOCR/RapidOCR) + pypdfium2 / PIL
#   * curl                       — for HEALTHCHECK
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgl1 \
      libglib2.0-0 \
      curl \
 && rm -rf /var/lib/apt/lists/*

# Copy python deps from the builder stage.
COPY --from=builder /install /usr/local

# Application code.
WORKDIR /app
COPY backend/ /app/backend/
COPY frontend/dist/ /app/frontend/dist/
COPY documents/ /app/documents/

# Persistent volume for the SQLite job DB (mount a PVC at /data in K8s).
RUN mkdir -p /data && chmod 0777 /data
VOLUME ["/data"]

# Non-root user (rootless containers + restricted PSP friendliness).
RUN groupadd --system app && useradd --system --gid app --home /app app \
 && chown -R app:app /app /data
USER app

WORKDIR /app/backend

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/health || exit 1

# `--workers 1` is correct for the embedded SQLite repository. Switch to
# multiple workers only after moving DATABASE_URL to PostgreSQL.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
