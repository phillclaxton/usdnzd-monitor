"""Audit history endpoint."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.common import Schema
from app.services import audit

router = APIRouter(prefix="/audit-events", tags=["audit"])


class AuditEventOut(Schema):
    id: int
    event_type: str
    entity_type: str
    entity_id: str | None
    actor: str
    timestamp: datetime
    before_json: str | None
    after_json: str | None
    message: str
    correlation_id: str | None


@router.get("", response_model=list[AuditEventOut], summary="List audit events")
async def list_audit_events(
    session: SessionDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AuditEventOut]:
    events = await audit.list_events(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return [AuditEventOut.model_validate(event) for event in events]
