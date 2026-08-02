"""Wise read-only operations and reconciliation.

Everything here reads.  Nothing in this module, or anywhere reachable from it,
initiates a conversion or a transfer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.strategy import Strategy
from app.providers.base import ProviderConfigurationError, ProviderError
from app.providers.wise import WiseBalance, WiseConversion, WiseProvider, WiseQuote
from app.schemas.conversion import ConversionIn
from app.schemas.settings import Settings
from app.security.secrets import SecretError, get_secret_store
from app.services import audit, conversion_service
from app.services.conversion_service import ConversionError

log = get_logger(__name__)


@dataclass(slots=True)
class WiseStatus:
    configured: bool
    connected: bool
    read_only: bool
    environment: str
    message: str
    profile_id: str = ""
    profiles: list[dict[str, Any]] = field(default_factory=list)
    token_hint: str = ""
    latency_ms: int | None = None


@dataclass(slots=True)
class ReconciliationResult:
    """What a reconciliation run found and did."""

    fetched: int = 0
    matched: int = 0
    imported: int = 0
    skipped_other_pair: int = 0
    errors: list[str] = field(default_factory=list)
    imported_references: list[str] = field(default_factory=list)
    matched_references: list[str] = field(default_factory=list)
    dry_run: bool = True


def build_provider(settings: Settings) -> WiseProvider:
    """Construct the Wise client from settings plus the stored token."""
    store = get_secret_store()
    try:
        token = store.get("wise_api_token")
    except SecretError as exc:
        raise ProviderConfigurationError("wise", str(exc)) from exc
    return WiseProvider(settings.providers.wise, token)


async def status(settings: Settings) -> WiseStatus:
    """Report whether Wise is usable, and say precisely what failed if not."""
    store = get_secret_store()
    wise_settings = settings.providers.wise
    try:
        secret_status = store.status()["wise_api_token"]
    except SecretError as exc:
        return WiseStatus(
            configured=False,
            connected=False,
            read_only=True,
            environment=wise_settings.environment,
            message=str(exc),
        )

    if not secret_status["configured"]:
        return WiseStatus(
            configured=False,
            connected=False,
            read_only=True,
            environment=wise_settings.environment,
            message="No Wise API token is stored.",
        )

    provider = build_provider(settings)
    try:
        health = await provider.health_check()
        profiles: list[dict[str, Any]] = []
        if health.healthy:
            try:
                profiles = await provider.get_profiles()
            except ProviderError as exc:
                # The rate call worked but the profile call did not; that is
                # useful detail, not a reason to report the whole thing broken.
                log.info("wise_profiles_unavailable", error=exc.message)
        return WiseStatus(
            configured=True,
            connected=health.healthy,
            read_only=True,
            environment=wise_settings.environment,
            message=health.message,
            profile_id=wise_settings.profile_id,
            profiles=[
                {"id": str(profile.get("id", "")), "type": profile.get("type", "")}
                for profile in profiles
            ],
            token_hint=secret_status["hint"],
            latency_ms=health.latency_ms,
        )
    finally:
        await provider.aclose()


async def balances(settings: Settings) -> list[WiseBalance]:
    provider = build_provider(settings)
    try:
        return await provider.get_balances()
    finally:
        await provider.aclose()


async def transactions(
    settings: Settings,
    *,
    balance_id: str | None = None,
    days: int = 90,
) -> list[WiseConversion]:
    """Read completed conversions from a balance statement."""
    wise_settings = settings.providers.wise
    resolved = balance_id or wise_settings.source_balance_id
    if not resolved:
        raise ProviderConfigurationError(
            "wise",
            "A source balance ID is required to read conversions. Set it under Settings → Wise.",
        )
    provider = build_provider(settings)
    try:
        end = utcnow()
        return await provider.get_conversions(resolved, end - timedelta(days=days), end)
    finally:
        await provider.aclose()


async def quote(
    settings: Settings, source_amount: Decimal, *, source: str, target: str
) -> WiseQuote:
    """Create a quote for fee estimation.

    The result is an estimate. This application never acts on it, and the UI
    labels an unauthenticated quote as not executable.
    """
    provider = build_provider(settings)
    try:
        return await provider.create_quote(source, target, source_amount)
    finally:
        await provider.aclose()


async def reconcile(
    session: AsyncSession,
    strategy: Strategy,
    settings: Settings,
    *,
    days: int = 90,
    commit: bool = False,
    actor: str = "user",
) -> ReconciliationResult:
    """Compare Wise's completed conversions with what is recorded here.

    Idempotent: matching is on the Wise reference, so re-running imports
    nothing twice.  Nothing is written unless ``commit`` is set.
    """
    result = ReconciliationResult(dry_run=not commit)

    try:
        found = await transactions(settings, days=days)
    except ProviderError as exc:
        result.errors.append(exc.message)
        return result

    result.fetched = len(found)

    for conversion in found:
        if (
            conversion.source_currency != strategy.source_currency
            or conversion.target_currency != strategy.target_currency
        ):
            result.skipped_other_pair += 1
            continue

        existing = await conversion_service.find_duplicate(session, "wise", conversion.reference)
        if existing is not None:
            result.matched += 1
            result.matched_references.append(conversion.reference)
            continue

        if not commit:
            result.imported_references.append(conversion.reference)
            continue

        payload = ConversionIn(
            strategy_id=strategy.id,
            executed_at=conversion.executed_at or utcnow(),
            source_amount=conversion.source_amount,
            target_amount=conversion.target_amount,
            gross_rate=conversion.rate,
            fee_target_currency=(
                conversion.fee
                if conversion.fee is not None
                and conversion.fee_currency == strategy.target_currency
                else None
            ),
            fee_source_currency=(
                conversion.fee
                if conversion.fee is not None
                and conversion.fee_currency == strategy.source_currency
                else None
            ),
            provider="wise",
            provider_transaction_id=conversion.reference,
            notes=conversion.description or "Imported from Wise",
            record_source="wise_api",
            # Reconciliation records history, which may pre-date the current
            # remaining balance.
            correcting_earlier_record=True,
        )
        try:
            await conversion_service.create_conversion(session, strategy, payload, actor=actor)
            result.imported += 1
            result.imported_references.append(conversion.reference)
        except ConversionError as exc:
            result.errors.append(f"{conversion.reference}: {exc}")

    if commit:
        await audit.record(
            session,
            event_type=AuditEventType.RECONCILED,
            entity_type="strategy",
            entity_id=strategy.id,
            message=(
                f"Wise reconciliation: {result.fetched} conversion(s) read, "
                f"{result.matched} already recorded, {result.imported} imported."
            ),
            after={
                "imported": result.imported_references,
                "matched": result.matched_references,
                "errors": result.errors,
            },
            actor=actor,
        )
    return result


async def store_credentials(
    session: AsyncSession,
    *,
    api_token: str | None,
    actor: str = "user",
) -> None:
    """Save or clear the Wise token.

    The value never reaches the audit trail — only the fact that it changed.
    """
    store = get_secret_store()
    if api_token:
        store.set("wise_api_token", api_token)
        message = "Wise API token stored"
    else:
        store.delete("wise_api_token")
        message = "Wise API token removed"

    await audit.record(
        session,
        event_type=AuditEventType.CREDENTIAL_CHANGED,
        entity_type="credential",
        entity_id="wise_api_token",
        message=message,
        after={"configured": bool(api_token)},
        actor=actor,
    )


def last_reconciliation_window(days: int) -> tuple[datetime, datetime]:
    end = utcnow()
    return end - timedelta(days=days), end
