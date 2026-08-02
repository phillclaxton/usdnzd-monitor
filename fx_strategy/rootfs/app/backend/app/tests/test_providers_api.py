"""Configuring the generic provider through the API.

The provider itself is covered in test_providers.py; what matters here is that
a user can set one up, test it, and that the API key never comes back out.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.providers.generic import PRESETS
from app.security.secrets import get_secret_store, reset_secret_store

API_KEY = "generic-provider-key-abcdef123456"


def rate_handler(request: httpx.Request) -> httpx.Response:
    """A stand-in for a vendor returning `{"rates": {"NZD": ...}}`."""
    if "/latest" not in request.url.path:
        return httpx.Response(404, json={"error": "no such path"})
    return httpx.Response(
        200,
        json={"base": "USD", "date": "2026-08-02", "rates": {"NZD": 1.7231}},
    )


def failing_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"message": "invalid api key"})


@pytest.fixture(autouse=True)
def clean_secrets(app_config: object) -> Any:
    reset_secret_store()
    yield
    reset_secret_store()


@pytest.fixture
def stub_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.generic import GenericProvider

    def build(self: GenericProvider) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, transport=httpx.MockTransport(rate_handler)
        )

    monkeypatch.setattr(GenericProvider, "_build_client", build)


@pytest.fixture
def stub_generic_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.generic import GenericProvider

    def build(self: GenericProvider) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, transport=httpx.MockTransport(failing_handler)
        )

    monkeypatch.setattr(GenericProvider, "_build_client", build)


async def test_presets_are_listed(client: AsyncClient) -> None:
    response = await client.get("/api/v1/providers/presets")
    assert response.status_code == 200
    body = response.json()
    assert {row["key"] for row in body} == set(PRESETS)
    # Each entry carries enough to choose between them.
    for row in body:
        assert row["display_name"]
        assert row["base_url"].startswith("https://")
        assert row["notes"]


async def test_a_preset_fills_in_the_configuration(client: AsyncClient) -> None:
    response = await client.post("/api/v1/providers/generic/preset/frankfurter")
    assert response.status_code == 200
    config = response.json()["config"]

    assert config["preset"] == "frankfurter"
    assert config["base_url"] == "https://api.frankfurter.app"
    assert config["rate_json_path"] == "rates.{target}"
    assert config["auth_style"] == "none"


async def test_an_unknown_preset_is_refused_by_name(client: AsyncClient) -> None:
    response = await client.post("/api/v1/providers/generic/preset/not-a-vendor")
    assert response.status_code == 422
    # The message names the alternatives rather than just failing.
    assert "frankfurter" in response.text


async def test_a_preset_stays_editable(client: AsyncClient) -> None:
    """A preset is a starting point, not a constraint."""
    await client.post("/api/v1/providers/generic/preset/frankfurter")
    response = await client.put(
        "/api/v1/providers/generic",
        json={"base_url": "https://rates.example.internal", "display_name": "House rates"},
    )
    assert response.status_code == 200
    config = response.json()["config"]
    assert config["base_url"] == "https://rates.example.internal"
    assert config["display_name"] == "House rates"
    # The rest of the preset survived the edit.
    assert config["rate_json_path"] == "rates.{target}"


async def test_the_api_key_is_stored_but_never_returned(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/providers/generic",
        json={"base_url": "https://api.example.com", "api_key": API_KEY},
    )
    assert response.status_code == 200
    assert API_KEY not in response.text

    # It reached the encrypted store.
    assert get_secret_store().get("generic_api_key") == API_KEY

    # And only a hint is exposed.
    status = response.json()["status"]
    assert status["key_hint"].endswith(API_KEY[-4:])
    assert status["key_hint"] != API_KEY

    fetched = await client.get("/api/v1/providers/generic")
    assert API_KEY not in fetched.text


async def test_the_api_key_can_be_removed(client: AsyncClient) -> None:
    await client.put("/api/v1/providers/generic", json={"api_key": API_KEY})
    response = await client.delete("/api/v1/providers/generic/credentials")
    assert response.status_code == 200
    assert get_secret_store().get("generic_api_key") is None
    assert response.json()["status"]["key_hint"] == ""


async def test_status_says_what_is_missing(client: AsyncClient) -> None:
    empty = (await client.get("/api/v1/providers/generic")).json()["status"]
    assert empty["configured"] is False
    assert "base URL" in empty["message"]

    await client.put(
        "/api/v1/providers/generic",
        json={"base_url": "https://api.example.com", "auth_style": "header"},
    )
    needs_key = (await client.get("/api/v1/providers/generic")).json()["status"]
    assert needs_key["configured"] is False
    assert "API key" in needs_key["message"]


async def test_a_successful_test_reports_the_rate(client: AsyncClient, stub_generic: None) -> None:
    await client.post("/api/v1/providers/generic/preset/frankfurter")
    response = await client.post("/api/v1/providers/generic/test")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    # The rate crosses the API as a string, at full scale.
    assert body["rate"] == "1.72310000"
    assert "1 USD = 1.72310000 NZD" in body["message"]


async def test_a_failing_test_explains_itself_without_erroring(
    client: AsyncClient, stub_generic_failing: None
) -> None:
    """The button has to tell the user what is wrong, not return a 500."""
    await client.post("/api/v1/providers/generic/preset/frankfurter")
    response = await client.post("/api/v1/providers/generic/test")

    assert response.status_code == 200
    body = response.json()
    assert body["rate"] is None
    assert body["message"]
    assert "401" in body["message"] or "invalid api key" in body["message"].lower()


async def test_the_key_never_reaches_the_audit_trail(client: AsyncClient) -> None:
    await client.put("/api/v1/providers/generic", json={"api_key": API_KEY})
    events = await client.get("/api/v1/audit-events")
    assert events.status_code == 200
    assert API_KEY not in events.text
    # The fact of the change is recorded.
    assert "generic_api_key" in events.text


async def test_enabling_the_provider_is_persisted(client: AsyncClient) -> None:
    await client.put(
        "/api/v1/providers/generic",
        json={"base_url": "https://api.frankfurter.app", "auth_style": "none", "enabled": True},
    )
    settings = (await client.get("/api/v1/settings")).json()
    assert settings["providers"]["generic"]["enabled"] is True
    assert settings["providers"]["generic"]["base_url"] == "https://api.frankfurter.app"
