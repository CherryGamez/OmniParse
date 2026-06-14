"""
API controllers for the document intelligence platform.

Exposes the synchronous and asynchronous extraction endpoints, the async job
status endpoint, and a mock OIDC token endpoint for the demo. Each extraction
endpoint accepts BOTH `multipart/form-data` (direct upload) and
`application/json` (S3 object URI) request bodies.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.context import get_correlation_id, set_correlation_id
from core.security import Principal, create_mock_token, require_roles
from schemas.payloads import (
    AsyncAcceptedResponse,
    ExtractRequest,
    ExtractionResult,
    JobStatus,
    JobStatusResponse,
    SourceType,
    SyncExtractResponse,
    TokenRequest,
    TokenResponse,
)
from services.extraction_pipeline import pipeline
from services.job_repository import JobModel, JobRepository, SessionLocal

logger = logging.getLogger("api")
settings = get_settings()

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------
async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Request normalization (multipart OR json)
# ---------------------------------------------------------------------------
class _NormalizedInput:
    """Container for a parsed extraction request regardless of content type."""

    def __init__(
        self,
        *,
        file_bytes: Optional[bytes],
        filename: Optional[str],
        s3_uri: Optional[str],
        callback_url: Optional[str],
        instructions: Optional[str],
    ) -> None:
        self.file_bytes = file_bytes
        self.filename = filename
        self.s3_uri = s3_uri
        self.callback_url = callback_url
        self.instructions = instructions

    @property
    def source_type(self) -> str:
        return "s3" if self.s3_uri else "upload"

    def validate(self) -> None:
        if not self.file_bytes and not self.s3_uri:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide either a file upload (multipart) or an 's3Uri' (JSON).",
            )


async def _parse_request(request: Request) -> _NormalizedInput:
    """Read the body as JSON or multipart and normalize into one shape."""
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        body = await request.json()
        return _NormalizedInput(
            file_bytes=None,
            filename=body.get("filename"),
            s3_uri=body.get("s3Uri"),
            callback_url=body.get("callbackUrl"),
            instructions=body.get("instructions"),
        )

    # Otherwise treat as multipart/form-data.
    form = await request.form()
    upload = form.get("file")
    file_bytes: Optional[bytes] = None
    filename: Optional[str] = None
    if upload is not None and hasattr(upload, "read"):
        file_bytes = await upload.read()
        filename = getattr(upload, "filename", None)

    return _NormalizedInput(
        file_bytes=file_bytes,
        filename=filename or form.get("filename"),
        s3_uri=form.get("s3_uri") or form.get("s3Uri"),
        callback_url=form.get("callback_url") or form.get("callbackUrl"),
        instructions=form.get("instructions"),
    )


# ---------------------------------------------------------------------------
# Auth (mock OIDC token mint) - demo convenience
# ---------------------------------------------------------------------------
@router.post("/auth/token", response_model=TokenResponse, tags=["auth"])
async def issue_token(req: TokenRequest) -> TokenResponse:
    """Mint a short-lived mock OIDC bearer token for local testing."""
    token = create_mock_token(req.sub, req.roles, expires_minutes=60)
    return TokenResponse(accessToken=token, expiresIn=3600, roles=req.roles)


# ---------------------------------------------------------------------------
# Project documents (PRD / TRD / App Flow) — served to the UI's Docs view
# ---------------------------------------------------------------------------
_DOCS_DIR = Path(__file__).resolve().parents[2] / "documents"
_DOC_FILES: dict[str, tuple[str, str]] = {
    "prd": ("PRD.md", "Product Requirement Document"),
    "trd": ("TRD.md", "Technical Requirement Document"),
    "app-flow": ("APP_FLOW.md", "App Flow Document"),
    "benefits": ("BENEFITS.md", "Benefits & Token Economics"),
}


@router.get("/documents", tags=["docs"], summary="List project documents")
async def list_documents() -> list[dict[str, str]]:
    return [
        {"id": doc_id, "title": title, "filename": filename}
        for doc_id, (filename, title) in _DOC_FILES.items()
    ]


@router.get("/documents/{doc_id}", tags=["docs"], summary="Get a project document")
async def get_document(doc_id: str) -> dict[str, str]:
    entry = _DOC_FILES.get(doc_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{doc_id}' not found."
        )
    filename, title = entry
    path = _DOCS_DIR / filename
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document file '{filename}' is missing on the server.",
        )
    return {"id": doc_id, "title": title, "content": path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# SYNC extraction
# ---------------------------------------------------------------------------
@router.post(
    "/extract/sync",
    response_model=SyncExtractResponse,
    tags=["extraction"],
    summary="Synchronously extract structured JSON from a document",
)
async def extract_sync(
    request: Request,
    principal: Principal = Depends(require_roles("extractor", "admin")),
) -> SyncExtractResponse:
    """Run the full pipeline inline and return the result. For small documents."""
    data = await _parse_request(request)
    data.validate()

    if data.file_bytes and len(data.file_bytes) > settings.max_sync_file_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds the {settings.max_sync_file_mb}MB sync limit. "
                "Use POST /api/v1/extract/async instead."
            ),
        )

    correlation_id = get_correlation_id()
    logger.info(
        "Sync extraction requested",
        extra={"extra_fields": {"sub": principal.sub, "sourceType": data.source_type}},
    )

    result = await pipeline.run(
        correlation_id=correlation_id,
        file_bytes=data.file_bytes,
        filename=data.filename,
        s3_uri=data.s3_uri,
        instructions=data.instructions,
    )
    return SyncExtractResponse(result=ExtractionResult(**result))


# ---------------------------------------------------------------------------
# ASYNC extraction
# ---------------------------------------------------------------------------
async def _fire_callback(callback_url: Optional[str], payload: dict[str, Any]) -> None:
    """Best-effort POST of the result back to the caller's callback URL."""
    if not callback_url:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                callback_url,
                json=json.loads(json.dumps(payload, default=str)),
                headers={"X-Correlation-Id": payload.get("correlationId", "-")},
            )
        logger.info(
            "Callback delivered",
            extra={"extra_fields": {"callbackUrl": callback_url, "status": payload.get("status")}},
        )
    except Exception as exc:  # callbacks must never crash the worker
        logger.warning(
            "Callback delivery failed",
            extra={"extra_fields": {"callbackUrl": callback_url, "error": str(exc)}},
        )


async def _process_job(job_id: str, correlation_id: str, data: _NormalizedInput) -> None:
    """Background worker that executes the pipeline and updates job state."""
    set_correlation_id(correlation_id)

    async with SessionLocal() as session:
        await JobRepository(session).update_status(job_id, "PROCESSING")

    try:
        result = await pipeline.run(
            correlation_id=correlation_id,
            file_bytes=data.file_bytes,
            filename=data.filename,
            s3_uri=data.s3_uri,
            instructions=data.instructions,
        )
        async with SessionLocal() as session:
            await JobRepository(session).set_result(job_id, result)
        await _fire_callback(
            data.callback_url,
            {"jobId": job_id, "status": "COMPLETED", "correlationId": correlation_id, "result": result},
        )
    except Exception as exc:
        logger.exception("Async job failed", extra={"extra_fields": {"jobId": job_id}})
        async with SessionLocal() as session:
            await JobRepository(session).set_error(job_id, str(exc))
        await _fire_callback(
            data.callback_url,
            {"jobId": job_id, "status": "FAILED", "correlationId": correlation_id, "error": str(exc)},
        )


@router.post(
    "/extract/async",
    response_model=AsyncAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["extraction"],
    summary="Enqueue an async extraction job (202 Accepted)",
)
async def extract_async(
    request: Request,
    background: BackgroundTasks,
    principal: Principal = Depends(require_roles("extractor", "admin")),
    session: AsyncSession = Depends(get_session),
) -> AsyncAcceptedResponse:
    """Accept the document, return a jobId immediately, and process in the background.

    On completion (or failure) the result is POSTed to the optional `callbackUrl`
    — designed to integrate with BPMN orchestration engines like Camunda 8.
    """
    data = await _parse_request(request)
    data.validate()

    correlation_id = get_correlation_id()
    job_id = uuid.uuid4().hex

    await JobRepository(session).create(
        job_id=job_id,
        correlation_id=correlation_id,
        source_type=data.source_type,
        filename=data.filename,
        callback_url=data.callback_url,
    )

    background.add_task(_process_job, job_id, correlation_id, data)
    logger.info(
        "Async job accepted",
        extra={"extra_fields": {"jobId": job_id, "sub": principal.sub}},
    )

    return AsyncAcceptedResponse(
        jobId=job_id,
        status=JobStatus.PENDING,
        statusUrl=f"/api/v1/jobs/{job_id}",
        correlationId=correlation_id,
    )


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------
def _to_job_response(job: JobModel) -> JobStatusResponse:
    result_obj: Optional[ExtractionResult] = None
    if job.result_json:
        result_obj = ExtractionResult(**json.loads(job.result_json))

    return JobStatusResponse(
        jobId=job.id,
        status=JobStatus(job.status),
        correlationId=job.correlation_id,
        sourceType=SourceType(job.source_type) if job.source_type else None,
        filename=job.filename,
        createdAt=job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc),
        updatedAt=job.updated_at if job.updated_at.tzinfo else job.updated_at.replace(tzinfo=timezone.utc),
        callbackUrl=job.callback_url,
        result=result_obj,
        error=job.error,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    tags=["extraction"],
    summary="Get the status (and result) of an async job",
)
async def get_job(
    job_id: str,
    principal: Principal = Depends(require_roles("extractor", "admin")),
    session: AsyncSession = Depends(get_session),
) -> JobStatusResponse:
    job = await JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found."
        )
    return _to_job_response(job)
