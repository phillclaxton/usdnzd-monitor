"""Shared HTTP behaviour for network-backed providers.

Retries are bounded and jittered, TLS verification is never disabled, and the
error text that reaches the caller is scrubbed of anything credential-shaped.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.logging_setup import get_logger, scrub_text
from app.providers.base import ProviderResponseError, ProviderUnavailableError

log = get_logger(__name__)

#: Status codes worth retrying: transient upstream problems and rate limits.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableHTTPError(Exception):
    """Internal marker so tenacity retries only what should be retried."""


class HttpProviderMixin:
    """Gives a provider a shared, correctly configured HTTP client."""

    name: str
    _client: httpx.AsyncClient | None = None

    def __init__(self, *, timeout: float = 15.0, base_url: str = "") -> None:
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 10.0)),
            follow_redirects=False,
            # TLS verification stays on. There is no configuration switch for it.
            verify=True,
            headers={"User-Agent": "FX-Strategy-Manager/1.0 (Home Assistant app)"},
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

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        attempts: int = 3,
    ) -> tuple[Any, int]:
        """Perform a request and return ``(parsed_json, latency_ms)``.

        Raises a :class:`~app.providers.base.ProviderError` subclass on failure.
        A failed call is never converted into a fake success.
        """
        started = time.monotonic()
        last_error: Exception | None = None

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential_jitter(initial=1, max=20),
                retry=retry_if_exception_type((RetryableHTTPError, httpx.TransportError)),
                reraise=True,
            ):
                with attempt:
                    try:
                        response = await self.client.request(
                            method, url, params=params, headers=headers, json=json_body
                        )
                    except httpx.TransportError as exc:
                        last_error = exc
                        raise
                    if response.status_code in RETRYABLE_STATUS:
                        last_error = ProviderUnavailableError(
                            self.name,
                            f"{self.name} returned HTTP {response.status_code}",
                        )
                        raise RetryableHTTPError(str(response.status_code))
                    if response.status_code >= 400:
                        raise ProviderUnavailableError(
                            self.name,
                            _describe_http_error(self.name, response),
                            retryable=False,
                        )
                    latency_ms = int((time.monotonic() - started) * 1000)
                    try:
                        return response.json(), latency_ms
                    except ValueError as exc:
                        raise ProviderResponseError(
                            self.name,
                            f"{self.name} returned a response that is not JSON.",
                        ) from exc
        except RetryableHTTPError as exc:
            message = (
                last_error.message
                if isinstance(last_error, ProviderUnavailableError)
                else f"{self.name} is temporarily unavailable (HTTP {exc})"
            )
            raise ProviderUnavailableError(self.name, message) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                self.name, f"{self.name} could not be reached: {scrub_text(str(exc))}"
            ) from exc

        # AsyncRetrying always either returns or raises; this satisfies typing.
        raise ProviderUnavailableError(self.name, f"{self.name} produced no response")


def _describe_http_error(provider: str, response: httpx.Response) -> str:
    """A message that is useful without leaking the request's credentials."""
    hint = {
        401: "the credential was rejected",
        403: "the credential is not permitted to use this endpoint",
        404: "the endpoint path is wrong for this provider",
        422: "the request parameters were rejected",
    }.get(response.status_code, "")
    body = scrub_text(response.text[:200]) if response.text else ""
    detail = f" — {hint}" if hint else ""
    tail = f" Response: {body}" if body else ""
    return f"{provider} returned HTTP {response.status_code}{detail}.{tail}"


def json_path(document: Any, path: str, *, provider: str, target: str = "") -> Any:
    """Read a dotted path out of a JSON document.

    Supports list indices (``data.0.rate``) and ``{target}`` / ``{source}``
    placeholders so one configuration works for providers that key their
    response by currency code.
    """
    expanded = path.format(target=target, source="") if "{" in path else path
    current = document
    for segment in [part for part in expanded.split(".") if part]:
        if isinstance(current, list):
            try:
                current = current[int(segment)]
                continue
            except (ValueError, IndexError) as exc:
                raise ProviderResponseError(
                    provider, f"response has no element at '{expanded}'"
                ) from exc
        if isinstance(current, dict):
            if segment not in current:
                raise ProviderResponseError(
                    provider,
                    f"response has no field '{expanded}'; "
                    f"available at this level: {', '.join(sorted(current)[:8]) or '(none)'}",
                )
            current = current[segment]
            continue
        raise ProviderResponseError(provider, f"cannot read '{expanded}' from the response")
    return current
