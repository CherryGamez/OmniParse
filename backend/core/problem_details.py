"""
RFC 7807 "Problem Details for HTTP APIs" exception handlers.

All error responses share the `application/problem+json` content type and a
consistent body shape, and every problem document echoes back the active
`correlationId` so a failing call can be traced through the logs.
"""
import logging

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.context import get_correlation_id

logger = logging.getLogger("errors")

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Minimal status-code -> human title map (extend as needed).
_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _problem(
    status_code: int,
    detail: str,
    *,
    instance: str | None = None,
    title: str | None = None,
    type_: str = "about:blank",
    extra: dict | None = None,
) -> JSONResponse:
    """Build a standardized RFC 7807 problem response."""
    body: dict[str, object] = {
        "type": type_,
        "title": title or _TITLES.get(status_code, "Error"),
        "status": status_code,
        "detail": detail,
        "correlationId": get_correlation_id(),
    }
    if instance:
        body["instance"] = instance
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_CONTENT_TYPE)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Convert FastAPI/Starlette HTTPExceptions into problem documents."""
    return _problem(exc.status_code, str(exc.detail), instance=request.url.path)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Surface request validation failures as a 422 problem document."""
    return _problem(
        422,
        "One or more request fields failed validation.",
        instance=request.url.path,
        title="Validation Error",
        extra={"errors": jsonable_encoder(exc.errors())},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler so unexpected errors never leak stack traces."""
    logger.exception("Unhandled exception while processing request")
    return _problem(
        500,
        "An unexpected error occurred while processing the request.",
        instance=request.url.path,
    )


def problem_response(
    status_code: int,
    detail: str,
    *,
    instance: str | None = None,
    title: str | None = None,
) -> JSONResponse:
    """Public helper for app-layer custom exception handlers."""
    return _problem(status_code, detail, instance=instance, title=title)
