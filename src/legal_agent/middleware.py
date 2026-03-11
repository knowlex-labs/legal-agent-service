"""ASGI middleware for request context (trace ID, user ID)."""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from legal_agent.config import get_settings
from legal_agent.logging_context import clear_request_context, set_request_context


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Extract X-Trace-Id and X-User-Id from request headers and set them in logging context.

    If X-Trace-Id is missing, a new UUID is generated.
    The trace ID is also returned in the response header for client correlation.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())

        if settings.trust_forwarded_headers:
            user_id = request.headers.get("x-user-id") or "-"
        else:
            user_id = request.headers.get("x-user-id") or str(uuid.uuid4())

        set_request_context(trace_id=trace_id, user_id=user_id)

        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            clear_request_context()
