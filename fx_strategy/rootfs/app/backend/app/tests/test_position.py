"""The FX position: its state, its history, and importing it.

The behaviours pinned here are the ones that would quietly corrupt a number
someone acts on:

* an empty position is a 200, not a 404;
* recording a conversion reduces the balance, and importing history does not;
* importing the same history twice is refused rather than doubling the gain;
* nothing hands out a total derived from an estimate without saying so.

The figures come from ``docs/examples/fx-state-example.json`` and are made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

REPO_ROOT = Path(__file__).resolve().parents[6]
EXAMPLE_STATE = REPO_ROOT / "docs" / "examples" / "fx-state-example.json"

BASE = "/api/v1/fx"


def example_document() -> dict[str, Any]:
    """The shipped example, read from disk rather than duplicated here.

    Reading the real file is the point: it is the thing a person will actually
    import, so a typo in it fails the suite instead of failing them.
    """
    return json.loads(EXAMPLE_STATE.read_text())


def position_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_currency": "USD",
        "target_currency": "NZD",
        "current_source_balance": "500000",
        "baseline_rate": "1.6000",
        "baseline_date": "2026-01-31",
        "floating_loan_rate": "0.0600",
        "current_offset_shortfall_nzd": "36500",
        "monthly_nzd_burn": "4000",
    }
    payload.update(overrides)
    return payload


async def set_rate(client: AsyncClient, rate: str) -> None:
    """Give the app a rate to value the position at."""
    response = await client.post("/api/v1/rates/manual", json={"rate": rate})
    assert response.status_code in (200, 201), response.text


# ---------------------------------------------------------------------------
# An empty position is a normal thing to describe
# ---------------------------------------------------------------------------


async def test_a_fresh_install_has_no_position_and_that_is_a_200(
    client: AsyncClient,
) -> None:
    """Not a 404. The frontend should not meet its first-run case as an error."""
    response = await client.get(f"{BASE}/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["position"] is None
    assert body["metrics"] is None
    assert body["conversion_count"] == 0
    assert body["latest_conversion"] is None


async def test_patching_a_position_that_does_not_exist_says_so(
    client: AsyncClient,
) -> None:
    response = await client.patch(f"{BASE}/state", json={"current_source_balance": "1"})
    assert response.status_code == 422, response.text
    assert "no position" in response.text.lower()


async def test_recording_a_conversion_before_a_position_says_so(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{BASE}/conversions", json={"source_amount": "100", "target_amount": "170"}
    )
    assert response.status_code == 422, response.text
    assert "no position" in response.text.lower()


# ---------------------------------------------------------------------------
# Saving and changing the position
# ---------------------------------------------------------------------------


async def test_a_position_can_be_saved_and_read_back(client: AsyncClient) -> None:
    saved = await client.post(f"{BASE}/state", json=position_payload())
    assert saved.status_code == 200, saved.text
    assert saved.json()["current_source_balance"] == "500000.0000"
    assert saved.json()["baseline_rate"] == "1.60000000"

    state = (await client.get(f"{BASE}/state")).json()
    assert state["position"]["current_offset_shortfall_nzd"] == "36500.0000"
    assert state["metrics"] is not None


async def test_a_patch_changes_only_what_it_names(client: AsyncClient) -> None:
    await client.post(f"{BASE}/state", json=position_payload())
    patched = await client.patch(f"{BASE}/state", json={"current_source_balance": "450000"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["current_source_balance"] == "450000.0000"
    # Untouched.
    assert patched.json()["baseline_rate"] == "1.60000000"
    assert patched.json()["monthly_nzd_burn"] == "4000.0000"


async def test_a_field_sent_as_null_is_cleared(client: AsyncClient) -> None:
    """Distinct from leaving it out, which is what the previous test covers."""
    await client.post(f"{BASE}/state", json=position_payload())
    patched = await client.patch(f"{BASE}/state", json={"baseline_rate": None})
    assert patched.status_code == 200, patched.text
    assert patched.json()["baseline_rate"] is None


async def test_a_loan_rate_entered_as_a_percentage_is_refused(
    client: AsyncClient,
) -> None:
    """6.04 instead of 0.0604 would overstate the carrying cost a hundredfold."""
    response = await client.post(f"{BASE}/state", json=position_payload(floating_loan_rate="6.04"))
    assert response.status_code == 422, response.text
    assert "fraction" in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"current_source_balance": "-1"},
        {"baseline_rate": "0"},
        {"current_offset_shortfall_nzd": "-5"},
        {"monthly_nzd_burn": "-5"},
    ],
)
async def test_an_impossible_position_is_refused(
    client: AsyncClient, overrides: dict[str, Any]
) -> None:
    response = await client.post(f"{BASE}/state", json=position_payload(**overrides))
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# The derived figures
# ---------------------------------------------------------------------------


async def test_the_metrics_are_the_arithmetic_the_dashboard_shows(
    client: AsyncClient,
) -> None:
    await client.post(f"{BASE}/state", json=position_payload())
    await set_rate(client, "1.7000")

    metrics = (await client.get(f"{BASE}/state")).json()["metrics"]
    assert metrics["current_target_value"] == "850000.0000"
    assert metrics["unrealised_improvement"] == "50000.0000"
    assert metrics["daily_carrying_cost"] == "6.0000"
    assert metrics["monthly_carrying_cost"] == "182.5000"
    assert metrics["months_of_burn"] == "212.5000"


async def test_without_a_baseline_the_improvement_is_unknown_not_zero(
    client: AsyncClient,
) -> None:
    """Zero would read as "you have gained nothing", which is a different claim."""
    await client.post(f"{BASE}/state", json=position_payload(baseline_rate=None))
    await set_rate(client, "1.7000")

    metrics = (await client.get(f"{BASE}/state")).json()["metrics"]
    assert metrics["unrealised_improvement"] is None
    assert metrics["realised"]["total"] is None
    assert metrics["total_improvement_confirmed"] is None
    # What does not depend on the baseline is still reported.
    assert metrics["current_target_value"] == "850000.0000"


async def test_without_a_rate_the_value_is_unknown_not_zero(client: AsyncClient) -> None:
    await client.post(f"{BASE}/state", json=position_payload())
    metrics = (await client.get(f"{BASE}/state")).json()["metrics"]
    assert metrics["current_rate"] is None
    assert metrics["current_target_value"] is None
    assert metrics["unrealised_improvement"] is None
    # The mortgage cost reads no rate, so it survives the provider being down.
    assert metrics["daily_carrying_cost"] == "6.0000"


# ---------------------------------------------------------------------------
# Recording a conversion reduces the balance
# ---------------------------------------------------------------------------


async def test_recording_a_conversion_reduces_the_balance(client: AsyncClient) -> None:
    """The one behaviour that separates this from POST /conversions."""
    await client.post(f"{BASE}/state", json=position_payload())
    response = await client.post(
        f"{BASE}/conversions",
        json={"source_amount": "50000", "target_amount": "85000", "gross_rate": "1.7000"},
    )
    assert response.status_code == 201, response.text

    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position["current_source_balance"] == "450000.0000"


async def test_recording_a_conversion_leaves_the_offset_alone(
    client: AsyncClient,
) -> None:
    """Not every conversion goes to the mortgage. Assuming one did would
    quietly corrupt the carrying cost, which is the figure most likely to be
    acted on."""
    await client.post(f"{BASE}/state", json=position_payload())
    await client.post(
        f"{BASE}/conversions", json={"source_amount": "50000", "target_amount": "85000"}
    )
    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position["current_offset_shortfall_nzd"] == "36500.0000"


async def test_correcting_a_conversion_does_not_credit_the_balance_back(
    client: AsyncClient,
) -> None:
    """The balance is user-owned. Documented on the endpoint and on the form,
    because a silent re-credit would be worse than an explicit non-one."""
    await client.post(f"{BASE}/state", json=position_payload())
    await client.post(
        f"{BASE}/conversions", json={"source_amount": "50000", "target_amount": "85000"}
    )
    (row,) = (await client.get(f"{BASE}/conversions")).json()["conversions"]

    deleted = await client.delete(f"/api/v1/conversions/{row['id']}")
    assert deleted.status_code == 200, deleted.text

    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position["current_source_balance"] == "450000.0000"


async def test_a_repeated_transaction_reference_is_still_refused(
    client: AsyncClient,
) -> None:
    await client.post(f"{BASE}/state", json=position_payload())
    payload = {
        "source_amount": "50000",
        "target_amount": "85000",
        "provider_transaction_id": "WISE-1",
    }
    assert (await client.post(f"{BASE}/conversions", json=payload)).status_code == 201
    repeat = await client.post(f"{BASE}/conversions", json=payload)
    assert repeat.status_code == 409, repeat.text


# ---------------------------------------------------------------------------
# Importing history does not decrement the balance it was just given
# ---------------------------------------------------------------------------


async def test_importing_the_example_leaves_the_balance_exactly_as_given(
    client: AsyncClient,
) -> None:
    """The subtle one. The balance in the document is already the balance
    *after* those conversions; decrementing it again would leave the position
    short by the whole converted total — here, US$125,000."""
    response = await client.post(
        f"{BASE}/state/import", params={"commit": True}, json=example_document()
    )
    assert response.status_code == 200, response.text
    assert response.json()["conversions_written"] == 4
    assert response.json()["resulting_balance"] == "500000.0000"

    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position["current_source_balance"] == "500000.0000"


async def test_an_import_previews_before_it_writes(client: AsyncClient) -> None:
    preview = await client.post(f"{BASE}/state/import", json=example_document())
    assert preview.status_code == 200, preview.text
    assert preview.json()["committed"] is False
    assert preview.json()["conversions_to_write"] == 4
    assert preview.json()["conversions_written"] == 0
    # Nothing was written.
    assert (await client.get(f"{BASE}/state")).json()["position"] is None


async def test_the_example_reconciles_to_its_documented_split(
    client: AsyncClient,
) -> None:
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    realised = (await client.get(f"{BASE}/conversions")).json()["realised"]
    assert realised["confirmed"] == "10732.5000"
    assert realised["estimated"] == "6000.0000"
    assert realised["total"] == "16732.5000"
    assert realised["includes_estimates"] is True


async def test_an_omitted_amount_is_derived_rather_than_estimated(
    client: AsyncClient,
) -> None:
    """34,000 at 1.7 means 20,000 converted. That is algebra on two known
    figures, not a guess, so the row is not flagged as an estimate."""
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    rows = (await client.get(f"{BASE}/conversions")).json()["conversions"]
    derived = next(row for row in rows if row["target_amount"] == "34000.0000")
    assert derived["source_amount"] == "20000.0000"
    assert derived["amounts_estimated"] is False
    # Its fee genuinely is unrecorded, and that is reported separately.
    assert derived["fee_source_currency"] is None
    assert derived["fee_unrecorded"] is True


async def test_a_conversion_with_only_one_known_figure_is_refused(
    client: AsyncClient,
) -> None:
    document = example_document()
    document["completed_conversions"] = [{"target_amount": "1000"}]
    response = await client.post(f"{BASE}/state/import", json=document)
    assert response.status_code == 422, response.text
    assert "at least two of" in response.text


async def test_importing_twice_is_refused_rather_than_doubling_the_gain(
    client: AsyncClient,
) -> None:
    """Nothing downstream could tell that the history had been counted twice,
    so the import has to notice on the way in."""
    first = await client.post(
        f"{BASE}/state/import", params={"commit": True}, json=example_document()
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"{BASE}/state/import", params={"commit": True}, json=example_document()
    )
    assert second.status_code == 409, second.text
    assert "twice" in second.text

    realised = (await client.get(f"{BASE}/conversions")).json()["realised"]
    assert realised["total"] == "16732.5000"


async def test_the_preview_warns_before_the_commit_is_refused(
    client: AsyncClient,
) -> None:
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    preview = await client.post(f"{BASE}/state/import", json=example_document())
    assert preview.status_code == 200, preview.text
    assert preview.json()["existing_conversions"] == 4
    assert preview.json()["conversions_to_write"] == 0
    assert any("twice" in warning for warning in preview.json()["warnings"])


async def test_replacing_the_history_re_imports_cleanly(client: AsyncClient) -> None:
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    again = await client.post(
        f"{BASE}/state/import",
        params={"commit": True, "replace_conversions": True},
        json=example_document(),
    )
    assert again.status_code == 200, again.text
    assert again.json()["replaced_conversions"] == 4
    assert again.json()["conversions_written"] == 4

    history = (await client.get(f"{BASE}/conversions")).json()
    assert len(history["conversions"]) == 4
    assert history["realised"]["total"] == "16732.5000"


# ---------------------------------------------------------------------------
# An estimate is never handed out as a fact
# ---------------------------------------------------------------------------


async def test_no_total_hides_that_an_estimate_went_into_it(
    client: AsyncClient,
) -> None:
    """Every surface reports the split, not a bare total with a flag beside it:
    a consumer that ignores the flag still cannot read the estimate as fact."""
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    await set_rate(client, "1.7000")

    for realised in (
        (await client.get(f"{BASE}/conversions")).json()["realised"],
        (await client.get(f"{BASE}/state")).json()["metrics"]["realised"],
    ):
        assert realised["includes_estimates"] is True
        assert realised["confirmed"] == "10732.5000"
        assert realised["estimated"] == "6000.0000"

    export = (await client.get(f"{BASE}/state/export")).json()
    assert export["includes_estimates"] is True
    assert export["realised_confirmed"] == "10732.5000"
    assert export["realised_estimated"] == "6000.0000"
    assert export["realised_total"] == "16732.5000"


async def test_the_headline_total_counts_only_confirmed_realised(
    client: AsyncClient,
) -> None:
    """One figure a consumer can rely on. The estimated part stays visible in
    ``realised`` rather than being folded into it."""
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    await set_rate(client, "1.7000")

    metrics = (await client.get(f"{BASE}/state")).json()["metrics"]
    # 10,732.50 confirmed realised + 50,000.00 unrealised.
    assert metrics["total_improvement_confirmed"] == "60732.5000"


async def test_entering_the_real_receipt_clears_the_estimate_everywhere(
    client: AsyncClient,
) -> None:
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    rows = (await client.get(f"{BASE}/conversions")).json()["conversions"]
    estimated = next(row for row in rows if row["amounts_estimated"])

    corrected = await client.put(
        f"/api/v1/conversions/{estimated['id']}",
        json={
            "executed_at": estimated["executed_at"],
            "source_amount": estimated["source_amount"],
            "target_amount": estimated["target_amount"],
            "gross_rate": estimated["gross_rate"],
            "amounts_estimated": False,
            "correction_reason": "Wise receipt entered.",
        },
    )
    assert corrected.status_code == 200, corrected.text

    realised = (await client.get(f"{BASE}/conversions")).json()["realised"]
    assert realised["includes_estimates"] is False
    assert realised["estimated"] == "0.0000"
    assert realised["confirmed"] == realised["total"] == "16732.5000"


# ---------------------------------------------------------------------------
# History, alerts and export
# ---------------------------------------------------------------------------


async def test_the_history_runs_newest_first_but_totals_up_oldest_first(
    client: AsyncClient,
) -> None:
    """The cumulative column only means anything in the order things happened,
    so it is computed forwards and only then displayed backwards."""
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    rows = (await client.get(f"{BASE}/conversions")).json()["conversions"]

    dates = [row["executed_at"] for row in rows]
    assert dates == sorted(dates, reverse=True)
    assert rows[0]["cumulative_improvement"] == "16732.5000"
    assert rows[-1]["cumulative_improvement"] == "4990.0000"


async def test_a_simulated_conversion_does_not_change_the_position(
    client: AsyncClient,
) -> None:
    """A replay must not alter what the position is said to be worth."""
    await client.post(f"{BASE}/state", json=position_payload())
    await client.post(
        "/api/v1/conversions",
        json={
            "executed_at": "2026-05-01T00:00:00Z",
            "source_amount": "10000",
            "target_amount": "18000",
            "simulated": True,
        },
    )
    history = (await client.get(f"{BASE}/conversions")).json()
    assert history["conversions"] == []
    assert history["realised"]["total"] == "0.0000"


async def test_the_export_carries_what_the_specification_asks_for(
    client: AsyncClient,
) -> None:
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    await set_rate(client, "1.7000")

    export = (await client.get(f"{BASE}/state/export")).json()
    assert export["source_balance"] == "500000.0000"
    assert export["baseline_rate"] == "1.60000000"
    assert export["offset_shortfall_nzd"] == "36500.0000"
    assert export["unrealised_gain_nzd"] == "50000.0000"
    assert len(export["conversions"]) == 4


async def test_the_alert_history_reads_what_was_actually_sent(
    client: AsyncClient,
) -> None:
    empty = await client.get(f"{BASE}/alerts")
    assert empty.status_code == 200
    assert empty.json() == []

    sent = await client.post("/api/v1/home-assistant/test-notification", json={})
    assert sent.status_code == 200, sent.text

    alerts = (await client.get(f"{BASE}/alerts")).json()
    assert len(alerts) == 1
    assert "test" in alerts[0]["message"].lower()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


async def test_the_position_survives_a_backup_and_restore(client: AsyncClient) -> None:
    await client.post(f"{BASE}/state/import", params={"commit": True}, json=example_document())
    document = (await client.post("/api/v1/backup")).json()
    assert document["counts"]["fx_position"] == 1

    restored = await client.post(
        "/api/v1/restore?replace=true",
        files={"file": ("backup.json", json.dumps(document), "application/json")},
    )
    assert restored.status_code == 200, restored.text

    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position is not None
    assert position["current_source_balance"] == "500000.0000"
    realised = (await client.get(f"{BASE}/conversions")).json()["realised"]
    assert realised["total"] == "16732.5000"


async def test_a_restore_will_not_merge_over_a_live_position(
    client: AsyncClient,
) -> None:
    """The guard used to count strategies alone. An install can now have a
    position and no strategy, and merging over it would duplicate it."""
    await client.post(f"{BASE}/state", json=position_payload())
    document = (await client.post("/api/v1/backup")).json()

    refused = await client.post(
        "/api/v1/restore",
        files={"file": ("backup.json", json.dumps(document), "application/json")},
    )
    assert refused.status_code == 422, refused.text
    assert "saved position" in refused.text


# ---------------------------------------------------------------------------
# The alert preferences ride the existing settings document
# ---------------------------------------------------------------------------


async def test_the_alert_preferences_need_no_endpoint_of_their_own(
    client: AsyncClient,
) -> None:
    settings = (await client.get("/api/v1/settings")).json()
    assert settings["fx_alerts"]["absolute_rate_move"] == "0.00500000"
    assert len(settings["fx_alerts"]["watch_levels"]) == 9

    updated = await client.put(
        "/api/v1/settings",
        json={"fx_alerts": {**settings["fx_alerts"], "minimum_change_since_last_alert": "0.0100"}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["fx_alerts"]["minimum_change_since_last_alert"] == "0.01000000"


async def test_converting_more_than_is_held_is_refused_not_clamped(
    client: AsyncClient,
) -> None:
    """Clamping to zero would swallow the discrepancy and leave a balance that
    is merely plausible. What has really happened is that the balance is stale,
    and only the person holding the money can say what it should be."""
    await client.post(f"{BASE}/state", json=position_payload())
    response = await client.post(
        f"{BASE}/conversions", json={"source_amount": "600000", "target_amount": "1020000"}
    )
    assert response.status_code == 422, response.text
    assert "holds 500000" in response.text

    # Nothing was recorded and the balance is untouched.
    assert (await client.get(f"{BASE}/conversions")).json()["conversions"] == []
    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position["current_source_balance"] == "500000.0000"


@pytest.mark.parametrize(
    "patch",
    [
        {"current_source_balance": "-1"},
        {"baseline_rate": "0"},
        {"floating_loan_rate": "6.04"},
        {"current_offset_shortfall_nzd": "-5"},
    ],
)
async def test_a_patch_is_not_a_back_door_past_the_checks(
    client: AsyncClient, patch: dict[str, Any]
) -> None:
    """A value a whole-document save would refuse has to be refused here too."""
    await client.post(f"{BASE}/state", json=position_payload())
    response = await client.patch(f"{BASE}/state", json=patch)
    assert response.status_code == 422, response.text

    position = (await client.get(f"{BASE}/state")).json()["position"]
    assert position["current_source_balance"] == "500000.0000"
    assert position["floating_loan_rate"] == "0.06000000"
