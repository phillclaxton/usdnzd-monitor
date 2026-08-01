"""SQLAlchemy models.

Importing this package registers every model with :class:`app.database.Base`,
which Alembic's autogenerate relies on.
"""

from app.models.audit import AuditEvent, AuditEventType
from app.models.rate import (
    FeeModel,
    ManualRate,
    ProviderStatus,
    RateAggregate,
    RateSample,
)
from app.models.setting import AppSetting
from app.models.strategy import (
    Conversion,
    DeadlineRequirement,
    RecordSource,
    Strategy,
    StrategyStatus,
    Tranche,
    TrancheStatus,
)

__all__ = [
    "AppSetting",
    "AuditEvent",
    "AuditEventType",
    "Conversion",
    "DeadlineRequirement",
    "FeeModel",
    "ManualRate",
    "ProviderStatus",
    "RateAggregate",
    "RateSample",
    "RecordSource",
    "Strategy",
    "StrategyStatus",
    "Tranche",
    "TrancheStatus",
]
