"""
Strictly-typed Pydantic models for every request and response payload.

These models are the public contract of the API: they drive OpenAPI/Swagger
documentation, request validation, and response serialization.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class SourceType(str, Enum):
    """Where the source document originated from."""

    UPLOAD = "upload"
    S3 = "s3"


class JobStatus(str, Enum):
    """Lifecycle states for an asynchronous extraction job."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ExtractRequest(BaseModel):
    """JSON request body for S3-sourced extractions.

    File uploads are sent as `multipart/form-data` instead; this model documents
    the JSON contract used by orchestration engines (e.g. Camunda 8) that submit
    an S3 object URI plus an optional callback URL.
    """

    s3Uri: Optional[str] = Field(
        default=None,
        description="S3 object URI to fetch, e.g. s3://my-bucket/path/to/file.pdf",
        examples=["s3://demo-bucket/contracts/invoice-001.pdf"],
    )
    filename: Optional[str] = Field(
        default=None, description="Optional override for the source filename."
    )
    instructions: Optional[str] = Field(
        default=None,
        description="Optional natural-language extraction instructions / target schema hint.",
        examples=["Extract invoiceNumber, totalAmount, lineItems[] and dueDate."],
    )
    callbackUrl: Optional[str] = Field(
        default=None,
        description="ASYNC only: URL that receives a POST with the result when the job finishes.",
        examples=["https://camunda.example.com/engine-rest/message"],
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class ExtractionResult(BaseModel):
    """The structured output produced by the two-step extraction pipeline."""

    correlationId: str
    sourceType: SourceType
    filename: Optional[str] = None
    markdown: str = Field(description="Intermediate Markdown produced by MarkItDown.")
    markdownChars: int = Field(description="Character length of the Markdown.")
    chunked: bool = Field(description="Whether the Markdown was split into chunks.")
    chunkCount: int = Field(description="Number of chunks sent to the LLM.")
    structured: Union[dict[str, Any], list[Any]] = Field(
        description="Structured JSON extracted by the LLM."
    )
    model: str = Field(description="Model used for extraction (or 'mock').")
    mock: bool = Field(description="True when the deterministic mock extractor was used.")
    ocrUsed: bool = Field(default=False, description="True if OCR was used to read the document.")
    ocrEngine: Optional[str] = Field(
        default=None, description="OCR engine/languages used (e.g. 'paddleocr:latin')."
    )
    processingMs: int = Field(description="End-to-end pipeline processing time in milliseconds.")
    tokensEstimate: int = Field(
        default=0,
        description=(
            "Estimated total tokens (input + output) actually consumed by the LLM "
            "for this extraction. Computed deterministically as char-count / 4."
        ),
    )
    tokensSavedVsRaw: int = Field(
        default=0,
        description=(
            "Estimated tokens SAVED vs. sending the raw document straight to a "
            "vision-LLM (≈1100 input tokens per scanned page). The savings come "
            "from the deterministic MarkItDown / PaddleOCR pre-processing step."
        ),
    )


class SyncExtractResponse(BaseModel):
    """Successful response from the synchronous extraction endpoint."""

    status: JobStatus = JobStatus.COMPLETED
    result: ExtractionResult


class AsyncAcceptedResponse(BaseModel):
    """`202 Accepted` response returned when an async job is enqueued."""

    jobId: str
    status: JobStatus = JobStatus.PENDING
    statusUrl: str = Field(description="Poll this URL for the job's status & result.")
    correlationId: str


class JobStatusResponse(BaseModel):
    """Full state of an asynchronous job, including the result when finished."""

    jobId: str
    status: JobStatus
    correlationId: str
    sourceType: Optional[SourceType] = None
    filename: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime
    callbackUrl: Optional[str] = None
    result: Optional[ExtractionResult] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth (mock OIDC) models
# ---------------------------------------------------------------------------
class TokenRequest(BaseModel):
    """Request body for minting a mock OIDC token (local demo only)."""

    sub: str = Field(default="demo-user", description="Subject / user identifier.")
    roles: list[str] = Field(default_factory=lambda: ["extractor"])


class TokenResponse(BaseModel):
    """A freshly minted mock bearer token."""

    accessToken: str
    tokenType: str = "Bearer"
    expiresIn: int
    roles: list[str]


# ---------------------------------------------------------------------------
# Health / readiness
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, str]
