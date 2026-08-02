"""Ingress base-path behaviour.

The add-on is mounted at an unpredictable prefix. These tests pin the rule that
the served document always carries a ``<base href>`` matching the prefix, and
that no absolute origin is baked into the HTML.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.web import inject_base_href

INGRESS_PATH = "/api/hassio_ingress/8CmaXHCoP0dNJcvbeqYKmA"


@pytest.fixture
def bundle(app_config: object) -> Path:
    """A minimal stand-in for the compiled Vite bundle."""
    from app.config import get_config

    static = get_config().static_dir
    (static / "assets").mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<script type="module" src="./assets/index-abc123.js"></script>'
        '</head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (static / "assets" / "index-abc123.js").write_text("export default 1;\n", encoding="utf-8")
    return static


def test_inject_base_href_adds_tag_after_head() -> None:
    html = "<html><head><title>x</title></head><body></body></html>"
    result = inject_base_href(html, "/ingress/abc/")
    assert '<head><base href="/ingress/abc/">' in result


def test_inject_base_href_replaces_existing_tag() -> None:
    html = '<html><head><base href="/"><title>x</title></head></html>'
    result = inject_base_href(html, "/ingress/abc/")
    assert result.count("<base") == 1
    assert '<base href="/ingress/abc/">' in result


async def test_index_served_with_ingress_base(client: AsyncClient, bundle: Path) -> None:
    response = await client.get("/", headers={"X-Ingress-Path": INGRESS_PATH})
    assert response.status_code == 200
    assert f'<base href="{INGRESS_PATH}/">' in response.text
    # Asset references stay relative: nothing hard-codes an origin or "/".
    assert 'src="./assets/index-abc123.js"' in response.text
    assert "http://testserver" not in response.text


async def test_index_without_ingress_header_uses_root(client: AsyncClient, bundle: Path) -> None:
    response = await client.get("/")
    assert '<base href="/">' in response.text


async def test_client_side_route_falls_back_to_index(client: AsyncClient, bundle: Path) -> None:
    response = await client.get("/conversions", headers={"X-Ingress-Path": INGRESS_PATH})
    assert response.status_code == 200
    assert f'<base href="{INGRESS_PATH}/">' in response.text


async def test_assets_are_served_and_cacheable(client: AsyncClient, bundle: Path) -> None:
    response = await client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


async def test_path_traversal_is_refused(client: AsyncClient, bundle: Path) -> None:
    response = await client.get("/../../etc/passwd")
    # Served as the SPA shell rather than the requested file.
    assert response.status_code == 200
    assert "root:" not in response.text


async def test_unknown_api_path_is_a_json_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_missing_bundle_degrades_to_a_readable_page(client: AsyncClient) -> None:
    response = await client.get("/", headers={"X-Ingress-Path": INGRESS_PATH})
    assert response.status_code == 200
    assert "compiled frontend bundle was not found" in response.text
    assert f'<base href="{INGRESS_PATH}/">' in response.text
