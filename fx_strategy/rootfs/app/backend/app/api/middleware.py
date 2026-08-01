"""Security headers, request correlation and rate limiting.

Ingress authenticates every request before it reaches this process, so there is
no login layer here.  What remains is protecting the app from a *different*
browser tab: a page on another origin must not be able to drive state-changing
endpoints, and the bundle must not be able to reach the internet.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.logging_setup import get_logger
from app.services.audit import new_correlation_id

log = get_logger(__name__)

Handler = Callable[[Request], Awaitable[Response]]

#: Everything is served from this origin; no CDN, no external font or script.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        # Vite emits a small inline module preload shim, and Recharts injects
        # inline styles for its SVG layers.
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Endpoints that talk to a third party or touch credentials get a tighter limit.
SENSITIVE_PREFIXES = (
    "/api/v1/wise",
    "/api/v1/rates/refresh",
    "/api/v1/rates/import",
    "/api/v1/conversions/import",
    "/api/v1/restore",
    "/api/v1/backup",
    "/api/v1/home-assistant/test-notification",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        correlation = new_correlation_id()
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation
        duration_ms = int((time.monotonic() - started) * 1000)
        if request.url.path.startswith("/api/"):
            log.debug(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
                correlation_id=correlation,
            )
        return response


class CrossOriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests that originate from another site.

    Ingress serves the app from the Home Assistant origin, so a legitimate
    ``Origin`` header always matches the request host.  Requests with no
    ``Origin`` at all (curl, the Home Assistant action layer) are allowed
    through: they are not browser-driven and cannot carry ambient credentials.
    """

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
                origin_host = origin.split("://", 1)[-1]
                if host and origin_host != host:
                    log.warning(
                        "cross_origin_rejected", origin=origin, host=host, path=request.url.path
                    )
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={
                            "error": {
                                "code": "cross_origin",
                                "message": "Cross-origin state change rejected.",
                            }
                        },
                    )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A small fixed-window limiter for sensitive endpoints.

    This protects the upstream provider quota and slows down a runaway
    automation; it is not a defence against a hostile network, which Ingress
    already handles.
    """

    def __init__(self, app: object, limit: int = 30, window_seconds: int = 60) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        path = request.url.path
        if request.method in SAFE_METHODS or not path.startswith(SENSITIVE_PREFIXES):
            return await call_next(request)

        now = time.monotonic()
        bucket = self._hits[path]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry_after = int(self.window - (now - bucket[0])) + 1
            log.warning("rate_limited", path=path, limit=self.limit)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": f"Too many requests to {path}. Try again in {retry_after}s.",
                    }
                },
            )
        bucket.append(now)
        return await call_next(request)
