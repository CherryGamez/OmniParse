"""
Structured (JSON) logging configuration.

Every emitted log line is a single JSON object that always carries the active
`correlationId`, which makes the logs trivially ingestible by ELK / Loki / Cloud
Logging and lets operators trace a single request end-to-end.
"""
import json
import logging
import sys
from datetime import datetime, timezone

from core.context import get_correlation_id


class JsonLogFormatter(logging.Formatter):
    """Render log records as compact, single-line JSON documents."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": get_correlation_id(),
        }

        # Allow callers to attach arbitrary structured fields via
        # `logger.info("...", extra={"extra_fields": {...}})`.
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger and key uvicorn loggers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Route uvicorn's own loggers through the same JSON handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
