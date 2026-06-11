"""
Request-scoped correlation id propagation.

A `ContextVar` lets every layer of the application (logging, services, callback
emitters) read the active `correlationId` without it having to be threaded
through every function signature. The middleware in `main.py` sets the value at
the start of each request.
"""
import contextvars

# Default of "-" keeps log output aligned when no request is active (startup).
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


def set_correlation_id(value: str) -> None:
    """Bind the correlation id for the current execution context."""
    _correlation_id.set(value)


def get_correlation_id() -> str:
    """Return the correlation id bound to the current execution context."""
    return _correlation_id.get()
