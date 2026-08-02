"""Load and persist the settings document."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.setting import AppSetting
from app.schemas.settings import Settings, SettingsUpdate
from app.services import audit

log = get_logger(__name__)

SECTION_NAMES = tuple(Settings.model_fields)


async def load_settings(session: AsyncSession) -> Settings:
    """Read the settings document, filling in defaults for missing sections."""
    rows = (await session.execute(select(AppSetting))).scalars().all()
    stored: dict[str, Any] = {}
    for row in rows:
        if row.key not in SECTION_NAMES:
            continue
        try:
            stored[row.key] = json.loads(row.value_json)
        except json.JSONDecodeError:
            # A corrupt section falls back to defaults rather than taking the
            # whole application down; the problem is logged loudly.
            log.error("settings_section_corrupt", section=row.key)
    return Settings.model_validate(stored)


async def _write_section(session: AsyncSession, key: str, payload: dict[str, Any]) -> None:
    row = await session.get(AppSetting, key)
    encoded = json.dumps(payload, sort_keys=True)
    if row is None:
        session.add(AppSetting(key=key, value_json=encoded))
    else:
        row.value_json = encoded


async def save_settings(
    session: AsyncSession, settings: Settings, *, actor: str = "system", message: str = ""
) -> Settings:
    """Persist every section of the document."""
    before = (await load_settings(session)).model_dump(mode="json")
    for key in SECTION_NAMES:
        section = getattr(settings, key)
        await _write_section(session, key, section.model_dump(mode="json"))
    # Sessions are created with autoflush off, so the write is made visible to
    # subsequent reads explicitly.
    await session.flush()
    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="settings",
        entity_id="settings",
        message=message or "Settings updated",
        before=before,
        after=settings.model_dump(mode="json"),
        actor=actor,
    )
    return settings


async def update_settings(
    session: AsyncSession, update: SettingsUpdate, *, actor: str = "user"
) -> Settings:
    """Apply a partial update, replacing only the supplied sections."""
    current = await load_settings(session)
    changed = update.model_dump(exclude_unset=True, exclude_none=True)
    merged = current.model_copy(
        update={key: getattr(update, key) for key in changed if getattr(update, key) is not None}
    )
    return await save_settings(
        session,
        merged,
        actor=actor,
        message=f"Settings updated: {', '.join(sorted(changed)) or 'no sections'}",
    )


async def patch_section(
    session: AsyncSession, section: str, values: dict[str, Any], *, actor: str = "system"
) -> Settings:
    """Update individual fields inside one section."""
    if section not in SECTION_NAMES:
        raise KeyError(f"unknown settings section {section!r}")
    current = await load_settings(session)
    section_model = getattr(current, section)
    updated_section = section_model.model_copy(update=values)
    # Re-validate so a bad value is rejected instead of silently stored.
    updated_section = type(section_model).model_validate(updated_section.model_dump())
    merged = current.model_copy(update={section: updated_section})
    return await save_settings(
        session, merged, actor=actor, message=f"Settings section {section} updated"
    )
