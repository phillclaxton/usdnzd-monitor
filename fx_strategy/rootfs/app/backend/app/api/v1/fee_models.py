"""Fee model endpoints.

Moved out of the strategy router, which no longer exists. Nothing computes with
a fee model today — the figures that did were part of the ladder — but the rows
are entered by hand and are in every backup, so leaving the table without a way
to read or change it would strand them.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.api.errors import NotFoundError
from app.models.rate import FeeModel
from app.schemas.common import Message
from app.schemas.fee_model import FeeModelIn, FeeModelOut
from app.services import audit

router = APIRouter(prefix="/fee-models", tags=["fee models"])


@router.get("", response_model=list[FeeModelOut], summary="List fee models")
async def list_fee_models(session: SessionDep) -> list[FeeModelOut]:
    rows = (await session.execute(select(FeeModel).order_by(FeeModel.name))).scalars().all()
    return [FeeModelOut.model_validate(row) for row in rows]


@router.post(
    "",
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


@router.delete("/{fee_model_id}", response_model=Message, summary="Delete a fee model")
async def delete_fee_model(fee_model_id: int, session: SessionDep, actor: ActorDep) -> Message:
    model = await session.get(FeeModel, fee_model_id)
    if model is None:
        raise NotFoundError(f"Fee model {fee_model_id} does not exist.")
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
