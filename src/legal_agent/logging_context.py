"""Request-scoped logging context using contextvars.

Sets trace_id and user_id per request so every log line includes them automatically.
"""

import contextvars
import logging
import uuid

# Context variables — set per-request by the middleware, read by the logging filter.
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")


def set_request_context(trace_id: str | None = None, user_id: str | None = None) -> None:
    """Set trace_id and user_id for the current async context."""
    trace_id_var.set(trace_id or str(uuid.uuid4()))
    user_id_var.set(user_id or "-")


def clear_request_context() -> None:
    """Reset context vars to defaults."""
    trace_id_var.set("-")
    user_id_var.set("-")


class RequestContextFilter(logging.Filter):
    """Logging filter that injects trace_id and user_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()  # type: ignore[attr-defined]
        record.user_id = user_id_var.get()  # type: ignore[attr-defined]
        return True
