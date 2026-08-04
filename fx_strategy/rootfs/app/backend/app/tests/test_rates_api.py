"""Rate endpoint tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.models.rate import RateSample
from app.providers.base import QuoteType


async def test_current_rate_is_explicit_about_having_nothing_yet(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/rates/current")).json()
    assert body["status"] == "unavailable"
    assert body["rate"] is None
    assert "No rate has been collected yet" in body["message"]


async def test_manual_entry_then_current_rate(client: AsyncClient) -> None:
    response = await client.post("/api/v1/rates/manual", json={"rate": "1.7604", "note": "test"})
    assert response.status_code == 200
    assert response.json()["rate"] == "1.76040000"

    body = (await client.get("/api/v1/rates/current")).json()
    assert body["rate"] == "1.76040000"
    assert body["status"] == "live"
    assert body["provider"] == "manual"
    assert body["quote_label"] == "Manually entered rate"
    assert body["source_currency"] == "USD"


async def test_manual_entry_rejects_a_non_positive_rate(client: AsyncClient) -> None:
    response = await client.post("/api/v1/rates/manual", json={"rate": "0"})
    assert response.status_code == 422


async def test_manual_entry_rejects_an_unknown_currency(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/rates/manual", json={"rate": "1.75", "target_currency": "XYZ"}
    )
    assert response.status_code == 422


async def test_refresh_reports_provider_failure_as_an_error(client: AsyncClient) -> None:
    """With no manual rate and no API provider, a refresh must fail visibly."""
    response = await client.post("/api/v1/rates/refresh")
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "provider_error"
    assert "manual" in body["error"]["details"]["attempted"]


async def test_refresh_uses_the_manual_fallback(client: AsyncClient) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.7500"})
    response = await client.post("/api/v1/rates/refresh")
    assert response.status_code == 200
    assert response.json()["provider"] == "manual"


async def test_history_returns_samples_for_a_short_range(
    client: AsyncClient, session: AsyncSession
) -> None:
    now = utcnow()
    for index, rate in enumerate(["1.70", "1.72", "1.76"]):
        session.add(
            RateSample(
                provider="test",
                source_currency="USD",
                target_currency="NZD",
                rate=Decimal(rate),
                rate_numeric=float(rate),
                quote_type=str(QuoteType.MID_MARKET),
                retrieved_at=now - timedelta(hours=3 - index),
            )
        )
    await session.commit()

    body = (await client.get("/api/v1/rates/history?range=24h")).json()
    assert body["resolution"] == "sample"
    assert [point["rate"] for point in body["points"]] == [
        "1.70000000",
        "1.72000000",
        "1.76000000",
    ]
    assert body["high"] == "1.76000000"
    assert body["low"] == "1.70000000"
    assert body["average"] == "1.72666667"


async def test_history_rejects_an_unknown_range(client: AsyncClient) -> None:
    response = await client.get("/api/v1/rates/history?range=17y")
    assert response.status_code == 422
    assert "Unknown range" in response.json()["error"]["message"]


async def test_history_rejects_a_reversed_custom_range(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/rates/history?start=2026-08-01T00:00:00Z&end=2026-07-01T00:00:00Z"
    )
    assert response.status_code == 422


async def test_provider_health_lists_every_provider(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/rates/providers")).json()
    names = {row["provider"] for row in body}
    assert {"manual", "simulation", "wise", "generic"} <= names
    wise = next(row for row in body if row["provider"] == "wise")
    assert wise["configured"] is False
    assert "not enabled" in wise["reason"]


async def test_csv_import_previews_before_writing(client: AsyncClient) -> None:
    csv_text = (
        "timestamp,source_currency,target_currency,rate,provider\n"
        "2026-07-01T00:00:00Z,USD,NZD,1.7100,csv\n"
        "2026-07-02T00:00:00Z,USD,NZD,1.7200,csv\n"
        "not-a-date,USD,NZD,1.7300,csv\n"
        "2026-07-03T00:00:00Z,USD,AUD,1.5000,csv\n"
    )
    files = {"file": ("rates.csv", csv_text, "text/csv")}

    preview = (await client.post("/api/v1/rates/import", files=files)).json()
    assert preview["accepted"] == 2
    assert preview["rejected"] == 2
    assert preview["committed"] is False
    assert preview["imported"] == 0
    messages = " ".join(error["message"] for error in preview["errors"])
    assert "Unreadable timestamp" in messages
    assert "different currency pair" in messages

    # Nothing was written by the preview.
    assert (await client.get("/api/v1/rates/history?range=1y")).json()["points"] == []

    committed = (await client.post("/api/v1/rates/import?commit=true", files=files)).json()
    assert committed["imported"] == 2
    assert committed["committed"] is True


async def test_csv_import_rejects_a_missing_column(client: AsyncClient) -> None:
    files = {"file": ("rates.csv", "timestamp,rate\n2026-07-01,1.75\n", "text/csv")}
    response = await client.post("/api/v1/rates/import", files=files)
    assert response.status_code == 422
    assert "Missing required column" in response.json()["error"]["message"]


async def test_csv_export_round_trips_through_the_importer(client: AsyncClient) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.7654"})
    response = await client.get("/api/v1/rates/export?range=24h")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    lines = response.text.strip().splitlines()
    assert lines[0] == "timestamp,source_currency,target_currency,rate,provider"
    assert "1.76540000" in lines[1]

    files = {"file": ("rates.csv", response.text, "text/csv")}
    preview = (await client.post("/api/v1/rates/import", files=files)).json()
    assert preview["accepted"] == 1
    assert preview["rejected"] == 0


async def test_rate_limiting_protects_the_refresh_endpoint(client: AsyncClient) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.75"})
    statuses = [(await client.post("/api/v1/rates/refresh")).status_code for _ in range(35)]
    assert 429 in statuses


# ---------------------------------------------------------------------------
# An unconfigured provider is not a fault
# ---------------------------------------------------------------------------


async def test_the_manual_provider_shows_as_not_configured_not_failing(
    client: AsyncClient,
) -> None:
    """With nothing entered it is unconfigured, whatever the stored row says."""
    rows = (await client.get("/api/v1/rates/providers")).json()
    manual = next(row for row in rows if row["provider"] == "manual")

    assert manual["configured"] is False
    assert manual["consecutive_failures"] == 0
    assert manual["retry_after"] is None
    assert "No manual rate has been entered" in manual["reason"]


async def test_a_historical_failure_on_it_is_not_presented_as_current(
    client: AsyncClient,
) -> None:
    """An installation carrying the old bad row sees the corrected state."""
    from app.database import get_sessionmaker
    from app.services import rate_service

    async with get_sessionmaker()() as session:
        await rate_service.record_provider_failure(
            session, "manual", "No manual rate has been entered yet.", max_backoff=3600
        )
        await session.commit()

    rows = (await client.get("/api/v1/rates/providers")).json()
    manual = next(row for row in rows if row["provider"] == "manual")

    assert manual["configured"] is False
    assert manual["consecutive_failures"] == 0
    assert manual["last_failure_at"] is None


async def test_entering_a_rate_makes_it_configured(client: AsyncClient) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.7200"})

    rows = (await client.get("/api/v1/rates/providers")).json()
    manual = next(row for row in rows if row["provider"] == "manual")
    assert manual["configured"] is True


async def test_an_unconfigured_provider_does_not_make_the_app_report_a_problem(
    client: AsyncClient,
) -> None:
    """The Home Assistant problem sensor and diagnostics both keyed off this."""
    from app.database import get_sessionmaker
    from app.services import backup, publisher, rate_service, settings_service

    async with get_sessionmaker()() as session:
        await rate_service.record_provider_failure(
            session, "manual", "No manual rate has been entered yet.", max_backoff=3600
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        settings = await settings_service.load_settings(session)
        context = await publisher.build_context(session, settings)
        assert context.provider_healthy is True
        assert "All providers healthy" in context.provider_message

        bundle = await backup.diagnostics(session, settings, include_logs=False)
        assert bundle["rates"]["failing_providers"] == []


async def test_a_genuinely_failing_provider_is_still_reported(client: AsyncClient) -> None:
    """The fix must not silence real faults.

    The exclusion is keyed on whether a provider is configured, not on its name,
    so a configured provider that fails is still reported.
    """
    from app.database import get_sessionmaker
    from app.services import publisher, rate_service, settings_service

    # Entering a rate makes the manual provider configured, so a failure
    # recorded against it is a real one.
    await client.post("/api/v1/rates/manual", json={"rate": "1.7200"})

    async with get_sessionmaker()() as session:
        await rate_service.record_provider_failure(
            session, "manual", "something genuinely broke", max_backoff=3600
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        settings = await settings_service.load_settings(session)
        context = await publisher.build_context(session, settings)
        assert context.provider_healthy is False
        assert "manual" in context.provider_message
