"""Serving the compiled frontend under a dynamic Ingress path.

Home Assistant mounts the add-on at an unpredictable prefix such as
``/api/hassio_ingress/T0k3n/`` and forwards it in the ``X-Ingress-Path`` header.
The bundle is built with relative asset URLs, and this module injects a
``<base href>`` matching the prefix so those relative URLs — and the router's
base path — resolve correctly no matter where the app is mounted.  Nothing in
the application assumes it is served from ``/``.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from app.config import AppConfig
from app.logging_setup import get_logger

log = get_logger(__name__)

_BASE_TAG_RE = re.compile(r"<base[^>]*>", re.IGNORECASE)
_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)

#: Assets are content-hashed by Vite, so they can be cached hard.
_IMMUTABLE_SUFFIXES = {".js", ".css", ".woff", ".woff2", ".svg", ".png", ".ico"}

FALLBACK_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>FX Strategy Manager</title></head>
<body style="font-family:system-ui;padding:2rem">
<h1>FX Strategy Manager</h1>
<p>The compiled frontend bundle was not found. The API is available under
<code>api/v1/</code>, and the OpenAPI documentation under <code>api/docs</code>.</p>
</body></html>
"""


def ingress_base(request: Request) -> str:
    """Return the base path the browser should resolve relative URLs against.

    Always ends with a single trailing slash.
    """
    raw = request.headers.get("X-Ingress-Path", "")
    if not raw:
        return "/"
    return "/" + raw.strip("/") + "/" if raw.strip("/") else "/"


def inject_base_href(html: str, base: str) -> str:
    """Insert (or replace) the ``<base>`` tag in a document head."""
    tag = f'<base href="{base}">'
    if _BASE_TAG_RE.search(html):
        return _BASE_TAG_RE.sub(tag, html, count=1)
    match = _HEAD_RE.search(html)
    if match:
        return html[: match.end()] + tag + html[match.end() :]
    return tag + html


def _is_safe_asset(root: Path, candidate: Path) -> bool:
    """Guard against path traversal out of the bundle directory."""
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    return resolved.is_file() and resolved.is_relative_to(root.resolve())


class FrontendFiles:
    """Serves the SPA bundle with Ingress-aware base paths."""

    def __init__(self, config: AppConfig) -> None:
        self.root = config.static_dir
        self.index_path = self.root / "index.html"

    @property
    def available(self) -> bool:
        return self.index_path.is_file()

    def index(self, request: Request) -> Response:
        base = ingress_base(request)
        if not self.available:
            return HTMLResponse(inject_base_href(FALLBACK_PAGE, base), status_code=200)
        html = self.index_path.read_text(encoding="utf-8")
        return HTMLResponse(
            inject_base_href(html, base),
            headers={"Cache-Control": "no-store"},
        )

    def asset(self, request: Request, path: str) -> Response:
        candidate = self.root / path
        if not _is_safe_asset(self.root, candidate):
            # Unknown paths fall through to the SPA so client-side routes work
            # on a hard refresh.
            return self.index(request)
        headers = {}
        if candidate.suffix in _IMMUTABLE_SUFFIXES and "/assets/" in f"/{path}":
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            headers["Cache-Control"] = "no-cache"
        return FileResponse(candidate, headers=headers)


def robots_txt() -> Response:
    """The dashboard is private; say so even though it is not reachable publicly."""
    return PlainTextResponse("User-agent: *\nDisallow: /\n")
