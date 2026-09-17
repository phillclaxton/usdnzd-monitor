"""SQLAlchemy models.

Importing this package registers every model with :class:`app.database.Base`,
which Alembic's autogenerate relies on.
"""

from app.models.alert import (
    AlertRule,
    AlertRuleType,
    NotificationLog,
    Severity,
    TargetState,
    TrancheAlertState,
)
from app.models.audit import AuditEvent, AuditEventType
from app.models.obligation import Obligation, ObligationFunding
from app.models.position import POSITION_ID, FxAlertState, FxPosition
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
    "POSITION_ID",
    "AlertRule",
    "AlertRuleType",
    "AppSetting",
    "AuditEvent",
    "AuditEventType",
    "Conversion",
    "DeadlineRequirement",
    "FeeModel",
    "FxAlertState",
    "FxPosition",
    "ManualRate",
    "NotificationLog",
    "Obligation",
    "ObligationFunding",
    "ProviderStatus",
    "RateAggregate",
    "RateSample",
    "RecordSource",
    "Severity",
    "Strategy",
    "StrategyStatus",
    "TargetState",
    "Tranche",
    "TrancheAlertState",
    "TrancheStatus",
]
