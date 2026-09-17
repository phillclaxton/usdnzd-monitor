"""Movement alerts: telling you when something changed, not what to do.

The tranche alerts this replaces answered "has the rate reached a level you
planned for?". These answer "has anything happened worth knowing about?" — a
different question, and the only one the app is in a position to answer now
that the plan lives elsewhere.

Every message here says what happened, what the rate is, why it matters and
what it is worth. **None of them says what to convert.** That judgement belongs
to whoever reads the alert.

Deduplication is the hard part
------------------------------

The existing notification cooldown is a timer keyed on
``(rule_type, entity_type, entity_id)``. A timer cannot express "don't mention
this again until it has moved another half cent", which is the rule that
actually keeps the volume tolerable, so :class:`~app.models.position.FxAlertState`
remembers the *value* each condition last spoke at. Four gates run in order:

1. **Prime on first sight.** The first time a condition is evaluated it records
   where things stand and says nothing. Without this, the first poll after an
   upgrade fires every condition at once and the natural response is to turn
   notifications off.
2. **State.** For a condition with two sides — a level crossed, a threshold
   passed — the state must have changed. A level crossed upwards only reports
   again downwards once the rate has fallen back through the hysteresis band.
3. **Movement, or time.** A condition may declare that the value must move a
   configured distance before it is said again, or that a period must pass, or
   either. This is the gate that did not exist before.
4. **Confirmation, then the notification cooldown.** Sustained conditions must
   hold for consecutive samples before they count, which is what stops one odd
   observation becoming an alert.

The key space
-------------

``absolute_move``, ``intraday_percent``, ``new_high:7d|30d|90d``,
``watch_level:<rate>``, ``round_number:<rate>``, ``portfolio_value``,
``mortgage:offset_shortfall``, ``mortgage:daily_cost``.

**A key is also the notification's ``entity_id``**, with
``entity_type="fx_alert"``, so this table and the existing cooldown agree on
what "the same alert" means without either knowing about the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.alert import AlertRuleType, Severity
from app.models.position import FxAlertState, FxPosition
from app.money import ZERO, quantize_rate, safe_divide
from app.schemas.settings import FxAlertSettings, Settings
from app.services import alert_common, position_math, rate_service
from app.services.notifications import Notification
from app.services.rate_service import CurrentRate

log = get_logger(__name__)

#: The windows reported as "a new high", and the settings flag for each.
HIGH_WINDOWS: tuple[tuple[str, int, str], ...] = (
    ("7d", 7, "alert_new_7d_high"),
    ("30d", 30, "alert_new_30d_high"),
    ("90d", 90, "alert_new_90d_high"),
)

#: Whole cents, for the round-number crossings that are not explicit watch levels.
ROUND_NUMBER_STEP = Decimal("0.01")

#: ``notification_log.entity_id`` is String(64) and an alert key is stored in it.
MAX_KEY_LENGTH = 64


@dataclass(frozen=True, slots=True)
class Candidate:
    """A condition that currently holds, before dedup has had its say."""

    key: str
    rule_type: AlertRuleType
    title: str
    message: str
    severity: Severity = Severity.INFO

    #: The rate the alert is about, remembered so the next one can measure
    #: against it.
    value: Decimal | None = None
    #: What that was measured against — the high beaten, the day's open.
    reference_value: Decimal | None = None
    #: For conditions measured in money rather than in rate.
    money_value: Decimal | None = None

    #: The side the condition is now on. When set, the alert fires only if this
    #: differs from the state last recorded: that is the hysteresis.
    state: str | None = None

    #: How far ``value`` (or ``money_value``) must move from the last alerted
    #: one before this may be said again. ``None`` means the rule does not apply.
    minimum_change: Decimal | None = None
    #: ...or how long must pass. Combined with ``minimum_change`` as an **or**:
    #: either one being satisfied is enough.
    cooldown_minutes: int | None = None

    #: Whether "does it still hold on the next sample?" is a meaningful
    #: question. It is for a sustained condition and not for a crossing, which
    #: is over by the time the next sample arrives.
    confirm: bool = True

    #: Whether the first sighting should be swallowed. Priming exists to stop a
    #: condition that was *already true* when the app first looked from firing
    #: as though it had just happened. A crossing is not like that: it is a
    #: transition between two observations, so the first one seen is real news
    #: and swallowing it would lose the event outright.
    prime: bool = True


@dataclass(slots=True)
class AlertRun:
    """What one evaluation decided, for the caller to deliver and for tests."""

    notifications: list[Notification] = field(default_factory=list)
    #: Keys that held but were held back, with the gate that stopped them.
    suppressed: list[tuple[str, str]] = field(default_factory=list)
    considered: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _money(value: Decimal | None, currency: str) -> str:
    if value is None:
        return "not calculable"
    return f"{currency} {value.quantize(Decimal('0.01')):,}"


def _rate(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}"


def _position_line(position: FxPosition, rate: Decimal) -> str:
    """The one line every rate alert ends with: what the position is worth."""
    value = position_math.current_value(position.current_source_balance, rate)
    line = (
        f"Your {_money(position.current_source_balance, position.source_currency)} "
        f"is worth about {_money(value, position.target_currency)}."
    )
    gain = position_math.unrealised_improvement(
        position.current_source_balance, rate, position.baseline_rate
    )
    if gain is not None and position.baseline_rate is not None:
        direction = "more" if gain >= ZERO else "less"
        line += (
            f"\nThat is about {_money(abs(gain), position.target_currency)} {direction} "
            f"than at your {_rate(position.baseline_rate)} baseline."
        )
    return line


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


async def _state(session: AsyncSession, key: str) -> FxAlertState:
    row = await session.get(FxAlertState, key)
    if row is None:
        row = FxAlertState(alert_key=key)
        session.add(row)
        await session.flush()
    return row


async def _reset_unqualified(session: AsyncSession, considered: set[str], firing: set[str]) -> None:
    """Break the confirmation streak of anything that stopped holding.

    Confirmation counts *consecutive* qualifying samples. A condition that was
    true and now is not has to start again, or a flicker across several polls
    would eventually add up to a confirmation it never actually earned.
    """
    lapsed = considered - firing
    if not lapsed:
        return
    rows = (
        (await session.execute(select(FxAlertState).where(FxAlertState.alert_key.in_(lapsed))))
        .scalars()
        .all()
    )
    for row in rows:
        row.qualifying_samples = 0
        row.first_qualifying_at = None
    await session.flush()


# ---------------------------------------------------------------------------
# The four gates
# ---------------------------------------------------------------------------


async def _consider(
    session: AsyncSession,
    settings: Settings,
    candidate: Candidate,
    sample_at: datetime,
) -> tuple[Notification | None, str | None]:
    """Apply the gates and record what was seen. Returns the alert, or the gate."""
    state = await _state(session, candidate.key)

    # Captured before anything is written, and from ``last_sample_at`` rather
    # than ``last_value``: a condition measured in money never sets a rate, so
    # testing the rate would leave it looking untouched for ever and priming on
    # every single evaluation.
    first_sight = state.last_sample_at is None and state.notification_count == 0

    if candidate.confirm:
        if state.qualifying_samples == 0:
            state.first_qualifying_at = sample_at
        state.qualifying_samples += 1
    state.last_sample_at = sample_at
    if candidate.value is not None:
        state.last_sample_rate = candidate.value

    def hold(reason: str) -> tuple[None, str]:
        return None, reason

    # 1. Prime on first sight.
    if first_sight and candidate.prime:
        state.state = candidate.state or state.state
        state.last_value = candidate.value
        state.last_reference_value = candidate.reference_value
        state.last_money_value = candidate.money_value
        return hold("primed")

    # 2. State: the condition must have moved to a different side.
    if candidate.state is not None and state.state == candidate.state:
        return hold("unchanged state")

    # 3. Movement, or time. Either satisfies; a condition declaring neither
    #    has already been decided by gate 2.
    if candidate.minimum_change is not None or candidate.cooldown_minutes is not None:
        moved = _moved_enough(state, candidate)
        waited = _waited_long_enough(state, candidate, sample_at)
        if not (moved or waited):
            return hold("too small a change")

    # 4. Confirmation. Then the delivery cooldown, which the caller leaves to
    #    notifications.send as the last line of defence.
    if candidate.confirm and not alert_common.confirmation_passed(
        qualifying_samples=state.qualifying_samples,
        first_qualifying_at=state.first_qualifying_at,
        sample_at=sample_at,
        required_samples=settings.notifications.confirmation_samples,
        minimum_seconds=settings.notifications.confirmation_min_seconds,
    ):
        return hold("awaiting confirmation")

    state.state = candidate.state or state.state
    state.last_value = candidate.value
    state.last_reference_value = candidate.reference_value
    state.last_money_value = candidate.money_value
    state.last_notified_at = sample_at
    state.notification_count += 1
    state.qualifying_samples = 0
    state.first_qualifying_at = None

    return (
        Notification(
            rule_type=candidate.rule_type,
            title=candidate.title,
            message=candidate.message,
            severity=candidate.severity,
            entity_type="fx_alert",
            entity_id=candidate.key,
        ),
        None,
    )


def _moved_enough(state: FxAlertState, candidate: Candidate) -> bool:
    if candidate.minimum_change is None:
        return False
    if candidate.money_value is not None:
        previous_money = state.last_money_value
        if previous_money is None:
            return True
        return abs(candidate.money_value - previous_money) >= candidate.minimum_change
    if candidate.value is None or state.last_value is None:
        return True
    return abs(candidate.value - state.last_value) >= candidate.minimum_change


def _waited_long_enough(state: FxAlertState, candidate: Candidate, sample_at: datetime) -> bool:
    if candidate.cooldown_minutes is None:
        return False
    if state.last_notified_at is None:
        return True
    elapsed = (sample_at - state.last_notified_at).total_seconds()
    return elapsed >= candidate.cooldown_minutes * 60


# ---------------------------------------------------------------------------
# Conditions that read no rate — so they still work when the provider is down
# ---------------------------------------------------------------------------


#: The band a figure is in when it is above every threshold anyone asked about.
ABOVE_ALL = "above-all"


def _band(value: Decimal, thresholds: list[Decimal]) -> str:
    """Which side of each threshold a figure sits on, as one comparable label.

    A label rather than a number so that the state gate does the work: the
    alert fires when the band changes and stays quiet while the figure drifts
    around inside one.
    """
    crossed = sorted((t for t in thresholds if value <= t), reverse=True)
    return f"below:{crossed[-1]}" if crossed else ABOVE_ALL


def position_candidates(position: FxPosition, fx: FxAlertSettings) -> list[Candidate]:
    """Mortgage milestones. These read no rate deliberately.

    Carrying cost does not stop being worth knowing because a rate provider is
    unreachable, so these are evaluated before the staleness check rather than
    after it.

    A candidate is emitted on **every** evaluation, including while the figure
    still sits above every threshold. That is what records the band it started
    in, so a shortfall that was already under NZ$50,000 the first time the app
    looked does not announce itself as though it had just got there. The state
    gate decides whether anything is actually said.
    """
    candidates: list[Candidate] = []
    target = position.target_currency

    shortfall = position.current_offset_shortfall_nzd
    if shortfall is not None and fx.offset_shortfall_thresholds:
        thresholds = list(fx.offset_shortfall_thresholds)
        band = _band(shortfall, thresholds)
        if shortfall <= ZERO:
            title = "The offset is fully funded"
        elif band == ABOVE_ALL:
            title = f"Offset shortfall is back above {_money(max(thresholds), target)}"
        else:
            title = f"Offset shortfall has fallen below {_money(_band_edge(band), target)}"
        candidates.append(
            Candidate(
                key="mortgage:offset_shortfall",
                rule_type=AlertRuleType.FX_MORTGAGE_MILESTONE,
                title=title,
                message=(
                    f"The unfunded part of the offset is now {_money(shortfall, target)}.\n"
                    "Nothing has been converted or moved: this reflects the figure you "
                    "last entered."
                ),
                severity=Severity.NOTICE,
                money_value=shortfall,
                state=band,
                confirm=False,
            )
        )

    daily = position_math.daily_carrying_cost(shortfall, position.floating_loan_rate)
    if daily is not None and fx.daily_cost_thresholds:
        thresholds = list(fx.daily_cost_thresholds)
        band = _band(daily, thresholds)
        monthly = position_math.monthly_carrying_cost(shortfall, position.floating_loan_rate)
        if band == ABOVE_ALL:
            title = f"Daily carrying cost is back above {_money(max(thresholds), target)}"
        else:
            title = f"Daily carrying cost is down to {_money(daily, target)}"
        candidates.append(
            Candidate(
                key="mortgage:daily_cost",
                rule_type=AlertRuleType.FX_MORTGAGE_MILESTONE,
                title=title,
                message=(
                    f"Waiting now costs about {_money(daily, target)} a day, "
                    f"{_money(monthly, target)} a month, on the unfunded part of the "
                    "offset.\n"
                    "Charged on the shortfall only, never on the whole balance held."
                ),
                severity=Severity.INFO,
                money_value=daily,
                state=band,
                confirm=False,
            )
        )
    return candidates


def _band_edge(band: str) -> Decimal:
    """The threshold a ``below:`` band was named after."""
    return Decimal(band.split(":", 1)[1])


# ---------------------------------------------------------------------------
# Conditions that need a rate the app trusts
# ---------------------------------------------------------------------------


def _local_midnight_utc(timezone: str, moment: datetime) -> datetime:
    """Midnight of ``moment``'s local day, in UTC.

    Built by combining the local date with ``time.min`` and converting, never
    by subtracting hours: a day on which the clocks change is 23 or 25 hours
    long and arithmetic on the offset silently lands in the wrong day.
    """
    try:
        zone = ZoneInfo(timezone)
    except Exception:  # pragma: no cover - a bad tz falls back rather than raising
        zone = ZoneInfo("UTC")
    local_date = moment.astimezone(zone).date()
    return datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)


async def rate_candidates(
    session: AsyncSession,
    settings: Settings,
    position: FxPosition,
    current: CurrentRate,
    states: dict[str, FxAlertState],
) -> list[Candidate]:
    """Everything measured against the rate. Caller has checked it is trusted.

    ``states`` is what each condition last spoke at, so a condition that is
    *relative to the last alert* — moved another half cent, gained another
    NZ$5,000 — can decide for itself whether it holds, instead of firing every
    poll and leaving a later gate to swallow it.
    """
    rate = current.rate
    sample = current.sample
    if rate is None or sample is None:
        return []

    fx = settings.fx_alerts
    source = position.source_currency
    target = position.target_currency
    pair = f"{source}/{target}"
    context = _position_line(position, rate)
    candidates: list[Candidate] = []

    # --- A. absolute movement since the last thing we said ------------------
    # The condition is the move itself, not "there is a rate". Emitting a
    # candidate on every poll would make the confirmation streak count polls
    # rather than consecutive *qualifying* samples, and confirmation would stop
    # meaning anything.
    spoke_at = states["absolute_move"].last_value if "absolute_move" in states else None
    if spoke_at is None or abs(rate - spoke_at) >= fx.absolute_rate_move:
        moved_from = spoke_at if spoke_at is not None else rate
        candidates.append(
            Candidate(
                key="absolute_move",
                rule_type=AlertRuleType.FX_ABSOLUTE_MOVE,
                title=f"{pair} has moved to {_rate(rate)}",
                message=(
                    f"{pair} is now {_rate(rate)}, "
                    f"from {_rate(moved_from)} when this was last mentioned.\n\n{context}"
                ),
                value=rate,
                reference_value=moved_from,
            )
        )

    # --- B. intraday move against the day's opening rate --------------------
    opening = await rate_service.sample_at_or_after(
        session,
        source,
        target,
        _local_midnight_utc(settings.general.timezone, sample.retrieved_at),
    )
    # No sample yet today: skip rather than anchor on yesterday's close, which
    # would report a move that did not happen today.
    if opening is not None and opening.rate > ZERO and opening.id != sample.id:
        percent = safe_divide((rate - opening.rate) * Decimal(100), opening.rate)
        if percent is not None and abs(percent) >= fx.intraday_percent_move:
            direction = "up" if percent > ZERO else "down"
            candidates.append(
                Candidate(
                    key="intraday_percent",
                    rule_type=AlertRuleType.FX_INTRADAY_MOVE,
                    title=(
                        f"{pair} is {direction} {abs(percent).quantize(Decimal('0.01'))}% today"
                    ),
                    message=(
                        f"{pair} opened at {_rate(opening.rate)} and is now {_rate(rate)}, "
                        f"a larger-than-normal move for one day.\n\n{context}"
                    ),
                    severity=Severity.NOTICE,
                    value=rate,
                    reference_value=opening.rate,
                    state=direction,
                    minimum_change=fx.minimum_change_since_last_alert,
                )
            )

    # --- C. new period highs ------------------------------------------------
    for label, days, flag in HIGH_WINDOWS:
        if not getattr(fx, flag):
            continue
        window = await rate_service.extremes(
            session,
            source,
            target,
            sample.retrieved_at - timedelta(days=days),
            until=sample.retrieved_at,
        )
        # The window excludes this observation on purpose: one that included it
        # would contain it as its own maximum and every sample would look new.
        if window.high is None or rate <= window.high:
            continue
        candidates.append(
            Candidate(
                key=f"new_high:{label}",
                rule_type=AlertRuleType.FX_NEW_HIGH,
                title=f"{pair} has reached a new {label} high of {_rate(rate)}",
                message=(f"The previous {label} high was {_rate(window.high)}.\n\n{context}"),
                severity=Severity.NOTICE,
                value=rate,
                reference_value=window.high,
                minimum_change=fx.minimum_change_since_last_alert,
                cooldown_minutes=fx.new_high_cooldown_minutes,
            )
        )

    # --- D. levels crossed --------------------------------------------------
    previous = await _previous_rate(session, current)
    candidates.extend(_level_candidates(fx, settings, pair, rate, previous, context))

    # --- E. what the remaining balance is worth -----------------------------
    value = position_math.current_value(position.current_source_balance, rate)
    if value is not None and position.current_source_balance > ZERO:
        previous_value = (
            states["portfolio_value"].last_money_value if "portfolio_value" in states else None
        )
        moved = previous_value is None or abs(value - previous_value) >= (
            fx.material_nzd_value_change
        )
        if moved:
            severity = Severity.INFO
            if previous_value is not None and abs(value - previous_value) >= (
                fx.secondary_nzd_value_change
            ):
                # The louder threshold: a move worth interrupting someone for.
                severity = Severity.NOTICE
            change = (
                f"about {_money(abs(value - previous_value), target)} "
                f"{'more' if value >= previous_value else 'less'} than when this was last "
                "mentioned.\n"
                if previous_value is not None
                else ""
            )
            candidates.append(
                Candidate(
                    key="portfolio_value",
                    rule_type=AlertRuleType.FX_PORTFOLIO_VALUE,
                    title=f"Your position is now worth {_money(value, target)}",
                    message=(
                        f"{pair} is {_rate(rate)} — {change}\n{context}\n\n"
                        "This is the value of what is still unconverted; it moves with the "
                        "rate, and nothing has been bought or sold."
                    ),
                    severity=severity,
                    value=rate,
                    money_value=value,
                )
            )

    return candidates


async def _previous_rate(session: AsyncSession, current: CurrentRate) -> Decimal | None:
    """The trusted observation immediately before this one.

    A crossing needs two points. Without a previous sample there is no crossing
    to report, only a rate that happens to sit somewhere.
    """
    if current.sample is None:
        return None
    row = await rate_service.sample_at_or_before(
        session,
        current.sample.source_currency,
        current.sample.target_currency,
        current.sample.retrieved_at - timedelta(microseconds=1),
    )
    return row.rate if row else None


def _level_candidates(
    fx: FxAlertSettings,
    settings: Settings,
    pair: str,
    rate: Decimal,
    previous: Decimal | None,
    context: str,
) -> list[Candidate]:
    """Watch levels and whole-cent round numbers actually crossed.

    Reported on the *crossing*, not on sitting above a level, so the same level
    does not announce itself on every poll. The state gate then keeps it quiet
    until the rate has come back through the hysteresis band, which is a
    stronger rule than any timer — so these carry no cooldown at all. A timer
    on top could only lose a genuine second crossing.
    """
    if previous is None:
        return []

    hysteresis = settings.notifications.reset_hysteresis
    levels: list[tuple[str, Decimal]] = [
        ("watch_level", quantize_rate(level)) for level in fx.watch_levels
    ]
    if fx.alert_round_number_breaks:
        crossed_round = position_math.round_number_levels(previous, rate, ROUND_NUMBER_STEP)
        explicit = {value for _prefix, value in levels}
        levels.extend(("round_number", value) for value in crossed_round if value not in explicit)

    candidates: list[Candidate] = []
    for prefix, level in levels:
        rising = previous < level <= rate
        # Falling uses the hysteresis band: drifting a hair back under a level
        # just crossed is not a crossing back down.
        falling = rate <= level - hysteresis < previous
        if not (rising or falling):
            continue
        direction = "above" if rising else "below"
        candidates.append(
            Candidate(
                key=_level_key(prefix, level),
                rule_type=AlertRuleType.FX_LEVEL_CROSSED,
                title=f"{pair} crossed {direction} {_rate(level)}",
                message=(
                    f"{pair} moved from {_rate(previous)} to {_rate(rate)}.\n\n{context}\n\n"
                    "A level worth noticing, not an instruction to transact."
                ),
                severity=Severity.NOTICE,
                value=rate,
                reference_value=level,
                state=direction,
                confirm=False,
                prime=False,
            )
        )
    return candidates


def _level_key(prefix: str, level: Decimal) -> str:
    key = f"{prefix}:{_rate(level)}"
    return key[:MAX_KEY_LENGTH]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


async def evaluate(
    session: AsyncSession,
    settings: Settings,
    *,
    current: CurrentRate | None = None,
    disagreement: bool = False,
    position_only: bool = False,
) -> AlertRun:
    """Work out what is worth saying, and record what was seen either way.

    Returns the notifications to deliver; it does not deliver them, so quiet
    hours, the queue and the log stay in one place — ``monitor._deliver``.

    ``position_only`` evaluates just the conditions that read no rate, which is
    what ``PATCH /fx/state`` wants: entering a new shortfall should say so at
    once rather than at the next poll.
    """
    run = AlertRun()
    fx = settings.fx_alerts
    if not fx.enabled:
        return run

    position = await session.get(FxPosition, 1)
    if position is None:
        return run

    # Read once. Conditions that are relative to the last alert need to see it
    # before they can decide whether they hold at all.
    states = {
        row.alert_key: row for row in (await session.execute(select(FxAlertState))).scalars().all()
    }

    moment = current.sample.retrieved_at if current and current.sample else None
    candidates = position_candidates(position, fx)

    if not position_only and current is not None:
        # A rate the app does not trust must not produce an alert. The mortgage
        # conditions above are already collected, because they read no rate.
        if current.rate is None or current.is_stale:
            log.debug("fx_alerts_rate_not_trusted", status=current.status)
        elif disagreement:
            # Providers disagreeing is not evidence of a move; it is evidence of
            # not knowing the rate. Nothing is counted towards confirmation.
            log.debug("fx_alerts_provider_disagreement")
        else:
            candidates.extend(await rate_candidates(session, settings, position, current, states))

    # Nothing to date the observation by — a position-only evaluation from the
    # state form, not a poll. The wall clock is right here: the change being
    # reacted to happened when someone typed it.
    if moment is None:
        moment = utcnow()

    firing = {candidate.key for candidate in candidates}
    for candidate in candidates:
        run.considered.append(candidate.key)
        notification, gate = await _consider(session, settings, candidate, moment)
        if notification is not None:
            run.notifications.append(notification)
        elif gate is not None:
            run.suppressed.append((candidate.key, gate))

    await _reset_unqualified(session, _confirmable_key_space(), firing)
    await session.flush()
    return run


def _confirmable_key_space() -> set[str]:
    """The keys whose confirmation streak has to be broken when they lapse.

    Only the sustained conditions: a crossing is over by the next sample, so
    there is no streak to break and nothing to reset.
    """
    keys = {"absolute_move", "intraday_percent", "portfolio_value"}
    keys.update(f"new_high:{label}" for label, _days, _flag in HIGH_WINDOWS)
    return keys


def alert_value(state: FxAlertState) -> dict[str, str | None]:
    """The stored figures, for ``GET /fx/alerts`` to show beside a message."""
    return {
        "rate": format(state.last_value, "f") if state.last_value is not None else None,
        "reference_value": (
            format(state.last_reference_value, "f")
            if state.last_reference_value is not None
            else None
        ),
        "money_value": (
            format(state.last_money_value, "f") if state.last_money_value is not None else None
        ),
    }


__all__ = [
    "AlertRun",
    "Candidate",
    "alert_value",
    "evaluate",
    "position_candidates",
    "rate_candidates",
]
