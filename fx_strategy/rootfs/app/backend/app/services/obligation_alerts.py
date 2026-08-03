"""Notifications about obligations.

Each trigger answers a question the user would otherwise have to check by
opening the dashboard. They are deliberately specific: a message that only says
"something changed" is one the user learns to ignore.

Every message states the amounts in full and says what it is based on. None of
them instructs the app to do anything; they inform a decision the user makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertRuleType, Severity
from app.schemas.settings import Settings
from app.services import notifications, obligation_service
from app.services.notifications import Notification
from app.services.obligation_engine import Priority, RecommendedAction

#: A recommended conversion amount has to move by more than this before it is
#: worth interrupting someone: rate noise alone should not send a message.
MATERIAL_CHANGE_NZD = Decimal("500")


@dataclass(frozen=True, slots=True)
class ObligationAlert:
    """One thing worth telling the user about."""

    key: str
    title: str
    message: str
    severity: Severity
    obligation_id: int | None = None


def _money(value: Decimal | None) -> str:
    return "an unknown amount" if value is None else f"NZ${value:,.2f}"


def _usd(value: Decimal | None) -> str:
    return "an unknown amount" if value is None else f"US${value:,.2f}"


def evaluate(
    ranked: list[obligation_service.RankedAnalysis],
    settings: Settings,
    *,
    due_within_days: int = 7,
    previous_amounts: dict[int, Decimal] | None = None,
) -> list[ObligationAlert]:
    """Everything currently worth a notification.

    Returns candidates; suppression by quiet hours and cooldown happens in the
    notification service, so this stays a pure function of the book's state.
    """
    alerts: list[ObligationAlert] = []
    previous = previous_amounts or {}

    for item in ranked:
        analysis = item.analysis
        row = item.row
        if row.completed or analysis.remaining_nzd <= 0:
            continue

        name = row.name
        oid = item.obligation_id

        # Due soon, or already overdue.
        if analysis.overdue:
            alerts.append(
                ObligationAlert(
                    key=f"obligation_overdue_{oid}",
                    title=f"{name} is overdue",
                    message=(
                        f"{name}: {_money(analysis.remaining_nzd)} was due "
                        f"{abs(analysis.days_until_due or 0)} day(s) ago and remains "
                        f"outstanding. About {_usd(analysis.usd_required_now)} would fund it "
                        "at the current rate."
                    ),
                    severity=Severity.CRITICAL,
                    obligation_id=oid,
                )
            )
        elif analysis.days_until_due is not None and analysis.days_until_due <= due_within_days:
            interest_note = (
                "This obligation is interest-free, so waiting costs nothing financially, "
                "but the date stands."
                if not analysis.has_interest_cost
                else f"Waiting costs about {_money(analysis.daily_cost_nzd)} a day."
            )
            alerts.append(
                ObligationAlert(
                    key=f"obligation_due_soon_{oid}",
                    title=f"{name} is due in {analysis.days_until_due} day(s)",
                    message=(
                        f"{name}: convert approximately {_usd(analysis.usd_required_now)} now "
                        f"to fund {_money(analysis.remaining_nzd)}. {interest_note}"
                    ),
                    severity=Severity.WARNING,
                    obligation_id=oid,
                )
            )

        # The user's own limit on waiting has run out.
        if row.max_wait_days is not None and row.max_wait_days <= 0:
            alerts.append(
                ObligationAlert(
                    key=f"obligation_max_wait_{oid}",
                    title=f"{name}: maximum waiting period reached",
                    message=(
                        f"{name}: the maximum acceptable waiting period has been reached. "
                        f"Converting about {_usd(analysis.usd_required_now)} now would fund "
                        f"{_money(analysis.remaining_nzd)}."
                    ),
                    severity=Severity.WARNING,
                    obligation_id=oid,
                )
            )

        # The target this obligation was waiting for has arrived.
        if (
            row.target_rate is not None
            and analysis.rate_used is not None
            and not analysis.rate_stale
            and analysis.rate_used >= row.target_rate
        ):
            alerts.append(
                ObligationAlert(
                    key=f"obligation_target_reached_{oid}",
                    title=f"{name}: target rate reached",
                    message=(
                        f"{name}: the rate is {analysis.rate_used}, at or above the target "
                        f"of {row.target_rate}. Funding {_money(analysis.remaining_nzd)} would "
                        f"now take about {_usd(analysis.usd_required_now)}. "
                        "Reaching a target converts nothing on its own."
                    ),
                    severity=Severity.INFO,
                    obligation_id=oid,
                )
            )

        # The rate is already past what 30 days of waiting would need to earn.
        break_even_30 = analysis.break_even_rate_after.get(30)
        if (
            break_even_30 is not None
            and analysis.rate_used is not None
            and not analysis.rate_stale
            and analysis.rate_used >= break_even_30
            and analysis.has_interest_cost
        ):
            alerts.append(
                ObligationAlert(
                    key=f"obligation_break_even_passed_{oid}",
                    title=f"{name}: the rate covers a month of waiting",
                    message=(
                        f"{name}: at {analysis.rate_used} the rate is above the "
                        f"{break_even_30} needed to repay 30 days of interest. Converting "
                        "now captures that; waiting longer risks giving it back."
                    ),
                    severity=Severity.INFO,
                    obligation_id=oid,
                )
            )

        # Waiting has turned net negative against the configured target.
        thirty = analysis.waiting.get(30)
        if (
            thirty is not None
            and thirty.net_benefit_nzd is not None
            and thirty.net_benefit_nzd < 0
            and analysis.has_interest_cost
            and not analysis.rate_stale
        ):
            alerts.append(
                ObligationAlert(
                    key=f"obligation_waiting_negative_{oid}",
                    title=f"{name}: waiting is now costing money",
                    message=(
                        f"{name}: waiting 30 days for {row.target_rate} would cost "
                        f"{_money(thirty.waiting_cost_nzd)} in interest against a gain of "
                        f"{_money(thirty.fx_gain_nzd)} — a net "
                        f"{_money(thirty.net_benefit_nzd)}. Converting now is cheaper."
                    ),
                    severity=Severity.WARNING,
                    obligation_id=oid,
                )
            )

        # A critical obligation with nothing yet applied to it.
        if row.priority == str(Priority.CRITICAL) and row.amount_funded_nzd <= 0:
            alerts.append(
                ObligationAlert(
                    key=f"obligation_critical_unfunded_{oid}",
                    title=f"{name} is critical and unfunded",
                    message=(
                        f"{name} is marked critical with {_money(analysis.remaining_nzd)} "
                        f"outstanding and nothing applied to it yet. About "
                        f"{_usd(analysis.usd_required_now)} would fund it at the current rate."
                    ),
                    severity=Severity.CRITICAL,
                    obligation_id=oid,
                )
            )

        # The recommended amount has moved materially since last time.
        was = previous.get(oid)
        usd_now = analysis.usd_required_now
        if was is not None and usd_now is not None:
            movement = abs(usd_now - was)
            if movement >= MATERIAL_CHANGE_NZD:
                direction = "less" if usd_now < was else "more"
                alerts.append(
                    ObligationAlert(
                        key=f"obligation_amount_changed_{oid}",
                        title=f"{name}: conversion amount has moved",
                        message=(
                            f"{name}: funding {_money(analysis.remaining_nzd)} now takes about "
                            f"{_usd(usd_now)}, {_usd(movement)} {direction} than when last "
                            "checked. This reflects the rate, not a change to the obligation."
                        ),
                        severity=Severity.INFO,
                        obligation_id=oid,
                    )
                )

    return alerts


async def deliver(
    session: AsyncSession,
    alerts: list[ObligationAlert],
    settings: Settings,
    *,
    cooldown_minutes: int | None = None,
) -> int:
    """Send each alert, letting the notification service apply its own rules."""
    delivered = 0
    for alert in alerts:
        result = await notifications.send(
            session,
            Notification(
                rule_type=AlertRuleType.DEADLINE_APPROACHING,
                title=alert.title,
                message=alert.message,
                severity=alert.severity,
                entity_type="obligation",
                entity_id=str(alert.obligation_id) if alert.obligation_id else None,
            ),
            settings,
            cooldown_minutes=cooldown_minutes,
        )
        if result.delivered:
            delivered += 1
    return delivered


def recommended_action_changed(before: RecommendedAction, after: RecommendedAction) -> bool:
    """Whether a change of recommendation is worth reporting.

    Moving between the two waiting states is not: the substance is the same.
    """
    waiting = {RecommendedAction.WAIT_FOR_TARGET, RecommendedAction.WAIT_WITH_DEADLINE}
    if before in waiting and after in waiting:
        return False
    return before != after
