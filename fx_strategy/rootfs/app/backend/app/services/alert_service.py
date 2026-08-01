"""The target alert state machine.

State per target, as specified:

``below`` → ``near`` → ``reached_unconfirmed`` → ``reached_confirmed`` →
``notified`` → ``acknowledged`` / ``completed``, with ``reset`` returning to
``below``.

A target-reached notification fires only when every one of these holds:

* the rate crossed from below to at or above the target;
* the rate is not stale;
* the providers agree within the configured threshold;
* the tranche is open and its notifications are enabled;
* the target has not already been acknowledged or completed;
* the confirmation rule passed — by default two consecutive qualifying samples
  at least 30 seconds apart.

A second notification for the same target requires the rate to have dropped
below ``target - hysteresis``, come back, and the cooldown to have expired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.alert import AlertRuleType, Severity, TargetState, TrancheAlertState
from app.models.audit import AuditEventType
from app.models.strategy import Strategy, Tranche, TrancheStatus
from app.money import ZERO, quantize_rate
from app.schemas.settings import Settings
from app.services import audit
from app.services import calculations as calc
from app.services import strategy_service as strategies
from app.services.notifications import Notification

log = get_logger(__name__)


@dataclass(slots=True)
class Evaluation:
    """What one evaluation of a target produced."""

    tranche_id: int
    previous_state: TargetState
    state: TargetState
    should_notify: bool = False
    notification: Notification | None = None
    reason: str = ""


@dataclass(slots=True)
class AlertOutcome:
    evaluations: list[Evaluation] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)

    @property
    def notified_tranche_ids(self) -> list[int]:
        return [
            evaluation.tranche_id for evaluation in self.evaluations if evaluation.should_notify
        ]


async def get_state(session: AsyncSession, tranche_id: int) -> TrancheAlertState:
    state = await session.get(TrancheAlertState, tranche_id)
    if state is None:
        state = TrancheAlertState(tranche_id=tranche_id)
        session.add(state)
        await session.flush()
    return state


async def states_for(session: AsyncSession, strategy: Strategy) -> dict[int, TrancheAlertState]:
    ids = [tranche.id for tranche in strategy.tranches]
    if not ids:
        return {}
    stmt = select(TrancheAlertState).where(TrancheAlertState.tranche_id.in_(ids))
    existing = {row.tranche_id: row for row in (await session.execute(stmt)).scalars().all()}
    for tranche_id in ids:
        if tranche_id not in existing:
            existing[tranche_id] = await get_state(session, tranche_id)
    return existing


def _confirmation_passed(state: TrancheAlertState, settings: Settings, sample_at: datetime) -> bool:
    """Two consecutive qualifying samples, far enough apart in time."""
    required = settings.notifications.confirmation_samples
    if state.qualifying_samples < required:
        return False
    if required <= 1:
        return True
    if state.first_qualifying_at is None:
        return False
    gap = (sample_at - state.first_qualifying_at).total_seconds()
    return gap >= settings.notifications.confirmation_min_seconds


def _format_money(value: Decimal | None, currency: str) -> str:
    if value is None:
        return "not calculable"
    return f"{currency} {value.quantize(Decimal('0.01')):,}"


def _build_target_message(
    strategy: Strategy,
    tranche: Tranche,
    observed_rate: Decimal,
    outstanding: Decimal,
    assumption: calc.FeeAssumption | None,
) -> Notification:
    outcome = calc.project_conversion(outstanding, observed_rate, assumption)
    source, target = strategy.source_currency, strategy.target_currency

    lines = [
        f"Tranche {tranche.sequence}: {_format_money(outstanding, source)}",
        f"Current observed rate: {quantize_rate(observed_rate)}",
        f"Estimated gross {target}: {_format_money(outcome.gross_target_amount, target)}",
    ]
    if outcome.fee.available:
        lines.append(f"Estimated fee: {_format_money(outcome.fee.amount_target_currency, target)}")
        lines.append(f"Estimated net {target}: {_format_money(outcome.net_target_amount, target)}")
    else:
        lines.append("Fee not included — no fee model is configured.")
    lines.append("")
    lines.append(
        "Check your Wise Auto Conversion, or open FX Strategy Manager. "
        "This app has not converted anything."
    )

    return Notification(
        rule_type=AlertRuleType.TARGET_REACHED,
        title=(
            f"{source}/{target} target reached: {tranche.target_rate.quantize(Decimal('0.0001'))}"
        ),
        message="\n".join(lines),
        severity=Severity.NOTICE,
        entity_type="tranche",
        entity_id=str(tranche.id),
    )


def _build_near_message(
    strategy: Strategy,
    tranche: Tranche,
    observed_rate: Decimal,
    outstanding: Decimal,
    assumption: calc.FeeAssumption | None,
) -> Notification:
    source, target = strategy.source_currency, strategy.target_currency
    distance = quantize_rate(tranche.target_rate - observed_rate)
    gross = calc.gross_proceeds(outstanding, tranche.target_rate)
    _ = assumption  # fee detail is deliberately left for the reached message

    return Notification(
        rule_type=AlertRuleType.TARGET_NEAR,
        title=f"{source}/{target} approaching {tranche.target_rate.quantize(Decimal('0.0001'))}",
        message=(
            f"{source}/{target} is {quantize_rate(observed_rate)}, which is {distance} below "
            f"your {tranche.target_rate.quantize(Decimal('0.0001'))} target.\n\n"
            f"The target tranche is {_format_money(outstanding, source)}.\n"
            f"Reaching the target would produce approximately "
            f"{_format_money(gross, target)} before fees."
        ),
        severity=Severity.INFO,
        entity_type="tranche",
        entity_id=str(tranche.id),
    )


def outstanding_for(strategy: Strategy, tranche: Tranche) -> Decimal:
    converted = sum(
        (c.source_amount for c in strategies.tranche_conversions(strategy, tranche.id)), ZERO
    )
    return max(tranche.calculated_source_amount - converted, ZERO)


async def evaluate_targets(
    session: AsyncSession,
    strategy: Strategy,
    settings: Settings,
    *,
    rate: Decimal | None,
    rate_is_stale: bool,
    sample_at: datetime | None = None,
    provider_disagreement: bool = False,
    assumption: calc.FeeAssumption | None = None,
) -> AlertOutcome:
    """Advance the state machine for every target in a strategy."""
    outcome = AlertOutcome()
    moment = sample_at or utcnow()
    states = await states_for(session, strategy)
    hysteresis = settings.notifications.reset_hysteresis
    near_threshold = settings.notifications.near_threshold

    for tranche in sorted(strategy.tranches, key=lambda item: item.sequence):
        state = states[tranche.id]
        previous = TargetState(state.state)
        evaluation = Evaluation(tranche_id=tranche.id, previous_state=previous, state=previous)

        # Terminal states are never re-entered by rate movement alone.
        if tranche.status in (str(TrancheStatus.COMPLETED), str(TrancheStatus.CANCELLED)):
            evaluation.state = TargetState.COMPLETED
            state.state = str(TargetState.COMPLETED)
            evaluation.reason = "tranche is closed"
            outcome.evaluations.append(evaluation)
            continue
        if tranche.status == str(TrancheStatus.SKIPPED):
            evaluation.reason = "tranche is skipped"
            outcome.evaluations.append(evaluation)
            continue

        if rate is None:
            evaluation.reason = "no rate available"
            outcome.evaluations.append(evaluation)
            continue

        state.last_sample_at = moment
        state.last_sample_rate = rate

        at_or_above = rate >= tranche.target_rate
        below_reset = rate < (tranche.target_rate - hysteresis)

        # -- reset ------------------------------------------------------
        if below_reset and previous in (
            TargetState.NOTIFIED,
            TargetState.REACHED_CONFIRMED,
            TargetState.REACHED_UNCONFIRMED,
            TargetState.ACKNOWLEDGED,
        ):
            state.state = str(TargetState.BELOW)
            state.qualifying_samples = 0
            state.first_qualifying_at = None
            state.reset_count += 1
            evaluation.state = TargetState.BELOW
            evaluation.reason = "rate fell below the reset threshold"
            outcome.evaluations.append(evaluation)
            continue

        if not at_or_above:
            # Below the target: track "near" for the approaching alert.
            near = rate >= (tranche.target_rate - near_threshold)
            if previous in (TargetState.BELOW, TargetState.NEAR, TargetState.RESET):
                state.state = str(TargetState.NEAR if near else TargetState.BELOW)
                state.qualifying_samples = 0
                state.first_qualifying_at = None
                evaluation.state = TargetState(state.state)
                if (
                    near
                    and previous is not TargetState.NEAR
                    and tranche.notifications_enabled
                    and not rate_is_stale
                ):
                    evaluation.should_notify = True
                    evaluation.notification = _build_near_message(
                        strategy, tranche, rate, outstanding_for(strategy, tranche), assumption
                    )
                    state.near_notified_at = moment
                    evaluation.reason = "approaching the target"
            outcome.evaluations.append(evaluation)
            continue

        # -- at or above the target -------------------------------------
        if previous in (TargetState.NOTIFIED, TargetState.ACKNOWLEDGED):
            # Already handled; waiting for a reset before it can fire again.
            evaluation.reason = "already notified; awaiting reset"
            outcome.evaluations.append(evaluation)
            continue

        if rate_is_stale:
            # A stale rate never confirms a target, and never advances the
            # qualifying-sample count.
            evaluation.reason = "rate is stale"
            outcome.evaluations.append(evaluation)
            continue

        if provider_disagreement:
            evaluation.reason = "providers disagree beyond the threshold"
            state.state = str(TargetState.REACHED_UNCONFIRMED)
            evaluation.state = TargetState.REACHED_UNCONFIRMED
            outcome.evaluations.append(evaluation)
            continue

        if state.qualifying_samples == 0:
            state.first_qualifying_at = moment
        state.qualifying_samples += 1

        if tranche.target_first_reached_at is None:
            tranche.target_first_reached_at = moment
            if tranche.status == str(TrancheStatus.PENDING):
                # "Target reached" is a status about the rate, not about money:
                # the tranche is not completed until a conversion is recorded.
                tranche.status = str(TrancheStatus.TARGET_REACHED)

        if not _confirmation_passed(state, settings, moment):
            state.state = str(TargetState.REACHED_UNCONFIRMED)
            evaluation.state = TargetState.REACHED_UNCONFIRMED
            evaluation.reason = (
                f"awaiting confirmation ({state.qualifying_samples} of "
                f"{settings.notifications.confirmation_samples} samples)"
            )
            outcome.evaluations.append(evaluation)
            continue

        state.state = str(TargetState.REACHED_CONFIRMED)
        evaluation.state = TargetState.REACHED_CONFIRMED

        if not tranche.notifications_enabled:
            evaluation.reason = "notifications are disabled for this tranche"
            outcome.evaluations.append(evaluation)
            continue
        if tranche.acknowledged_at is not None:
            state.state = str(TargetState.ACKNOWLEDGED)
            evaluation.state = TargetState.ACKNOWLEDGED
            evaluation.reason = "target already acknowledged"
            outcome.evaluations.append(evaluation)
            continue

        evaluation.should_notify = True
        evaluation.notification = _build_target_message(
            strategy, tranche, rate, outstanding_for(strategy, tranche), assumption
        )
        evaluation.reason = "target reached and confirmed"
        state.state = str(TargetState.NOTIFIED)
        state.last_notified_at = moment
        state.notification_count += 1
        evaluation.state = TargetState.NOTIFIED
        tranche.notification_sent_at = moment

        await audit.record(
            session,
            event_type=AuditEventType.TARGET_REACHED,
            entity_type="tranche",
            entity_id=tranche.id,
            message=(
                f"Tranche {tranche.sequence} target {tranche.target_rate} reached at {rate}. "
                "No conversion has been performed."
            ),
            after={"observed_rate": rate, "target_rate": tranche.target_rate},
        )
        outcome.evaluations.append(evaluation)

    await session.flush()
    outcome.notifications = [
        evaluation.notification
        for evaluation in outcome.evaluations
        if evaluation.notification is not None
    ]
    return outcome


# ---------------------------------------------------------------------------
# Other rule types
# ---------------------------------------------------------------------------


def walk_away_notification(
    strategy: Strategy,
    rate: Decimal,
    remaining: Decimal,
    highest_target: Decimal | None,
    assumption: calc.FeeAssumption | None,
) -> Notification:
    source, target = strategy.source_currency, strategy.target_currency
    outcome = calc.project_conversion(remaining, rate, assumption)
    net = (
        outcome.net_target_amount
        if outcome.net_target_amount is not None
        else outcome.gross_target_amount
    )
    lines = [
        f"{source} remaining: {_format_money(remaining, source)}",
        f"Estimated {'net' if outcome.net_target_amount is not None else 'gross'} "
        f"if converted now: {_format_money(net, target)}",
    ]
    if highest_target is not None:
        extra = calc.movement_value(remaining, highest_target - rate)
        lines.append(
            f"Waiting for {highest_target.quantize(Decimal('0.0001'))} adds about "
            f"{_format_money(extra, target)} before fees, but leaves the full "
            f"{_format_money(remaining, source)} exposed."
        )
    else:
        lines.append(
            f"No target above the current rate is still open. "
            f"{_format_money(remaining, source)} remains exposed while it is unconverted."
        )
    # The cost of waiting is always stated, so the message never reads as an
    # argument for holding out.
    lines.append(
        f"A one-cent reversal would be worth "
        f"{_format_money(calc.value_of_one_cent(remaining), target)}."
    )
    return Notification(
        rule_type=AlertRuleType.WALK_AWAY_REACHED,
        title=(
            f"Your {strategy.walk_away_rate.quantize(Decimal('0.0001'))} walk-away rate "
            "has been reached"
            if strategy.walk_away_rate is not None
            else "Walk-away rate reached"
        ),
        message="\n".join(lines),
        severity=Severity.NOTICE,
        entity_type="strategy",
        entity_id=str(strategy.id),
    )


def deadline_notification(
    strategy: Strategy,
    days_remaining: int,
    remaining: Decimal,
    required_before_deadline: Decimal,
    next_target_distance: Decimal | None,
) -> Notification:
    source = strategy.source_currency
    severity_enum, _message = calc.deadline_severity(days_remaining)
    lines = [
        f"{source} remaining: {_format_money(remaining, source)}",
    ]
    if required_before_deadline > ZERO:
        lines.append(
            f"Required conversion before the deadline: "
            f"{_format_money(required_before_deadline, source)}"
        )
    if next_target_distance is not None:
        lines.append(f"The next target is {next_target_distance} above the current rate.")
    lines.append("")
    lines.append("No target has been changed and nothing has been converted.")

    return Notification(
        rule_type=(
            AlertRuleType.DEADLINE_MISSED
            if days_remaining < 0
            else AlertRuleType.DEADLINE_APPROACHING
        ),
        title=(
            "Conversion deadline has passed"
            if days_remaining < 0
            else f"{days_remaining} day{'s' if days_remaining != 1 else ''} until your "
            "conversion deadline"
        ),
        message="\n".join(lines),
        severity=(
            Severity.CRITICAL
            if severity_enum in (calc.DeadlineSeverity.CRITICAL, calc.DeadlineSeverity.OVERDUE)
            else Severity.WARNING
        ),
        entity_type="strategy",
        entity_id=str(strategy.id),
    )


def reversal_notification(
    strategy: Strategy, fall: Decimal, high: Decimal, remaining: Decimal
) -> Notification:
    source, target = strategy.source_currency, strategy.target_currency
    return Notification(
        rule_type=AlertRuleType.RATE_REVERSAL,
        title=f"{source}/{target} has fallen {fall} from today's high",
        message=(
            f"Today's high was {high.quantize(Decimal('0.0001'))}.\n"
            f"Remaining exposure: {_format_money(remaining, source)}\n"
            f"Approximate reduction in conversion value: "
            f"{_format_money(calc.movement_value(remaining, fall), target)}"
        ),
        severity=Severity.WARNING,
        entity_type="strategy",
        entity_id=str(strategy.id),
    )


def provider_error_notification(provider: str, error: str, minutes_down: int) -> Notification:
    return Notification(
        rule_type=AlertRuleType.PROVIDER_ERROR,
        title=f"Rate provider {provider} has been failing for {minutes_down} minutes",
        message=(
            f"{error}\n\n"
            "Rate collection has stopped or fallen back to another provider. "
            "Targets will not be confirmed from a stale rate."
        ),
        severity=Severity.CRITICAL,
        entity_type="provider",
        entity_id=provider,
    )
