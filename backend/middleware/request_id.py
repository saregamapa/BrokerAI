"""Middleware that stamps every request with a unique request_id."""
import uuid
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger("brokerai.access")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        start = time.monotonic()
        response = await call_next(request)
        elapsed = round((time.monotonic() - start) * 1000, 1)
        response.headers["X-Request-Id"] = request_id
        # Skip logging for health endpoints and static files to reduce noise
        path = request.url.path
        if not path.startswith("/health") and not path.startswith("/static"):
            log.info(
                "method=%s path=%s status=%d ms=%s rid=%s",
                request.method,
                path,
                response.status_code,
                elapsed,
                request_id,
            )
        return response
