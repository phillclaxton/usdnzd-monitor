"""Strategy endpoints."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, status

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import ConflictError, NotFoundError, ValidationError
from app.models.rate import FeeModel
from app.models.strategy import Strategy, StrategyStatus
from app.schemas.common import Message
from app.schemas.strategy import (
    AllocationIssue,
    FeeModelIn,
    FeeModelOut,
    ScenariosOut,
    StrategyIn,
    StrategyOut,
    StrategySummaryOut,
    TemplateOut,
    ValidationOut,
)
from app.services import audit, rate_service, settings_service, summary_service
from app.services import strategy_service as strategies
from app.services.strategy_service import StrategyError

router = APIRouter(tags=["strategies"])


async def _load(session: SessionDep, strategy_id: int) -> Strategy:
    strategy = await strategies.get_strategy(session, strategy_id)
    if strategy is None:
        raise NotFoundError(f"Strategy {strategy_id} does not exist.")
    return strategy


def _validation_out(report: object) -> ValidationOut:
    from app.services.calculations import AllocationReport

    assert isinstance(report, AllocationReport)
    return ValidationOut(
        valid=report.valid,
        total_allocated=report.total_allocated,
        unallocated=report.unallocated,
        percentage_total=report.percentage_total,
        issues=[
            *[AllocationIssue(severity="error", message=message) for message in report.errors],
            *[AllocationIssue(severity="warning", message=message) for message in report.warnings],
        ],
    )


# ---------------------------------------------------------------------------
# Templates and fee models
# ---------------------------------------------------------------------------


@router.get("/strategy-templates", response_model=list[TemplateOut], summary="Ladder templates")
async def list_templates(session: SessionDep, settings: SettingsDep) -> list[TemplateOut]:
    current = await rate_service.latest_sample(
        session, settings.general.source_currency, settings.general.target_currency
    )
    return [
        TemplateOut.model_validate(template)
        for template in strategies.templates(current.rate if current else None)
    ]


@router.get("/fee-models", response_model=list[FeeModelOut], summary="List fee models")
async def list_fee_models(session: SessionDep) -> list[FeeModelOut]:
    from sqlalchemy import select

    rows = (await session.execute(select(FeeModel).order_by(FeeModel.name))).scalars().all()
    return [FeeModelOut.model_validate(row) for row in rows]


@router.post(
    "/fee-models",
    response_model=FeeModelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a fee model",
)
async def create_fee_model(
    payload: FeeModelIn, session: SessionDep, actor: ActorDep
) -> FeeModelOut:
    model = FeeModel(**payload.model_dump())
    session.add(model)
    await session.flush()
    await audit.record(
        session,
        event_type="created",
        entity_type="fee_model",
        entity_id=model.id,
        message=f"Fee model '{model.name}' created",
        after=payload.model_dump(),
        actor=actor,
    )
    return FeeModelOut.model_validate(model)


@router.delete("/fee-models/{fee_model_id}", response_model=Message, summary="Delete a fee model")
async def delete_fee_model(fee_model_id: int, session: SessionDep, actor: ActorDep) -> Message:
    from sqlalchemy import select

    model = await session.get(FeeModel, fee_model_id)
    if model is None:
        raise NotFoundError(f"Fee model {fee_model_id} does not exist.")
    in_use = (
        (await session.execute(select(Strategy).where(Strategy.fee_model_id == fee_model_id)))
        .scalars()
        .first()
    )
    if in_use is not None:
        raise ConflictError(
            f"Fee model '{model.name}' is used by strategy '{in_use.name}'. "
            "Change that strategy's fee model first."
        )
    name = model.name
    await session.delete(model)
    await audit.record(
        session,
        event_type="deleted",
        entity_type="fee_model",
        entity_id=fee_model_id,
        message=f"Fee model '{name}' deleted",
        actor=actor,
    )
    return Message(message=f"Fee model '{name}' deleted.")


# ---------------------------------------------------------------------------
# Strategy CRUD
# ---------------------------------------------------------------------------


@router.get("/strategies", response_model=list[StrategyOut], summary="List strategies")
async def list_strategies(session: SessionDep, include_archived: bool = False) -> list[StrategyOut]:
    rows = await strategies.list_strategies(session, include_archived=include_archived)
    return [StrategyOut.model_validate(row) for row in rows]


@router.post(
    "/strategies",
    response_model=StrategyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a strategy",
)
async def create_strategy(
    payload: StrategyIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> StrategyOut:
    try:
        strategy = await strategies.create_strategy(session, payload, actor=actor)
    except StrategyError as exc:
        raise ValidationError(str(exc)) from exc

    # The first strategy created becomes the one the dashboard follows.
    if settings.general.active_strategy_id is None:
        await settings_service.patch_section(
            session, "general", {"active_strategy_id": strategy.id}, actor=actor
        )
    return StrategyOut.model_validate(strategy)


@router.get("/strategies/{strategy_id}", response_model=StrategyOut, summary="Read a strategy")
async def read_strategy(strategy_id: int, session: SessionDep) -> StrategyOut:
    return StrategyOut.model_validate(await _load(session, strategy_id))


@router.put("/strategies/{strategy_id}", response_model=StrategyOut, summary="Update a strategy")
async def update_strategy(
    strategy_id: int, payload: StrategyIn, session: SessionDep, actor: ActorDep
) -> StrategyOut:
    strategy = await _load(session, strategy_id)
    if strategy.status == str(StrategyStatus.ARCHIVED):
        raise ConflictError("An archived strategy cannot be edited. Duplicate it instead.")
    try:
        updated = await strategies.update_strategy(session, strategy, payload, actor=actor)
    except StrategyError as exc:
        raise ValidationError(str(exc)) from exc
    return StrategyOut.model_validate(updated)


@router.delete("/strategies/{strategy_id}", response_model=Message, summary="Delete a strategy")
async def delete_strategy(strategy_id: int, session: SessionDep, actor: ActorDep) -> Message:
    """Delete a strategy, or archive it when it carries financial records."""
    strategy = await _load(session, strategy_id)
    had_conversions = bool(strategy.conversions)
    await strategies.delete_strategy(session, strategy, actor=actor)
    return Message(
        message=(
            "The strategy has recorded conversions, so it was archived rather than deleted. "
            "Its history stays intact."
            if had_conversions
            else "Strategy deleted."
        )
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/strategies/{strategy_id}/activate", response_model=StrategyOut, summary="Activate")
async def activate_strategy(strategy_id: int, session: SessionDep, actor: ActorDep) -> StrategyOut:
    strategy = await _load(session, strategy_id)
    try:
        await strategies.activate(session, strategy, actor=actor)
    except StrategyError as exc:
        raise ValidationError(str(exc)) from exc
    return StrategyOut.model_validate(strategy)


@router.post("/strategies/{strategy_id}/pause", response_model=StrategyOut, summary="Pause")
async def pause_strategy(strategy_id: int, session: SessionDep, actor: ActorDep) -> StrategyOut:
    strategy = await _load(session, strategy_id)
    await strategies.set_status(session, strategy, StrategyStatus.PAUSED, actor=actor)
    return StrategyOut.model_validate(strategy)


@router.post("/strategies/{strategy_id}/resume", response_model=StrategyOut, summary="Resume")
async def resume_strategy(strategy_id: int, session: SessionDep, actor: ActorDep) -> StrategyOut:
    strategy = await _load(session, strategy_id)
    if strategy.status != str(StrategyStatus.PAUSED):
        raise ConflictError("Only a paused strategy can be resumed.")
    await strategies.set_status(session, strategy, StrategyStatus.ACTIVE, actor=actor)
    return StrategyOut.model_validate(strategy)


@router.post("/strategies/{strategy_id}/complete", response_model=StrategyOut, summary="Complete")
async def complete_strategy(strategy_id: int, session: SessionDep, actor: ActorDep) -> StrategyOut:
    strategy = await _load(session, strategy_id)
    await strategies.set_status(session, strategy, StrategyStatus.COMPLETED, actor=actor)
    return StrategyOut.model_validate(strategy)


@router.post(
    "/strategies/{strategy_id}/duplicate",
    response_model=StrategyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate",
)
async def duplicate_strategy(
    strategy_id: int, session: SessionDep, actor: ActorDep, name: str | None = None
) -> StrategyOut:
    strategy = await _load(session, strategy_id)
    copy = await strategies.duplicate(session, strategy, name=name, actor=actor)
    return StrategyOut.model_validate(copy)


# ---------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------


@router.get(
    "/strategies/{strategy_id}/summary",
    response_model=StrategySummaryOut,
    summary="Dashboard summary",
)
async def strategy_summary(
    strategy_id: int, session: SessionDep, settings: SettingsDep
) -> StrategySummaryOut:
    strategy = await _load(session, strategy_id)
    return await summary_service.build_summary(session, strategy, settings)


@router.get("/strategies/{strategy_id}/validate", response_model=ValidationOut, summary="Validate")
async def validate_strategy(strategy_id: int, session: SessionDep) -> ValidationOut:
    strategy = await _load(session, strategy_id)
    return _validation_out(strategies.recalculate_allocations(strategy))


@router.get("/strategies/{strategy_id}/scenarios", response_model=ScenariosOut, summary="Scenarios")
async def strategy_scenarios(
    strategy_id: int,
    session: SessionDep,
    settings: SettingsDep,
    periods: int = Query(default=4, ge=1, le=60),
    custom_rate: Decimal | None = Query(default=None),
) -> ScenariosOut:
    strategy = await _load(session, strategy_id)
    if custom_rate is not None and custom_rate <= 0:
        raise ValidationError("A comparison rate must be greater than zero.")
    return await summary_service.build_scenarios(
        session, strategy, settings, periods=periods, custom_rate=custom_rate
    )


@router.get("/summary", response_model=StrategySummaryOut, summary="Active strategy summary")
async def active_summary(session: SessionDep, settings: SettingsDep) -> StrategySummaryOut:
    """The dashboard's single entry point when no strategy ID is known."""
    strategy = await strategies.active_strategy(session, settings)
    if strategy is None:
        raise NotFoundError("No strategy exists yet. Create one to see the dashboard.")
    return await summary_service.build_summary(session, strategy, settings)
