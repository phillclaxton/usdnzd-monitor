"""Tranche endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import ActorDep, SessionDep
from app.api.errors import ConflictError, NotFoundError, ValidationError
from app.api.v1.strategies import _validation_out
from app.database import utcnow
from app.models.audit import AuditEventType
from app.models.strategy import Strategy, Tranche, TrancheStatus
from app.schemas.common import Message
from app.schemas.strategy import TrancheIn, TrancheOut, TrancheReorder, ValidationOut
from app.services import audit
from app.services import strategy_service as strategies

router = APIRouter(tags=["tranches"])


async def _strategy(session: SessionDep, strategy_id: int) -> Strategy:
    strategy = await strategies.get_strategy(session, strategy_id)
    if strategy is None:
        raise NotFoundError(f"Strategy {strategy_id} does not exist.")
    return strategy


async def _tranche(session: SessionDep, tranche_id: int) -> Tranche:
    tranche = await session.get(Tranche, tranche_id)
    if tranche is None:
        raise NotFoundError(f"Tranche {tranche_id} does not exist.")
    return tranche


@router.get(
    "/strategies/{strategy_id}/tranches",
    response_model=list[TrancheOut],
    summary="List tranches",
)
async def list_tranches(strategy_id: int, session: SessionDep) -> list[TrancheOut]:
    strategy = await _strategy(session, strategy_id)
    return [
        TrancheOut.model_validate(tranche)
        for tranche in sorted(strategy.tranches, key=lambda item: item.sequence)
    ]


@router.post(
    "/strategies/{strategy_id}/tranches",
    response_model=TrancheOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a tranche",
)
async def add_tranche(
    strategy_id: int, payload: TrancheIn, session: SessionDep, actor: ActorDep
) -> TrancheOut:
    strategy = await _strategy(session, strategy_id)
    if any(tranche.sequence == payload.sequence for tranche in strategy.tranches):
        raise ConflictError(f"Sequence {payload.sequence} is already used in this strategy.")

    tranche = Tranche(
        sequence=payload.sequence,
        name=payload.name or f"Tranche {payload.sequence}",
        allocation_type=payload.allocation_type,
        allocation_value=payload.allocation_value,
        target_rate=payload.target_rate,
        minimum_rate=payload.minimum_rate,
        deadline=payload.deadline,
        intended_for_auto_conversion=payload.intended_for_auto_conversion,
        notifications_enabled=payload.notifications_enabled,
        wise_auto_conversion_reference=payload.wise_auto_conversion_reference,
    )
    strategy.tranches.append(tranche)
    await session.flush()
    strategies.recalculate_allocations(strategy)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="tranche",
        entity_id=tranche.id,
        message=f"Tranche {tranche.sequence} added at target {tranche.target_rate}",
        after=payload.model_dump(mode="json"),
        actor=actor,
    )
    return TrancheOut.model_validate(tranche)


@router.put("/tranches/{tranche_id}", response_model=TrancheOut, summary="Update a tranche")
async def update_tranche(
    tranche_id: int, payload: TrancheIn, session: SessionDep, actor: ActorDep
) -> TrancheOut:
    tranche = await _tranche(session, tranche_id)
    before = {
        "sequence": tranche.sequence,
        "allocation_type": tranche.allocation_type,
        "allocation_value": tranche.allocation_value,
        "target_rate": tranche.target_rate,
        "status": tranche.status,
    }

    if payload.target_rate != tranche.target_rate:
        # A moved target must not stay flagged as reached at the old level.
        tranche.target_first_reached_at = None
        tranche.notification_sent_at = None
        tranche.acknowledged_at = None
        if tranche.status in (str(TrancheStatus.TARGET_REACHED), str(TrancheStatus.ARMED)):
            tranche.status = str(TrancheStatus.PENDING)

    tranche.sequence = payload.sequence
    tranche.name = payload.name or tranche.name
    tranche.allocation_type = payload.allocation_type
    tranche.allocation_value = payload.allocation_value
    tranche.target_rate = payload.target_rate
    tranche.minimum_rate = payload.minimum_rate
    tranche.deadline = payload.deadline
    tranche.intended_for_auto_conversion = payload.intended_for_auto_conversion
    tranche.notifications_enabled = payload.notifications_enabled
    tranche.wise_auto_conversion_reference = payload.wise_auto_conversion_reference

    strategy = await _strategy(session, tranche.strategy_id)
    await session.flush()
    strategies.recalculate_allocations(strategy)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="tranche",
        entity_id=tranche.id,
        message=f"Tranche {tranche.sequence} updated",
        before=before,
        after=payload.model_dump(mode="json"),
        actor=actor,
    )
    return TrancheOut.model_validate(tranche)


@router.delete("/tranches/{tranche_id}", response_model=Message, summary="Delete a tranche")
async def delete_tranche(tranche_id: int, session: SessionDep, actor: ActorDep) -> Message:
    tranche = await _tranche(session, tranche_id)
    strategy = await _strategy(session, tranche.strategy_id)

    linked = strategies.tranche_conversions(strategy, tranche.id)
    if linked:
        raise ConflictError(
            f"Tranche {tranche.sequence} has {len(linked)} recorded conversion(s). "
            "Skip it instead, or reassign those conversions first."
        )

    sequence = tranche.sequence
    strategy.tranches.remove(tranche)
    await session.flush()
    strategies.recalculate_allocations(strategy)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.DELETED,
        entity_type="tranche",
        entity_id=tranche_id,
        message=f"Tranche {sequence} deleted",
        actor=actor,
    )
    return Message(message=f"Tranche {sequence} deleted.")


@router.post("/tranches/reorder", response_model=list[TrancheOut], summary="Reorder tranches")
async def reorder_tranches(
    payload: TrancheReorder, session: SessionDep, actor: ActorDep
) -> list[TrancheOut]:
    strategy = await _strategy(session, payload.strategy_id)
    by_id = {tranche.id: tranche for tranche in strategy.tranches}
    if set(payload.tranche_ids) != set(by_id):
        raise ValidationError(
            "The reorder list must contain every tranche in the strategy, exactly once."
        )
    for position, tranche_id in enumerate(payload.tranche_ids, start=1):
        by_id[tranche_id].sequence = position
    await session.flush()
    strategies.recalculate_allocations(strategy)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="strategy",
        entity_id=strategy.id,
        message="Tranches reordered",
        after={"order": payload.tranche_ids},
        actor=actor,
    )
    return [
        TrancheOut.model_validate(tranche)
        for tranche in sorted(strategy.tranches, key=lambda item: item.sequence)
    ]


@router.post("/tranches/{tranche_id}/acknowledge", response_model=TrancheOut, summary="Acknowledge")
async def acknowledge_tranche(tranche_id: int, session: SessionDep, actor: ActorDep) -> TrancheOut:
    """Silence repeat alerts for a target without claiming it was converted.

    Acknowledging is explicitly not completing: a tranche is only completed by
    recording an actual conversion against it.
    """
    tranche = await _tranche(session, tranche_id)
    tranche.acknowledged_at = utcnow()
    await session.flush()
    await audit.record(
        session,
        event_type=AuditEventType.ACKNOWLEDGED,
        entity_type="tranche",
        entity_id=tranche.id,
        message=f"Tranche {tranche.sequence} target acknowledged (not marked as converted)",
        actor=actor,
    )
    return TrancheOut.model_validate(tranche)


@router.post("/tranches/{tranche_id}/skip", response_model=TrancheOut, summary="Skip")
async def skip_tranche(tranche_id: int, session: SessionDep, actor: ActorDep) -> TrancheOut:
    tranche = await _tranche(session, tranche_id)
    before = tranche.status
    tranche.status = str(TrancheStatus.SKIPPED)
    await session.flush()
    strategy = await _strategy(session, tranche.strategy_id)
    strategies.recalculate_allocations(strategy)
    await session.flush()
    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="tranche",
        entity_id=tranche.id,
        message=f"Tranche {tranche.sequence} skipped",
        before={"status": before},
        after={"status": tranche.status},
        actor=actor,
    )
    return TrancheOut.model_validate(tranche)


@router.get(
    "/strategies/{strategy_id}/allocation", response_model=ValidationOut, summary="Allocation"
)
async def allocation_report(strategy_id: int, session: SessionDep) -> ValidationOut:
    strategy = await _strategy(session, strategy_id)
    return _validation_out(strategies.recalculate_allocations(strategy))
