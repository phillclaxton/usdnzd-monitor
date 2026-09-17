"""Movement alerts, and the deduplication that makes them bearable.

The risk this whole feature carries is volume. An alert system that fires on
every poll gets switched off, and then none of it works. So most of what is
pinned here is about *not* alerting: priming on first sight, hysteresis on a
level, and the minimum-change rule that did not exist for tranche alerts.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.models.alert import AlertRuleType
from app.models.position import FxAlertState, FxPosition
from app.models.rate import RateSample
from app.providers.base import QuoteType
from app.schemas.settings import Settings
from app.services import fx_alerts, rate_service, settings_service


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    return await settings_service.load_settings(session)


async def make_position(session: AsyncSession, **overrides: object) -> FxPosition:
    values: dict[str, object] = {
        "id": 1,
        "source_currency": "USD",
        "target_currency": "NZD",
        "current_source_balance": Decimal("500000"),
        "baseline_rate": Decimal("1.6000"),
        "notes": "",
    }
    values.update(overrides)
    position = FxPosition(**values)
    session.add(position)
    await session.flush()
    return position


async def add_sample(session: AsyncSession, rate: str, *, seconds_ago: int = 0) -> RateSample:
    sample = RateSample(
        provider="test",
        source_currency="USD",
        target_currency="NZD",
        rate=Decimal(rate),
        rate_numeric=float(rate),
        quote_type=str(QuoteType.MID_MARKET),
        retrieved_at=utcnow() - timedelta(seconds=seconds_ago),
    )
    session.add(sample)
    await session.flush()
    return sample


async def observe(
    session: AsyncSession, settings: Settings, rate: str, *, seconds_ago: int = 0, **kwargs: object
) -> fx_alerts.AlertRun:
    """One poll: store a sample, then evaluate against it.

    Offsets are in seconds and stay inside the staleness window — the default
    is fifteen minutes, and an observation older than that is deliberately not
    trusted enough to alert from, which would make every test here silent.
    """
    await add_sample(session, rate, seconds_ago=seconds_ago)
    current = await rate_service.current_rate(session, settings)
    return await fx_alerts.evaluate(session, settings, current=current, **kwargs)  # type: ignore[arg-type]


def keys(run: fx_alerts.AlertRun) -> set[str]:
    return {notification.entity_id or "" for notification in run.notifications}


async def settle(session: AsyncSession, settings: Settings, rate: str) -> None:
    """Get past priming and confirmation so a test can start from a quiet state."""
    for seconds in (780, 720, 660):
        await observe(session, settings, rate, seconds_ago=seconds)


# ---------------------------------------------------------------------------
# Nothing at all on first contact
# ---------------------------------------------------------------------------


async def test_the_first_poll_after_an_upgrade_says_nothing(
    session: AsyncSession, settings: Settings
) -> None:
    """Every condition is true of a position it has never seen before. Firing
    them all at once is how someone learns to turn notifications off."""
    await make_position(session)
    run = await observe(session, settings, "1.7500")
    assert run.notifications == []
    assert all(reason == "primed" for _key, reason in run.suppressed)
    assert run.considered  # it did look


async def test_with_no_position_there_is_nothing_to_say(
    session: AsyncSession, settings: Settings
) -> None:
    run = await observe(session, settings, "1.7500")
    assert run.notifications == []
    assert run.considered == []


async def test_alerts_can_be_switched_off_entirely(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    settings.fx_alerts.enabled = False
    run = await observe(session, settings, "1.7500")
    assert run.considered == []


# ---------------------------------------------------------------------------
# A rate the app does not trust produces nothing
# ---------------------------------------------------------------------------


async def test_a_stale_rate_never_produces_an_alert(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.7000")
    # Far enough back to be stale under the configured window.
    seconds = settings.providers.stale_after_seconds + 600
    run = await observe(session, settings, "1.8000", seconds_ago=seconds)
    assert keys(run) == set()


async def test_providers_disagreeing_is_not_evidence_of_a_move(
    session: AsyncSession, settings: Settings
) -> None:
    """It is evidence of not knowing the rate, which is a different thing."""
    await make_position(session)
    await settle(session, settings, "1.7000")
    run = await observe(session, settings, "1.8000", disagreement=True)
    assert keys(run) == set()


# ---------------------------------------------------------------------------
# The gate that did not exist before
# ---------------------------------------------------------------------------


async def test_a_small_wobble_after_an_alert_says_nothing(
    session: AsyncSession, settings: Settings
) -> None:
    """Having spoken at 1.7502, 1.7508 is not news. This is the rule that stops
    a rate oscillating around a level producing one alert per cooldown for ever."""
    await make_position(session)
    await settle(session, settings, "1.7000")

    # A half-cent move earns an alert.
    await observe(session, settings, "1.7502", seconds_ago=600)
    spoke = await observe(session, settings, "1.7502", seconds_ago=540)
    assert "absolute_move" in keys(spoke)

    await observe(session, settings, "1.7508", seconds_ago=480)
    quiet = await observe(session, settings, "1.7508", seconds_ago=420)
    assert "absolute_move" not in keys(quiet)
    # Not merely suppressed by a later gate: the condition does not hold, so it
    # is never raised. That is what keeps the confirmation streak honest.
    assert "absolute_move" not in quiet.considered


async def test_a_real_move_after_an_alert_does_say_so(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.7000")
    await observe(session, settings, "1.7502", seconds_ago=600)
    await observe(session, settings, "1.7502", seconds_ago=540)

    await observe(session, settings, "1.7560", seconds_ago=480)
    again = await observe(session, settings, "1.7560", seconds_ago=420)
    assert "absolute_move" in keys(again)


async def test_one_odd_sample_is_not_enough(session: AsyncSession, settings: Settings) -> None:
    """Confirmation: a condition has to hold for consecutive samples. One
    observation can be a provider hiccup; two is the market."""
    await make_position(session)
    await settle(session, settings, "1.7000")

    spike = await observe(session, settings, "1.7600", seconds_ago=600)
    assert "absolute_move" not in keys(spike)
    assert ("absolute_move", "awaiting confirmation") in spike.suppressed

    # It goes away again, so the streak breaks and nothing is ever said.
    back = await observe(session, settings, "1.7002", seconds_ago=540)
    assert "absolute_move" not in keys(back)
    state = await session.get(FxAlertState, "absolute_move")
    assert state is not None
    assert state.notification_count == 0


# ---------------------------------------------------------------------------
# New period highs
# ---------------------------------------------------------------------------


async def test_a_new_high_compares_against_the_window_before_it(
    session: AsyncSession, settings: Settings
) -> None:
    """A window including the current sample contains it as its own maximum, so
    every sample would look like a new high. The comparison must exclude it."""
    await make_position(session)
    await settle(session, settings, "1.7000")

    await observe(session, settings, "1.7300", seconds_ago=600)
    # Rising, because the first 1.7300 is inside the second sample's window and
    # a new high has to beat it.
    run = await observe(session, settings, "1.7310", seconds_ago=540)
    assert "new_high:7d" in keys(run)


async def test_sitting_at_the_high_is_not_a_new_high(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.7000")
    await observe(session, settings, "1.7300", seconds_ago=600)
    await observe(session, settings, "1.7300", seconds_ago=540)

    # Same rate again: it no longer exceeds the previous window high.
    still = await observe(session, settings, "1.7300", seconds_ago=480)
    assert "new_high:7d" not in keys(still)
    assert "new_high:7d" not in still.considered


async def test_a_new_high_within_the_cooldown_needs_a_real_move(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.7000")
    await observe(session, settings, "1.7300", seconds_ago=600)
    spoke = await observe(session, settings, "1.7310", seconds_ago=540)
    assert "new_high:7d" in keys(spoke)

    # A hair higher, moments later: still a new high, but not worth saying again.
    await observe(session, settings, "1.7311", seconds_ago=480)
    tiny = await observe(session, settings, "1.7312", seconds_ago=420)
    assert "new_high:7d" not in keys(tiny)

    # Half a cent higher is worth saying, cooldown or not.
    real = await observe(session, settings, "1.7365", seconds_ago=360)
    assert "new_high:7d" in keys(real)


async def test_each_window_can_be_switched_off_on_its_own(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    settings.fx_alerts.alert_new_7d_high = False
    await settle(session, settings, "1.7000")
    await observe(session, settings, "1.7300", seconds_ago=600)
    run = await observe(session, settings, "1.7310", seconds_ago=540)
    assert "new_high:7d" not in keys(run)
    assert "new_high:30d" in keys(run)


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------


async def test_crossing_a_level_is_reported_once_not_on_every_poll(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.7400")

    crossed = await observe(session, settings, "1.7520", seconds_ago=600)
    assert "watch_level:1.7500" in keys(crossed)

    # Still above it, but it was not crossed again.
    for seconds in (540, 480, 420):
        again = await observe(session, settings, "1.7530", seconds_ago=seconds)
        assert "watch_level:1.7500" not in keys(again)


async def test_a_level_does_not_re_fire_while_the_rate_wobbles_across_it(
    session: AsyncSession, settings: Settings
) -> None:
    """Drifting a pip back under a level just crossed is not a crossing back
    down. The hysteresis band is what makes that true."""
    await make_position(session)
    await settle(session, settings, "1.7400")
    await observe(session, settings, "1.7520", seconds_ago=600)

    inside_band = await observe(session, settings, "1.7490", seconds_ago=540)
    assert "watch_level:1.7500" not in keys(inside_band)


async def test_falling_clear_through_a_level_is_reported(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.7400")
    await observe(session, settings, "1.7520", seconds_ago=600)

    below = await observe(session, settings, "1.7400", seconds_ago=540)
    assert "watch_level:1.7500" in keys(below)
    notification = next(n for n in below.notifications if n.entity_id == "watch_level:1.7500")
    assert "crossed below" in notification.title


async def test_round_numbers_can_be_reported_beside_the_watch_levels(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session)
    await settle(session, settings, "1.6850")
    crossed = await observe(session, settings, "1.6920", seconds_ago=600)
    assert "round_number:1.6900" in keys(crossed)


async def test_round_numbers_can_be_switched_off(session: AsyncSession, settings: Settings) -> None:
    await make_position(session)
    settings.fx_alerts.alert_round_number_breaks = False
    await settle(session, settings, "1.6850")
    crossed = await observe(session, settings, "1.6920", seconds_ago=600)
    assert not any(key.startswith("round_number") for key in keys(crossed))


# ---------------------------------------------------------------------------
# Intraday
# ---------------------------------------------------------------------------


async def test_the_intraday_open_is_the_first_sample_of_the_local_day(
    session: AsyncSession, settings: Settings
) -> None:
    """Not the last sample of the previous one, which is what asking for the
    sample at-or-before midnight returns under five-minute polling."""
    zone = fx_alerts._local_midnight_utc(settings.general.timezone, utcnow())
    yesterday = RateSample(
        provider="test",
        source_currency="USD",
        target_currency="NZD",
        rate=Decimal("1.5000"),
        rate_numeric=1.5,
        quote_type=str(QuoteType.MID_MARKET),
        retrieved_at=zone - timedelta(minutes=5),
    )
    session.add(yesterday)
    await session.flush()

    opening = await rate_service.sample_at_or_after(session, "USD", "NZD", zone)
    assert opening is None or opening.rate != Decimal("1.5000")


@pytest.mark.parametrize(
    "moment",
    [
        # Either side of the Sunday NZDT ends in 2026: the local day that
        # contains the transition is 25 hours long, so any arithmetic that
        # subtracts a fixed offset lands in the wrong day for one of these.
        datetime(2026, 4, 4, 22, 0, tzinfo=UTC),
        datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
        datetime(2026, 4, 6, 6, 0, tzinfo=UTC),
        datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
    ],
)
def test_midnight_is_built_from_the_date_not_by_subtracting_hours(
    moment: datetime,
) -> None:
    """Midnight must be midnight of the *local* day the moment falls in."""
    zone = ZoneInfo("Pacific/Auckland")
    midnight = fx_alerts._local_midnight_utc("Pacific/Auckland", moment)

    # It is genuinely midnight where the user is...
    assert midnight.astimezone(zone).time() == time.min
    # ...on the same local day as the moment, and never after it.
    assert midnight.astimezone(zone).date() == moment.astimezone(zone).date()
    assert midnight <= moment


def test_an_unknown_timezone_falls_back_rather_than_raising() -> None:
    """A bad setting must not take the alert run down with it."""
    moment = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    assert fx_alerts._local_midnight_utc("Mars/Olympus_Mons", moment) == datetime(
        2026, 4, 5, 0, 0, tzinfo=UTC
    )


# ---------------------------------------------------------------------------
# What the position is worth
# ---------------------------------------------------------------------------


async def test_a_material_change_in_value_is_worth_saying(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(session, current_source_balance=Decimal("500000"))
    await settle(session, settings, "1.7000")

    # 500,000 x 0.02 = NZ$10,000, comfortably over the 5,000 default.
    await observe(session, settings, "1.7200", seconds_ago=600)
    run = await observe(session, settings, "1.7200", seconds_ago=540)
    assert "portfolio_value" in keys(run)


async def test_a_small_change_in_value_is_not(session: AsyncSession, settings: Settings) -> None:
    await make_position(session, current_source_balance=Decimal("500000"))
    await settle(session, settings, "1.7000")
    await observe(session, settings, "1.7200", seconds_ago=600)
    await observe(session, settings, "1.7200", seconds_ago=540)

    # 500,000 x 0.001 = NZ$500.
    await observe(session, settings, "1.7210", seconds_ago=480)
    quiet = await observe(session, settings, "1.7210", seconds_ago=420)
    assert "portfolio_value" not in keys(quiet)


# ---------------------------------------------------------------------------
# The mortgage, which reads no rate
# ---------------------------------------------------------------------------


async def test_a_mortgage_milestone_still_fires_while_the_provider_is_down(
    session: AsyncSession, settings: Settings
) -> None:
    """Carrying cost does not stop being worth knowing because a rate feed is
    unreachable, so these are evaluated before the staleness check."""
    await make_position(
        session,
        current_offset_shortfall_nzd=Decimal("150000"),
        floating_loan_rate=Decimal("0.0600"),
    )
    run = await fx_alerts.evaluate(session, settings, current=None)
    assert run.notifications == []  # primed

    position = await session.get(FxPosition, 1)
    assert position is not None
    position.current_offset_shortfall_nzd = Decimal("40000")
    await session.flush()

    crossed = await fx_alerts.evaluate(session, settings, current=None)
    assert "mortgage:offset_shortfall" in keys(crossed)


async def test_a_milestone_does_not_repeat_while_it_drifts_inside_a_band(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(
        session,
        current_offset_shortfall_nzd=Decimal("150000"),
        floating_loan_rate=Decimal("0.0600"),
    )
    await fx_alerts.evaluate(session, settings, current=None)

    position = await session.get(FxPosition, 1)
    assert position is not None
    position.current_offset_shortfall_nzd = Decimal("40000")
    await session.flush()
    assert "mortgage:offset_shortfall" in keys(
        await fx_alerts.evaluate(session, settings, current=None)
    )

    # Still in the "below 50,000" band.
    position.current_offset_shortfall_nzd = Decimal("38000")
    await session.flush()
    assert "mortgage:offset_shortfall" not in keys(
        await fx_alerts.evaluate(session, settings, current=None)
    )


async def test_the_daily_cost_milestone_names_the_cost(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(
        session,
        current_offset_shortfall_nzd=Decimal("150000"),
        floating_loan_rate=Decimal("0.0600"),
    )
    await fx_alerts.evaluate(session, settings, current=None)

    position = await session.get(FxPosition, 1)
    assert position is not None
    # 36,500 x 6% / 365 = exactly 6.00 a day, under the 10 threshold.
    position.current_offset_shortfall_nzd = Decimal("36500")
    await session.flush()

    run = await fx_alerts.evaluate(session, settings, current=None)
    notification = next(
        (n for n in run.notifications if n.entity_id == "mortgage:daily_cost"), None
    )
    assert notification is not None
    assert "6.00" in notification.title
    assert "182.50" in notification.message


# ---------------------------------------------------------------------------
# What the messages may and may not say
# ---------------------------------------------------------------------------


FORBIDDEN = ("convert now", "you should convert", "buy ", "sell ", "tranche")


async def test_no_alert_tells_anyone_what_to_do(session: AsyncSession, settings: Settings) -> None:
    """The specification is explicit: no "convert now", no tranche
    recommendations, no buy or sell instructions. That analysis happens
    elsewhere and this app is not in a position to do it."""
    await make_position(
        session,
        current_offset_shortfall_nzd=Decimal("150000"),
        floating_loan_rate=Decimal("0.0600"),
    )
    await settle(session, settings, "1.7000")
    for seconds in (600, 540, 480, 420, 360, 0):
        run = await observe(session, settings, "1.7620", seconds_ago=seconds)
        for notification in run.notifications:
            text = f"{notification.title}\n{notification.message}".lower()
            for phrase in FORBIDDEN:
                assert phrase not in text, f"{notification.entity_id}: {phrase!r}"


async def test_an_alert_says_what_the_position_is_worth(
    session: AsyncSession, settings: Settings
) -> None:
    """What happened, the rate, why it matters, and the impact — per the spec."""
    await make_position(session)
    await settle(session, settings, "1.7400")
    run = await observe(session, settings, "1.7520", seconds_ago=600)

    notification = next((n for n in run.notifications if n.entity_id == "watch_level:1.7500"), None)
    assert notification is not None
    assert "1.7520" in notification.message
    assert "NZD" in notification.message
    # Against a 1.6000 baseline, 500,000 at 1.7520 is NZ$76,000 ahead.
    assert "76,000" in notification.message


# ---------------------------------------------------------------------------
# The key space and the log agree
# ---------------------------------------------------------------------------


async def test_an_alert_key_is_also_its_entity_id(
    session: AsyncSession, settings: Settings
) -> None:
    """That invariant is what lets the state table and the existing cooldown
    agree on what "the same alert" means without either importing the other."""
    await make_position(session)
    await settle(session, settings, "1.7400")
    run = await observe(session, settings, "1.7520", seconds_ago=600)

    assert run.notifications
    for notification in run.notifications:
        assert notification.entity_type == "fx_alert"
        assert notification.entity_id is not None
        assert len(notification.entity_id) <= fx_alerts.MAX_KEY_LENGTH
        assert await session.get(FxAlertState, notification.entity_id) is not None


async def test_every_rule_type_is_one_of_the_movement_types(
    session: AsyncSession, settings: Settings
) -> None:
    await make_position(
        session,
        current_offset_shortfall_nzd=Decimal("40000"),
        floating_loan_rate=Decimal("0.0600"),
    )
    await settle(session, settings, "1.7000")
    seen: set[AlertRuleType] = set()
    for seconds in (600, 540, 480, 420, 360, 0):
        run = await observe(session, settings, "1.7620", seconds_ago=seconds)
        seen.update(n.rule_type for n in run.notifications)

    assert seen <= {
        AlertRuleType.FX_ABSOLUTE_MOVE,
        AlertRuleType.FX_INTRADAY_MOVE,
        AlertRuleType.FX_NEW_HIGH,
        AlertRuleType.FX_LEVEL_CROSSED,
        AlertRuleType.FX_PORTFOLIO_VALUE,
        AlertRuleType.FX_MORTGAGE_MILESTONE,
    }
    assert seen  # something fired


# ---------------------------------------------------------------------------
# Through the pipeline, and out of the API
# ---------------------------------------------------------------------------


async def test_the_monitor_delivers_a_movement_alert(
    session: AsyncSession, settings: Settings
) -> None:
    """Delivered through the same funnel as every other notification, so quiet
    hours, the retry queue and the log apply without fx_alerts knowing they
    exist."""
    from app.home_assistant.client import set_home_assistant
    from app.services import monitor, settings_service
    from app.services.rate_service import RefreshOutcome
    from app.tests.test_alerts import FakeHomeAssistant

    settings.notifications.services = ["notify.test"]
    settings.notifications.confirmation_samples = 1
    await settings_service.save_settings(session, settings)

    fake = FakeHomeAssistant()
    set_home_assistant(fake)  # type: ignore[arg-type]
    try:
        await make_position(session)
        await add_sample(session, "1.7400", seconds_ago=120)
        await add_sample(session, "1.7520", seconds_ago=1)

        result = await monitor.run_after_refresh(session, settings, RefreshOutcome())

        assert result.notifications_delivered >= 1
        titles = [call["title"] for call in fake.calls]
        assert any("1.7500" in title for title in titles)
    finally:
        set_home_assistant(None)


async def test_entering_a_shortfall_says_so_at_once(client: AsyncClient) -> None:
    """Rather than up to five minutes later, at the next poll. Only the
    conditions that read no rate are evaluated: nothing about the market has
    changed by someone typing."""
    await client.post(
        "/api/v1/fx/state",
        json={
            "current_source_balance": "500000",
            "current_offset_shortfall_nzd": "150000",
            "floating_loan_rate": "0.0600",
        },
    )
    # The save primes; nothing is said about a state first seen.
    before = (await client.get("/api/v1/fx/alerts")).json()

    patched = await client.patch("/api/v1/fx/state", json={"current_offset_shortfall_nzd": "40000"})
    assert patched.status_code == 200, patched.text

    after = (await client.get("/api/v1/fx/alerts")).json()
    assert len(after) > len(before)
    assert any("Offset shortfall" in row["title"] for row in after)


async def test_a_failure_to_alert_never_fails_the_save(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The figure is stored by the time the alert runs. A missed notification
    is a smaller problem than a 500 on a form that actually worked."""
    await client.post("/api/v1/fx/state", json={"current_source_balance": "500000"})

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no")

    monkeypatch.setattr(fx_alerts, "evaluate", explode)
    patched = await client.patch("/api/v1/fx/state", json={"current_offset_shortfall_nzd": "40000"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["current_offset_shortfall_nzd"] == "40000.0000"


async def test_the_alert_history_carries_the_figures_it_spoke_about(
    session: AsyncSession, settings: Settings, client: AsyncClient
) -> None:
    """§6 of the specification wants the rate and the reference value beside
    the message, not just the words."""
    await make_position(session)
    await settle(session, settings, "1.7400")
    run = await observe(session, settings, "1.7520", seconds_ago=600)
    assert "watch_level:1.7500" in keys(run)

    from app.services import notifications

    for notification in run.notifications:
        await notifications.send(session, notification, settings, cooldown_minutes=0)
    await session.commit()

    rows = (await client.get("/api/v1/fx/alerts")).json()
    level = next((row for row in rows if row["entity_id"] == "watch_level:1.7500"), None)
    assert level is not None
    assert level["rate"] == "1.75200000"
    assert level["reference_value"] == "1.75000000"
