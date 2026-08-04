"""Backup, restore and diagnostics.

A normal backup contains everything except credentials.  A diagnostics bundle
additionally masks account and transaction identifiers, because it is the thing
most likely to be pasted into an issue tracker.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import get_config
from app.database import (
    checkpoint_wal,
    integrity_check,
    sqlite_file_size,
    utcnow,
)
from app.logging_setup import get_logger, get_ring_buffer
from app.models.alert import NotificationLog, TrancheAlertState
from app.models.audit import AuditEvent, AuditEventType
from app.models.rate import FeeModel, ManualRate, ProviderStatus, RateAggregate, RateSample
from app.models.setting import AppSetting
from app.models.strategy import Conversion, DeadlineRequirement, Strategy, Tranche
from app.schemas.settings import Settings
from app.security.secrets import get_secret_store
from app.services import audit

log = get_logger(__name__)

BACKUP_FORMAT_VERSION = 1

#: Never included in a backup, whatever else is.
EXCLUDED_TABLES = frozenset({"secrets"})


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {column.name: _serialize(getattr(row, column.name)) for column in row.__table__.columns}


async def _dump(session: AsyncSession, model: Any) -> list[dict[str, Any]]:
    rows = (await session.execute(select(model))).scalars().all()
    return [_row_to_dict(row) for row in rows]


async def create_backup(
    session: AsyncSession, settings: Settings, *, actor: str = "user"
) -> dict[str, Any]:
    """A complete, portable copy of the data — without any credential.

    The WAL is checkpointed first so a filesystem-level snapshot taken at the
    same moment is also complete.
    """
    try:
        await checkpoint_wal()
    except Exception:  # pragma: no cover - best effort
        log.warning("backup_checkpoint_failed")

    data: dict[str, list[dict[str, Any]]] = {
        "strategies": await _dump(session, Strategy),
        "tranches": await _dump(session, Tranche),
        "deadline_requirements": await _dump(session, DeadlineRequirement),
        "conversions": await _dump(session, Conversion),
        "fee_models": await _dump(session, FeeModel),
        "rate_samples": await _dump(session, RateSample),
        "rate_aggregates": await _dump(session, RateAggregate),
        "manual_rates": await _dump(session, ManualRate),
        "provider_status": await _dump(session, ProviderStatus),
        "tranche_alert_states": await _dump(session, TrancheAlertState),
        "notification_log": await _dump(session, NotificationLog),
        "audit_events": await _dump(session, AuditEvent),
    }
    counts = {name: len(rows) for name, rows in data.items()}

    document: dict[str, Any] = {
        "format_version": BACKUP_FORMAT_VERSION,
        "app_version": __version__,
        "created_at": utcnow().isoformat(),
        "contains_secrets": False,
        "note": (
            "Credentials are deliberately excluded. Re-enter your API tokens after restoring."
        ),
        "settings": settings.model_dump(mode="json"),
        "data": data,
        "counts": counts,
    }

    await audit.record(
        session,
        event_type=AuditEventType.EXPORTED,
        entity_type="backup",
        message=f"Backup created: {sum(counts.values())} rows across {len(counts)} tables",
        after=counts,
        actor=actor,
    )
    return document


class RestoreError(ValueError):
    """The supplied backup cannot be restored."""


#: Restore order matters: parents before children.
RESTORE_ORDER: tuple[tuple[str, Any], ...] = (
    ("fee_models", FeeModel),
    ("strategies", Strategy),
    ("tranches", Tranche),
    ("deadline_requirements", DeadlineRequirement),
    ("conversions", Conversion),
    ("rate_samples", RateSample),
    ("rate_aggregates", RateAggregate),
    ("manual_rates", ManualRate),
    ("provider_status", ProviderStatus),
    ("tranche_alert_states", TrancheAlertState),
    ("notification_log", NotificationLog),
    ("audit_events", AuditEvent),
)


def _coerce(model: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Turn a serialized row back into constructor arguments.

    Types are recognised from the column definition rather than from the column
    name, so a timestamp called ``timestamp`` is treated the same as one called
    ``created_at``.
    """
    from datetime import UTC

    from app.database import DecimalText, UTCDateTime

    values: dict[str, Any] = {}
    for column in model.__table__.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if value is None:
            values[column.name] = None
        elif isinstance(column.type, UTCDateTime):
            parsed = datetime.fromisoformat(str(value))
            values[column.name] = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        elif isinstance(column.type, DecimalText):
            values[column.name] = Decimal(str(value))
        else:
            # Custom types raise rather than declaring a python_type, so this
            # asks carefully and otherwise passes the value straight through.
            try:
                resolved: Any = column.type.python_type
            except (NotImplementedError, AttributeError):
                resolved = None
            values[column.name] = bool(value) if resolved is bool else value
    return values


async def restore_backup(
    session: AsyncSession,
    document: dict[str, Any],
    *,
    replace: bool = False,
    actor: str = "user",
) -> dict[str, int]:
    """Load a backup document.

    Refuses to merge into a database that already has strategies unless
    ``replace`` is set, so a restore cannot silently duplicate a portfolio.
    """
    if not isinstance(document, dict) or "data" not in document:
        raise RestoreError("This file is not a FX Strategy Manager backup.")
    version = document.get("format_version")
    if version != BACKUP_FORMAT_VERSION:
        raise RestoreError(
            f"This backup is format version {version}; this app reads version "
            f"{BACKUP_FORMAT_VERSION}."
        )

    existing = (await session.execute(select(func.count()).select_from(Strategy))).scalar_one()
    if existing and not replace:
        raise RestoreError(
            f"This installation already has {existing} strategy(ies). "
            "Set replace=true to overwrite them, or restore into a fresh install."
        )

    if replace:
        from sqlalchemy import delete

        # Reverse order so children go before parents.
        for _name, model in reversed(RESTORE_ORDER):
            await session.execute(delete(model))
        await session.execute(delete(AppSetting))
        await session.flush()

    counts: dict[str, int] = {}
    data = document["data"]
    for name, model in RESTORE_ORDER:
        rows = data.get(name) or []
        for row in rows:
            session.add(model(**_coerce(model, row)))
        counts[name] = len(rows)
    await session.flush()

    settings_document = document.get("settings")
    if isinstance(settings_document, dict):
        for key, value in settings_document.items():
            session.add(AppSetting(key=key, value_json=json.dumps(value, sort_keys=True)))
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.RESTORED,
        entity_type="backup",
        message=(
            f"Restored a backup from {document.get('created_at', 'an unknown time')}: "
            f"{sum(counts.values())} rows. Credentials were not included and must be re-entered."
        ),
        after=counts,
        actor=actor,
    )
    log.info("backup_restored", rows=sum(counts.values()))
    return counts


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


#: Identifiers are masked in the diagnostics bundle: it is the artefact most
#: likely to be pasted somewhere public.
def mask_identifier(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


async def diagnostics(
    session: AsyncSession, settings: Settings, *, include_logs: bool = True
) -> dict[str, Any]:
    """Everything useful for troubleshooting, with nothing sensitive in it."""
    from app.home_assistant.client import get_home_assistant
    from app.scheduler.jobs import get_scheduler
    from app.services import publisher, rate_service

    config = get_config()
    store = get_secret_store()

    latest = await rate_service.latest_sample(
        session, settings.general.source_currency, settings.general.target_currency
    )
    provider_rows = await rate_service.provider_statuses(session)
    # Not configured is not failing: an unused manual fallback would otherwise
    # be reported here as a fault.
    from app.scheduler.jobs import build_registry

    registry = await build_registry(session, settings)
    try:
        unconfigured = {d.name for d in registry.describe() if not d.configured}
    finally:
        await registry.aclose()
    failures = [
        row for row in provider_rows if not row.healthy and row.provider not in unconfigured
    ]

    integrity = await integrity_check()
    ha_status = await get_home_assistant().status()

    counts = {
        "strategies": (
            await session.execute(select(func.count()).select_from(Strategy))
        ).scalar_one(),
        "conversions": (
            await session.execute(select(func.count()).select_from(Conversion))
        ).scalar_one(),
        "rate_samples": (
            await session.execute(select(func.count()).select_from(RateSample))
        ).scalar_one(),
        "audit_events": (
            await session.execute(select(func.count()).select_from(AuditEvent))
        ).scalar_one(),
        "notifications": (
            await session.execute(select(func.count()).select_from(NotificationLog))
        ).scalar_one(),
    }

    clock_warning = None
    if latest is not None and latest.provider_timestamp is not None:
        drift = abs((latest.retrieved_at - latest.provider_timestamp).total_seconds())
        if drift > 3600:
            clock_warning = (
                f"The provider's timestamp differs from this system's clock by "
                f"{int(drift // 60)} minutes. Check the host time; calculations that "
                "depend on sample ordering may be affected."
            )

    return {
        "app": {
            "version": config.app_version,
            "architecture": config.build_arch,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "simulation_mode": settings.simulation.enabled,
            "ingress_entry_configured": bool(config.ingress_entry),
        },
        "database": {
            "path": str(config.database_path),
            "size_bytes": sqlite_file_size(config),
            "integrity_problems": integrity,
            "counts": counts,
        },
        "rates": {
            "last_sample_at": latest.retrieved_at.isoformat() if latest else None,
            "last_provider": latest.provider if latest else None,
            "last_provider_timestamp": (
                latest.provider_timestamp.isoformat()
                if latest and latest.provider_timestamp
                else None
            ),
            "clock_warning": clock_warning,
            "providers": [
                {
                    "provider": row.provider,
                    "healthy": row.healthy,
                    "consecutive_failures": row.consecutive_failures,
                    "last_success_at": (
                        row.last_success_at.isoformat() if row.last_success_at else None
                    ),
                    "last_failure_at": (
                        row.last_failure_at.isoformat() if row.last_failure_at else None
                    ),
                    "last_latency_ms": row.last_latency_ms,
                    "last_error": row.last_error,
                }
                for row in provider_rows
            ],
            "failing_providers": [row.provider for row in failures],
        },
        "scheduler": get_scheduler().status(),
        "home_assistant": {
            "available": ha_status.available,
            "message": ha_status.message,
            "latency_ms": ha_status.latency_ms,
            "notify_services_discovered": len(ha_status.notify_services),
            "configured_services": settings.notifications.services,
        },
        "mqtt": publisher.diagnostics(),
        "wise": {
            "enabled": settings.providers.wise.enabled,
            "environment": settings.providers.wise.environment,
            "profile_id": mask_identifier(settings.providers.wise.profile_id),
            "source_balance_id": mask_identifier(settings.providers.wise.source_balance_id),
            "read_only": True,
        },
        "credentials": {
            key: {"configured": value["configured"]} for key, value in store.status().items()
        },
        "secrets_file_mode": store.file_permissions(),
        "recent_logs": get_ring_buffer().tail(100) if include_logs else [],
        "generated_at": utcnow().isoformat(),
        "note": (
            "This bundle excludes credentials and masks account and transaction "
            "identifiers. Check it before sharing."
        ),
    }
