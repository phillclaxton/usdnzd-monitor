"""The FX position, and the state that keeps alerts from repeating themselves.

These are **facts about the world** — how much currency is held, what it is
measured against, what the mortgage still costs — which is why they are a table
and not a settings section. Every change goes through :mod:`app.services.audit`,
so the position has a change history for free, and it is captured by backups.

The *preferences* that decide when to say something about those facts live in
the ``fx_alerts`` settings section instead. The line falls cleanly: the measured
offset shortfall is a fact; "tell me when the daily carrying cost drops below
five dollars" is a preference.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, MoneyText, RateText, UTCDateTime, utcnow

#: The only row ``fx_position`` ever has. There is one position, not a list of
#: them, and giving it a fixed id means every read is ``session.get(...)``
#: rather than a query that could return two rows and have to pick one.
POSITION_ID = 1


class FxPosition(Base):
    """What is held, what it is measured against, and what waiting costs.

    Almost every column is nullable, because the position is filled in over
    several sittings — the balance one day, the mortgage figures another — and a
    half-filled position must still be readable rather than rejected. Only the
    currencies and the balance are required, because without them there is no
    position at all.

    Nothing here is derived. The NZD value, the realised and unrealised
    improvement and the carrying cost are all computed on read by
    :mod:`app.services.position_math`, so a stale stored total can never
    disagree with the figures it was built from.
    """

    __tablename__ = "fx_position"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=POSITION_ID)

    source_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NZD")

    #: The currency still held and not yet converted.
    current_source_balance: Mapped[Decimal] = mapped_column(
        MoneyText(), nullable=False, default=Decimal(0)
    )

    #: What every improvement figure is measured against. While this is unset,
    #: no improvement can be calculated and the app says so rather than
    #: showing zero.
    baseline_rate: Mapped[Decimal | None] = mapped_column(RateText())
    baseline_date: Mapped[date | None] = mapped_column(Date)

    #: A fraction, not a percentage: 6.04% is stored as 0.0604. Rate precision
    #: rather than money precision, because 4 places is too coarse for an
    #: interest rate.
    floating_loan_rate: Mapped[Decimal | None] = mapped_column(RateText())
    #: How much of the offset facility is still unfunded, in the target currency.
    current_offset_shortfall_nzd: Mapped[Decimal | None] = mapped_column(MoneyText())
    monthly_nzd_burn: Mapped[Decimal | None] = mapped_column(MoneyText())

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class FxAlertState(Base):
    """The last thing said about one alert condition, so it is not said twice.

    Written by PR 4's movement alerts; the table exists from here so that work
    is pure logic with no migration of its own.

    The existing notification dedupe is a *time* cooldown over
    ``notification_log``, which cannot express "do not mention this again until
    it moves another half cent". That needs the value alerted at to be
    remembered, which is what this table is for.

    ``alert_key`` is the identity of a condition — ``new_high:30d``,
    ``watch_level:1.7500`` — and the invariant is that it is also the
    ``entity_id`` of every notification raised for that condition, with
    ``entity_type="fx_alert"``. That maps this table straight onto the existing
    cooldown with no change to ``notification_log``, whose ``entity_id`` is
    ``String(64)`` — hence the width here.

    The sample and confirmation columns are named exactly as
    :class:`~app.models.alert.TrancheAlertState` names them, so one confirmation
    rule can read both without anyone having to remember which table calls it
    what.
    """

    __tablename__ = "fx_alert_state"

    alert_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: Where the condition currently sits, for hysteresis: a level crossed
    #: upwards only alerts downwards once it has fallen back through the
    #: reset band. ``armed`` means nothing has happened yet.
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="armed")

    #: The rate at the last alert. The gate that does not exist today compares
    #: against this: alerting at 1.7502 must not alert again at 1.7508.
    last_value: Mapped[Decimal | None] = mapped_column(RateText())
    #: What the rate was measured against — the high beaten, or the day's open.
    last_reference_value: Mapped[Decimal | None] = mapped_column(RateText())
    #: For the conditions measured in money rather than rate: portfolio value,
    #: carrying cost.
    last_money_value: Mapped[Decimal | None] = mapped_column(MoneyText())

    qualifying_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_qualifying_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sample_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sample_rate: Mapped[Decimal | None] = mapped_column(RateText())

    last_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
