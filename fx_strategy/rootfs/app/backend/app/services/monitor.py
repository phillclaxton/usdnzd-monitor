"""The post-refresh pipeline.

Runs after every rate sample: advance each strategy's target state machine,
check the walk-away, deadline and reversal rules, chase provider outages, and
deliver whatever that produced.

This module is deliberately the only place that decides *whether* to notify, so
the rules cannot drift apart across call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.alert import Severity
from app.models.strategy import Strategy, StrategyStatus
from app.money import ZERO, quantize_rate
from app.schemas.settings import Settings
from app.services import alert_service, notifications, rate_service
from app.services import calculations as calc
from app.services import strategy_service as strategies
from app.services.notifications import Notification
from app.services.rate_service import RefreshOutcome

log = get_logger(__name__)


@dataclass(slots=True)
class MonitorResult:
    evaluated_strategies: int = 0
    notifications_attempted: int = 0
    notifications_delivered: int = 0
    suppressed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_after_refresh(
    session: AsyncSession, settings: Settings, outcome: RefreshOutcome
) -> MonitorResult:
    """Scheduler callback: react to a new rate sample."""
    result = MonitorResult()

    # Retry anything queued while Home Assistant was unavailable, first.
    try:
        await notifications.retry_queued(session, settings)
    except Exception as exc:
        result.errors.append(f"queued retry failed: {exc}")
        log.warning("queued_retry_failed", error=str(exc))

    await _check_provider_health(session, settings, outcome, result)

    current = await rate_service.current_rate(session, settings)
    rate = current.rate
    rate_is_stale = current.is_stale

    # Confirmation spacing is measured against when the samples were observed,
    # not when this code ran, so a replay exercises the same rules that live
    # polling does.
    observed_at = current.sample.retrieved_at if current.sample is not None else None

    for strategy in await strategies.monitored_strategies(session):
        result.evaluated_strategies += 1
        await _evaluate_strategy(
            session,
            settings,
            strategy,
            rate=rate,
            rate_is_stale=rate_is_stale,
            disagreement=outcome.disagreement_exceeded,
            observed_at=observed_at,
            result=result,
        )
    return result


async def _deliver(
    session: AsyncSession,
    settings: Settings,
    notification: Notification,
    result: MonitorResult,
    *,
    cooldown_minutes: int | None = None,
) -> None:
    result.notifications_attempted += 1
    delivery = await notifications.send(
        session, notification, settings, cooldown_minutes=cooldown_minutes
    )
    if delivery.delivered:
        result.notifications_delivered += 1
    elif delivery.suppressed_reason:
        result.suppressed.append(f"{notification.rule_type}: {delivery.suppressed_reason}")


async def _evaluate_strategy(
    session: AsyncSession,
    settings: Settings,
    strategy: Strategy,
    *,
    rate: Decimal | None,
    rate_is_stale: bool,
    disagreement: bool,
    result: MonitorResult,
    observed_at: datetime | None = None,
) -> None:
    fee_model = await strategies.get_fee_model(session, strategy.fee_model_id)
    assumption = strategies.fee_assumption_from(fee_model)

    alerts = await alert_service.evaluate_targets(
        session,
        strategy,
        settings,
        rate=rate,
        rate_is_stale=rate_is_stale,
        sample_at=observed_at,
        provider_disagreement=disagreement,
        assumption=assumption,
    )
    for notification in alerts.notifications:
        await _deliver(session, settings, notification, result)

    if rate is None or rate_is_stale:
        # Every remaining rule is rate-driven; a stale rate must not trigger one.
        return

    remaining = strategies.remaining_amount(strategy)
    await _check_walk_away(session, settings, strategy, rate, remaining, assumption, result)
    await _check_deadline(session, settings, strategy, rate, remaining, result)
    await _check_reversal(session, settings, strategy, rate, remaining, result)


async def _check_walk_away(
    session: AsyncSession,
    settings: Settings,
    strategy: Strategy,
    rate: Decimal,
    remaining: Decimal,
    assumption: calc.FeeAssumption | None,
    result: MonitorResult,
) -> None:
    if strategy.walk_away_rate is None or remaining <= ZERO or rate < strategy.walk_away_rate:
        return
    outstanding = [tranche.target_rate for tranche in strategies.open_tranches(strategy)]
    highest = max((target for target in outstanding if target > rate), default=None)
    notification = alert_service.walk_away_notification(
        strategy, rate, remaining, highest, assumption
    )
    # A daily reminder at most: the walk-away level can sit reached for days.
    await _deliver(session, settings, notification, result, cooldown_minutes=24 * 60)


async def _check_deadline(
    session: AsyncSession,
    settings: Settings,
    strategy: Strategy,
    rate: Decimal,
    remaining: Decimal,
    result: MonitorResult,
) -> None:
    days = strategies.days_until(strategy.final_deadline)
    if days is None or remaining <= ZERO:
        return
    thresholds = sorted(settings.notifications.deadline_warning_days, reverse=True)
    if days >= 0 and days not in thresholds:
        return

    required = ZERO
    for requirement in strategy.requirements:
        requirement_days = strategies.days_until(requirement.due_date)
        if requirement_days is not None and requirement_days <= max(thresholds or [30]):
            required += calc.required_conversion_shortfall(
                strategies.requirement_amount(strategy, requirement),
                strategies.converted_source_total(strategy),
            )

    next_tranche = strategies.next_target(strategy, rate)
    distance = quantize_rate(next_tranche.target_rate - rate) if next_tranche is not None else None
    notification = alert_service.deadline_notification(
        strategy, days, remaining, required, distance
    )
    await _deliver(session, settings, notification, result, cooldown_minutes=12 * 60)


async def _check_reversal(
    session: AsyncSession,
    settings: Settings,
    strategy: Strategy,
    rate: Decimal,
    remaining: Decimal,
    result: MonitorResult,
) -> None:
    threshold = settings.notifications.reversal_threshold
    if threshold <= ZERO or remaining <= ZERO:
        return
    _low, high = await rate_service.extremes(
        session,
        strategy.source_currency,
        strategy.target_currency,
        utcnow() - timedelta(hours=24),
    )
    if high is None:
        return
    fall = quantize_rate(high - rate)
    if fall < threshold:
        return
    notification = alert_service.reversal_notification(strategy, fall, high, remaining)
    await _deliver(session, settings, notification, result, cooldown_minutes=6 * 60)


async def _check_provider_health(
    session: AsyncSession,
    settings: Settings,
    outcome: RefreshOutcome,
    result: MonitorResult,
) -> None:
    """Notify once a provider has been failing for longer than the threshold.

    A single failed poll is not worth waking someone for; a provider that has
    been down for half an hour is.

    Only a provider this cycle actually asked can be reported as failing. A
    fallback sitting behind a healthy primary is never contacted, so whatever
    its status row says, there is no outage to report — and a provider with
    nothing entered is a blank setting, not an outage at three in the morning.
    """
    threshold = timedelta(seconds=settings.providers.error_notify_after_seconds)
    now = utcnow()
    for status in await rate_service.provider_statuses(session):
        if status.healthy or status.failing_since is None:
            continue
        if status.provider not in outcome.polled or status.provider in outcome.unconfigured:
            continue
        down_for = now - status.failing_since
        if down_for < threshold:
            continue
        notification = alert_service.provider_error_notification(
            status.provider,
            status.last_error or "The provider returned an error.",
            int(down_for.total_seconds() // 60),
        )
        await _deliver(session, settings, notification, result, cooldown_minutes=6 * 60)


async def send_test_notification(
    session: AsyncSession, settings: Settings, *, services: list[str] | None = None
) -> notifications.DeliveryResult:
    """Send a message that is unmistakably a test."""
    notification = Notification(
        rule_type=alert_service.AlertRuleType.TARGET_REACHED,
        title="FX Strategy Manager test notification",
        message=(
            "This is a test. No target has been reached and nothing has been converted.\n\n"
            "If you can read this, notifications are configured correctly."
        ),
        severity=Severity.INFO,
        entity_type="test",
        entity_id="test",
        services=services,
    )
    return await notifications.send(session, notification, settings, bypass_cooldown=True)


async def check_strategy_completion(session: AsyncSession, strategy: Strategy) -> bool:
    """Mark a strategy completed when nothing is left to convert.

    Completion follows recorded conversions, never a target being reached.
    """
    if strategy.status != str(StrategyStatus.ACTIVE):
        return False
    if strategies.remaining_amount(strategy) > ZERO:
        return False
    if not strategy.conversions:
        return False
    await strategies.set_status(session, strategy, StrategyStatus.COMPLETED, actor="system")
    return True
