"""Wise endpoints — read-only.

There is no execution endpoint here, and there is no code path in this
application that performs a conversion.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import NotFoundError, ValidationError
from app.api.errors import ProviderError as ApiProviderError
from app.providers.base import ProviderError
from app.schemas.common import MoneyStr, RateStr, Schema, StrictSchema
from app.services import strategy_service as strategies
from app.services import wise_service
from app.services.execution import EXECUTION_REQUIREMENTS

router = APIRouter(prefix="/wise", tags=["wise"])

#: Shown wherever the Wise integration is described.
READ_ONLY_NOTICE = (
    "This application does not automatically convert or transfer money. "
    "The Wise integration is read-only."
)


class WiseStatusOut(Schema):
    configured: bool
    connected: bool
    read_only: bool
    environment: str
    message: str
    profile_id: str
    profiles: list[dict[str, Any]]
    token_hint: str
    latency_ms: int | None
    notice: str = READ_ONLY_NOTICE


class WiseCredentialsIn(StrictSchema):
    #: Empty or null removes the stored token.
    api_token: str | None = None
    profile_id: str | None = None
    source_balance_id: str | None = None
    target_balance_id: str | None = None
    environment: str | None = None
    enabled: bool | None = None


class WiseBalanceOut(Schema):
    balance_id: str
    currency: str
    amount: MoneyStr
    reserved: MoneyStr
    type: str


class WiseConversionOut(Schema):
    reference: str
    source_currency: str
    target_currency: str
    source_amount: MoneyStr
    target_amount: MoneyStr
    rate: RateStr | None
    fee: MoneyStr | None
    fee_currency: str
    executed_at: datetime | None
    description: str


class WiseQuoteOut(Schema):
    rate: RateStr
    source_amount: MoneyStr
    target_amount: MoneyStr
    fee: MoneyStr
    fee_currency: str
    expires_at: datetime | None
    authenticated: bool
    #: Always false in this application: it never transacts on a quote.
    executable_here: bool = False
    note: str


class ReconcileOut(Schema):
    dry_run: bool
    fetched: int
    matched: int
    imported: int
    skipped_other_pair: int
    errors: list[str]
    imported_references: list[str]
    matched_references: list[str]


class ExecutionPolicyOut(Schema):
    execution_enabled: bool = False
    message: str
    requirements_for_any_future_module: list[str]


@router.get("/status", response_model=WiseStatusOut, summary="Wise connection status")
async def wise_status(settings: SettingsDep) -> WiseStatusOut:
    result = await wise_service.status(settings)
    return WiseStatusOut(
        configured=result.configured,
        connected=result.connected,
        read_only=result.read_only,
        environment=result.environment,
        message=result.message,
        profile_id=result.profile_id,
        profiles=result.profiles,
        token_hint=result.token_hint,
        latency_ms=result.latency_ms,
    )


@router.post("/test", response_model=WiseStatusOut, summary="Test the connection")
async def wise_test(settings: SettingsDep) -> WiseStatusOut:
    """Try a real call and report exactly which part failed."""
    return await wise_status(settings)


@router.put("/credentials", response_model=WiseStatusOut, summary="Store credentials")
async def set_credentials(
    payload: WiseCredentialsIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> WiseStatusOut:
    """Save the Wise token and non-secret settings.

    The token goes to the encrypted secret store, never to the database, the
    audit trail or any API response.
    """
    from app.services import settings_service

    if payload.api_token is not None:
        await wise_service.store_credentials(session, api_token=payload.api_token, actor=actor)

    updates: dict[str, Any] = {}
    for field_name in ("profile_id", "source_balance_id", "target_balance_id", "environment"):
        value = getattr(payload, field_name)
        if value is not None:
            updates[field_name] = value
    if payload.enabled is not None:
        updates["enabled"] = payload.enabled

    if updates:
        # The sub-model is revalidated, then handed to patch_section as a model
        # rather than a raw dict, so no unvalidated value reaches a Decimal field.
        wise_settings = type(settings.providers.wise).model_validate(
            {**settings.providers.wise.model_dump(), **updates}
        )
        settings = await settings_service.patch_section(
            session, "providers", {"wise": wise_settings}, actor=actor
        )

    return await wise_status(settings)


@router.delete("/credentials", response_model=WiseStatusOut, summary="Remove credentials")
async def delete_credentials(
    session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> WiseStatusOut:
    await wise_service.store_credentials(session, api_token=None, actor=actor)
    return await wise_status(settings)


@router.get("/balances", response_model=list[WiseBalanceOut], summary="Read balances")
async def wise_balances(settings: SettingsDep) -> list[WiseBalanceOut]:
    try:
        rows = await wise_service.balances(settings)
    except ProviderError as exc:
        raise ApiProviderError(exc.message) from exc
    return [WiseBalanceOut.model_validate(row.__dict__) for row in rows]


@router.get("/transactions", response_model=list[WiseConversionOut], summary="Read conversions")
async def wise_transactions(
    settings: SettingsDep,
    days: int = Query(default=90, ge=1, le=730),
    balance_id: str | None = None,
) -> list[WiseConversionOut]:
    try:
        rows = await wise_service.transactions(settings, balance_id=balance_id, days=days)
    except ProviderError as exc:
        raise ApiProviderError(exc.message) from exc
    return [WiseConversionOut.model_validate(row.__dict__) for row in rows]


@router.post("/quote", response_model=WiseQuoteOut, summary="Quote for fee estimation")
async def wise_quote(
    settings: SettingsDep,
    source_amount: Decimal = Query(..., gt=0),
) -> WiseQuoteOut:
    """Create a quote purely to estimate fees."""
    try:
        result = await wise_service.quote(
            settings,
            source_amount,
            source=settings.general.source_currency,
            target=settings.general.target_currency,
        )
    except ProviderError as exc:
        raise ApiProviderError(exc.message) from exc

    return WiseQuoteOut(
        rate=result.rate,
        source_amount=result.source_amount,
        target_amount=result.target_amount,
        fee=result.fee,
        fee_currency=result.fee_currency,
        expires_at=result.expires_at,
        authenticated=result.authenticated,
        note=(
            "An estimate for planning. "
            + (
                "This quote was created against your Wise profile, but this application "
                "will not act on it."
                if result.authenticated
                else "This quote was not authenticated against your profile and is not executable."
            )
        ),
    )


@router.post("/reconcile", response_model=ReconcileOut, summary="Reconcile with Wise")
async def wise_reconcile(
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    strategy_id: int | None = None,
    days: int = Query(default=90, ge=1, le=730),
    commit: bool = Query(default=False, description="Set true to import unmatched conversions"),
) -> ReconcileOut:
    """Compare Wise's completed conversions with the records held here.

    Defaults to a dry run. Matching is on the Wise reference, so running it
    twice imports nothing twice.
    """
    strategy = (
        await strategies.get_strategy(session, strategy_id)
        if strategy_id is not None
        else await strategies.active_strategy(session, settings)
    )
    if strategy is None:
        raise NotFoundError("There is no strategy to reconcile against.")

    result = await wise_service.reconcile(
        session, strategy, settings, days=days, commit=commit, actor=actor
    )
    if result.errors and result.fetched == 0:
        raise ApiProviderError("Wise could not be read.", details={"errors": result.errors})
    return ReconcileOut(
        dry_run=result.dry_run,
        fetched=result.fetched,
        matched=result.matched,
        imported=result.imported,
        skipped_other_pair=result.skipped_other_pair,
        errors=result.errors,
        imported_references=result.imported_references,
        matched_references=result.matched_references,
    )


@router.get("/execution-policy", response_model=ExecutionPolicyOut, summary="Execution policy")
async def execution_policy() -> ExecutionPolicyOut:
    """State plainly that execution is not implemented.

    There is no counterpart endpoint that performs a conversion.
    """
    return ExecutionPolicyOut(
        message=(
            "This application does not execute conversions. No endpoint exists that would, "
            "and the only shipped executor refuses. Create your conversions in Wise; this "
            "app tells you when to and records what happened."
        ),
        requirements_for_any_future_module=list(EXECUTION_REQUIREMENTS),
    )


@router.post("/execute", include_in_schema=False)
async def refuse_execution() -> None:
    """A deliberate dead end.

    Present so that anyone probing for an execution endpoint gets an explicit
    refusal rather than a 404 that might read as "not built yet, try another
    path".
    """
    raise ValidationError(
        "Conversion execution is not implemented and will not be performed. "
        "See GET /api/v1/wise/execution-policy."
    )
