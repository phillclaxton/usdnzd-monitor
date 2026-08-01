"""SQLAlchemy models.

Importing this package registers every model with :class:`app.database.Base`,
which Alembic's autogenerate relies on.
"""

from app.models.audit import AuditEvent, AuditEventType
from app.models.setting import AppSetting

__all__ = [
    "AppSetting",
    "AuditEvent",
    "AuditEventType",
]
