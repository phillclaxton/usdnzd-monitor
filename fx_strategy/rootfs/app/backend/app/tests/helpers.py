"""Shared test utilities."""

from __future__ import annotations

from typing import Any

from app.home_assistant.client import HomeAssistantError


def all_route_paths(app: Any) -> list[str]:
    """Every path in the application, including routers mounted by include_router.

    ``app.routes`` alone returns the included routers as single opaque objects,
    so a structural assertion over it would inspect about seven paths and quietly
    pass whatever the API actually exposes. This recurses through
    ``original_router`` to reach the real ones.
    """
    paths: list[str] = []

    def walk(routes: Any) -> None:
        for route in routes or []:
            paths.append(getattr(route, "path", ""))
            nested = getattr(route, "routes", None)
            if nested is None:
                inner = getattr(route, "original_router", None)
                nested = getattr(inner, "routes", None)
            if nested:
                walk(nested)

    walk(app.routes)
    return paths


class FakeHomeAssistant:
    """Records notify calls, and can be made to fail on demand."""

    def __init__(self, *, fail: str | None = None, retryable: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.retryable = retryable
        self.configured = True

    async def notify(self, service: str, *, title: str, message: str, data: Any = None) -> None:
        if self.fail:
            raise HomeAssistantError(self.fail, retryable=self.retryable)
        self.calls.append({"service": service, "title": title, "message": message, "data": data})

    async def aclose(self) -> None:
        return None
