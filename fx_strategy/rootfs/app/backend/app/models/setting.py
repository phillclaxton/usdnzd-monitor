"""Runtime settings, stored as a key/value document table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, utcnow


class AppSetting(Base):
    """A single settings namespace serialised as JSON.

    Settings are grouped (``general``, ``providers``, ``notifications``, ...)
    rather than stored one row per field, so a settings update is a single
    atomic write with a single audit event.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
