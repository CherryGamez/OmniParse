"""
FastAPI application bootstrap.

Wires together:
  * structured JSON logging
  * correlation-id middleware (reads/sets `X-Correlation-Id`)
  * RFC 7807 problem-detail exception handlers
  * Kubernetes liveness (`/health`) and readiness (`/ready`) probes
  * the versioned API router (`/api/v1/...`)
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.routes import router as api_router
from core import problem_details as problems
from core.config import get_settings
from core.context import set_correlation_id
from core.logging_config import configure_logging
from schemas.payloads import HealthResponse, ReadyResponse
from services.extraction_pipeline import (
    ConversionError,
    IngestionError,
    LLMExtractionError,
    LLMFatalError,
)
from services.job_repository import engine, init_db

settings = get_settings()
configure_logging()
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup and dispose of them on shutdown."""
    await init_db()
    logger.info(
        "Application startup complete",
        extra={"extra_fields": {"version": settings.app_version, "env": settings.environment}},
    )
    yield
    await engine.dispose()
    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Enterprise preprocessing & extraction layer. Converts messy documents "
        "(PDF/DOCX) into structured JSON via a two-step pipeline: "
        "Document → Markdown (MarkItDown) → Structured JSON (LLM)."
    ),
    lifespan=lifespan,
)

# CORS - the React demo UI calls the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Bind a correlation id for the request and echo it back on the response."""
    correlation_id = (
        request.headers.get("X-Correlation-Id")
        or request.headers.get("correlationId")
        or uuid.uuid4().hex
    )
    set_correlation_id(correlation_id)

    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id

    logger.info(
        "Request handled",
        extra={
            "extra_fields": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "durationMs": int((time.perf_counter() - start) * 1000),
            }
        },
    )
    return response


# RFC 7807 problem-detail handlers.
app.add_exception_handler(StarletteHTTPException, problems.http_exception_handler)
app.add_exception_handler(RequestValidationError, problems.validation_exception_handler)
app.add_exception_handler(Exception, problems.unhandled_exception_handler)


# Domain-specific pipeline errors -> meaningful RFC 7807 responses (not a 500).
async def ingestion_error_handler(request: Request, exc: IngestionError) -> JSONResponse:
    return problems.problem_response(
        400, f"Could not read the source document: {exc}",
        instance=request.url.path, title="Ingestion Error",
    )


async def conversion_error_handler(request: Request, exc: ConversionError) -> JSONResponse:
    return problems.problem_response(
        422, str(exc), instance=request.url.path, title="Unprocessable Document",
    )


async def llm_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return problems.problem_response(
        502, f"LLM extraction failed: {exc}",
        instance=request.url.path, title="Bad Gateway (LLM Provider)",
    )


app.add_exception_handler(IngestionError, ingestion_error_handler)
app.add_exception_handler(ConversionError, conversion_error_handler)
app.add_exception_handler(LLMFatalError, llm_error_handler)
app.add_exception_handler(LLMExtractionError, llm_error_handler)


# ---------------------------------------------------------------------------
# Health & readiness probes
# ---------------------------------------------------------------------------
async def _health() -> HealthResponse:
    return HealthResponse(status="healthy", service=settings.app_name, version=settings.app_version)


async def _ready() -> ReadyResponse:
    """Readiness verifies dependent resources (the job database) are reachable."""
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover
        checks["database"] = f"error: {exc}"

    overall = "ready" if all(v == "ok" for v in checks.values()) else "not-ready"
    return ReadyResponse(status=overall, checks=checks)


# Bare paths for Kubernetes probes (hit directly on the pod).
@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    return await _health()


@app.get("/ready", response_model=ReadyResponse, tags=["ops"])
async def ready() -> ReadyResponse:
    return await _ready()


# `/api`-prefixed aliases so the probes are reachable through the ingress too.
@app.get("/api/health", response_model=HealthResponse, tags=["ops"])
async def api_health() -> HealthResponse:
    return await _health()


@app.get("/api/ready", response_model=ReadyResponse, tags=["ops"])
async def api_ready() -> ReadyResponse:
    return await _ready()


app.include_router(api_router)
