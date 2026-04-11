"""
Middleware to extract and set trace_id and user_id for logging
"""
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from legal_agent.rag_engine.utils.logger import set_trace_id, set_user_id, clear_context


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to capture X-Trace-Id and x-user-id headers
    and inject them into logging context
    """

    async def dispatch(self, request: Request, call_next):
        # Extract trace_id from header, generate if not present
        trace_id = request.headers.get("x-trace-id") or request.headers.get("X-Trace-Id")
        if not trace_id:
            trace_id = str(uuid.uuid4())

        # Extract user_id from header
        user_id = request.headers.get("x-user-id") or request.headers.get("X-User-Id") or "anonymous"

        # Set in logging context
        set_trace_id(trace_id)
        set_user_id(user_id)

        try:
            # Process request
            response = await call_next(request)

            # Add trace_id to response headers for client tracking
            response.headers["X-Trace-Id"] = trace_id

            return response

        finally:
            # Clear context after request
            clear_context()
