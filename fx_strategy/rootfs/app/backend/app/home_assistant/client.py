"""Home Assistant REST API client.

Talks to the Supervisor's Core proxy at ``http://supervisor/core/api`` using the
``SUPERVISOR_TOKEN`` the add-on is started with.  The token is never logged and
never returned through this application's own API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import AppConfig, get_config
from app.database import utcnow
from app.logging_setup import get_logger, scrub_text

log = get_logger(__name__)


class HomeAssistantError(RuntimeError):
    """Home Assistant could not be reached, or rejected the request."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class HomeAssistantStatus:
    available: bool
    message: str
    version: str = ""
    notify_services: list[str] = field(default_factory=list)
    latency_ms: int | None = None


class HomeAssistantClient:
    """Minimal client covering what this app needs: status, services, notify."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or get_config()
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        """Whether a Supervisor token was supplied at all."""
        return self._config.supervisor_available

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._config.home_assistant_api,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={
                "Authorization": f"Bearer {self._config.supervisor_token}",
                "Content-Type": "application/json",
            },
        )

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = self._build_client()
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _request(self, method: str, path: str, payload: Any = None) -> Any:
        if not self.configured:
            raise HomeAssistantError(
                "No Supervisor token is available, so Home Assistant cannot be reached. "
                "This is normal outside the add-on container.",
                retryable=False,
            )
        try:
            response = await self.client.request(method, path, json=payload)
        except httpx.TransportError as exc:
            raise HomeAssistantError(
                f"Home Assistant could not be reached: {scrub_text(str(exc))}"
            ) from exc
        if response.status_code in (401, 403):
            raise HomeAssistantError("Home Assistant rejected the add-on's token.", retryable=False)
        if response.status_code == 404:
            raise HomeAssistantError(f"Home Assistant has no endpoint {path}.", retryable=False)
        if response.status_code >= 400:
            raise HomeAssistantError(
                f"Home Assistant returned HTTP {response.status_code} for {path}."
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # -- queries -----------------------------------------------------------

    async def status(self) -> HomeAssistantStatus:
        if not self.configured:
            return HomeAssistantStatus(
                available=False,
                message="No Supervisor token; running outside Home Assistant.",
            )
        started = utcnow()
        try:
            body = await self._request("GET", "/")
            services = await self.notify_services()
        except HomeAssistantError as exc:
            return HomeAssistantStatus(available=False, message=exc.message)
        latency = int((utcnow() - started).total_seconds() * 1000)
        message = body.get("message", "API running.") if isinstance(body, dict) else "API running."
        return HomeAssistantStatus(
            available=True,
            message=str(message),
            notify_services=services,
            latency_ms=latency,
        )

    async def notify_services(self) -> list[str]:
        """Discover the notify services this installation offers.

        Device names are never hard-coded: the list comes from Home Assistant.
        """
        body = await self._request("GET", "/services")
        services: list[str] = []
        for domain in body if isinstance(body, list) else []:
            if not isinstance(domain, dict) or domain.get("domain") != "notify":
                continue
            for name in domain.get("services") or {}:
                services.append(f"notify.{name}")
        return sorted(services)

    async def call_service(self, domain: str, service: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", f"/services/{domain}/{service}", payload)

    async def notify(
        self, service: str, *, title: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        """Send one notification through a ``notify.*`` service."""
        domain, _, name = service.partition(".")
        if domain != "notify" or not name:
            raise HomeAssistantError(
                f"{service!r} is not a notify service; expected something like "
                "notify.mobile_app_your_phone.",
                retryable=False,
            )
        payload: dict[str, Any] = {"title": title, "message": message}
        if data:
            payload["data"] = data
        await self.call_service("notify", name, payload)

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Write a state directly.

        Used only as the fallback when no MQTT broker is available. States
        written this way do not survive a Home Assistant restart, which is why
        MQTT discovery is preferred.
        """
        await self._request(
            "POST",
            f"/states/{entity_id}",
            {"state": state, "attributes": attributes or {}},
        )


_client: HomeAssistantClient | None = None


def get_home_assistant() -> HomeAssistantClient:
    global _client
    if _client is None:
        _client = HomeAssistantClient()
    return _client


def set_home_assistant(client: HomeAssistantClient | None) -> None:
    """Replace the client. Used by tests."""
    global _client
    _client = client
