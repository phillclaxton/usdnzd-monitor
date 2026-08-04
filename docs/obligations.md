# Debts and conversion priorities

Record the NZD obligations you intend to fund by converting USD, and the app
will tell you what each costs to carry, what a better rate would be worth, and
which to fund first.

**This is decision support. It never pays, converts or transfers anything, and
it is not financial advice.** Every figure is an estimate calculated from the
values you enter and an indicative market rate.

## The idea

Two forces pull in opposite directions:

- An interest-bearing debt costs money every day it goes unpaid.
- A better USD/NZD rate reduces the USD needed to settle it.

Waiting is rational only while the second outweighs the first. The app computes
the break-even in both directions:

- **How long** a given rate improvement pays for.
- **What rate** would repay a given number of days of waiting.

## The calculations

For an obligation of `A` NZD at annual rate `r`, current rate `R`, over `d` days:

| Figure | Formula |
| --- | --- |
| Cost of waiting | `A × r × d / 365` |
| USD required now | `A / R` |
| Gain at target rate `T` | `(A / R) × (T − R)` |
| Net benefit of waiting | gain − cost |
| Break-even waiting period | gain ÷ (`A × r / 365`) |
| Break-even target rate | `R + (cost ÷ (A / R))` |

A 365-day year is used unless you enter a daily rate directly. **Monthly cost is
the annual figure divided by twelve**, not a 30-day slice, so twelve months and
one year agree.

### Worked example

NZ$256,000 at 6.04%, with the rate at 1.7200:

| | |
| --- | --- |
| Annual cost | NZ$15,462.40 |
| Daily cost | NZ$42.36 |
| Monthly cost | NZ$1,288.53 |
| USD required now | US$148,837.21 |
| Gain from a 0.01 improvement | NZ$1,488.37 |
| Days that improvement pays for | about 35 |

So a one-cent improvement is worth roughly five weeks of interest. Waiting
longer than that for it loses money.

### Zero interest

An obligation with no interest has **no financial break-even period**. The app
reports this as unknown rather than as zero or infinity — the concept does not
apply, and a number there would imply a conclusion the arithmetic does not
support. Marking the interest basis as "no interest" makes the cost zero
whatever is in the rate field, so an interest-free debt cannot accrue by
accident.

## Financial priority versus overall priority

Two scores, because they genuinely differ.

**Financial priority** counts only what money says: how close the due date is,
the daily interest cost, the size of the balance, and any maximum waiting period
you set.

**Overall priority** adds the priority you chose and the relationship
importance.

An interest-free family loan is financially unhurried and may still be the first
thing you want to fund. Ranking by interest rate alone would bury it. Both ranks
are shown, and every component of the score is listed, because a bare number is
impossible to argue with.

Weights:

| Component | Range |
| --- | --- |
| Due urgency | 0 (no date) to 80 (overdue); 60 within 7 days, 35 within 30 |
| Your priority | Critical 60, High 30, Normal 10, Low 0 |
| Relationship importance | High 35, Moderate 15, None 0 |
| Interest cost | 0 to 40, stepped by daily cost |
| Size | 0 to 10, by share of the book |
| Maximum wait | 25 if 7 days or fewer, 12 within 30, 4 beyond |
| All-or-nothing | 8, since it is harder to schedule around |

Interest cost is capped deliberately. An obligation costing NZ$100 a day is more
urgent than one costing NZ$10, but not ten times more urgent than every
non-financial consideration put together.

## Recommended actions

| State | Meaning |
| --- | --- |
| `PAY_NOW` | NZD is already available; no conversion needed |
| `CONVERT_NOW` | Due soon, critical, past the waiting limit, or waiting costs more than it earns |
| `CONVERT_PARTIAL` | Part falls due soon and partial payment is allowed; fund that part |
| `WAIT_FOR_TARGET` | No urgent date, and the target would leave you ahead |
| `WAIT_WITH_DEADLINE` | Waiting is rational, but treat a specific day as the cut-off |
| `REVIEW` | Something needed to decide is missing — no rate, a stale rate, or no target |
| `FUNDED` | Fully funded |
| `OVERDUE` | Past its due date and still outstanding |

**A stale rate never produces a recommendation to wait.** The comparison rests
on the rate being current; if it is not, the app says so and asks you to
refresh rather than reasoning from an old number.

## Fields

| Field | Notes |
| --- | --- |
| Name, type | Type affects wording only; every figure comes from the values |
| Total NZD, amount funded | Remaining is the difference unless you override it |
| Annual interest rate | Entered as a percentage in the UI, stored as a fraction |
| Interest basis | Simple annual, daily rate entered manually, or none |
| Due date, earliest payment date | Both optional |
| Priority | Critical, High, Normal, Low |
| Relationship importance | None, Moderate, High — non-financial urgency |
| Minimum payment | Honoured by the allocation planner |
| Partial payments allowed | An all-or-nothing debt is never part-funded |
| Target rate | The rate this obligation is waiting for |
| Maximum acceptable wait | In days; also becomes a break-even horizon |
| Notes, active, completed | |

## Editing and clearing

Each row of the priority table carries an **Edit** button, and the obligation's
name is a link that opens its detail. Both open in a dialog over the page, so
what you asked for appears where you asked for it. Escape or the ✕ closes it;
from the detail, **Edit** switches the same dialog to the form.

Every field is editable, including the ones that change what the recommendation
says.

Optional fields — due date, target rate, maximum acceptable wait — each have a
**Clear** button beside them. Clearing one removes it: an emptied field is sent
as an explicit null rather than being left as it was. Some browsers hide the
native clear control on a date input, which is why there is an explicit one.

Clearing a due date changes the recommendation, since the deadline may have been
the only thing forcing a conversion. Both the old and the new value are kept in
the audit trail.

## Conversion allocation

Three plans are shown side by side: critical only, critical plus anything due
within a fortnight, and everything. You can also ask what a specific amount of
USD would settle, or what would happen at a hypothetical rate.

The planner works down the overall ranking. An obligation that does not allow
partial payment is skipped rather than part-funded and the planner carries on to
the next — a half-paid all-or-nothing debt satisfies nobody. A minimum payment
is treated the same way.

**An allocation is a suggestion.** You carry out the conversion in Wise
yourself.

## Home Assistant entities

Ten portfolio sensors on the app's device:

`sensor.fx_total_active_obligations_nzd`, `fx_total_usd_required`,
`fx_total_daily_waiting_cost`, `fx_total_monthly_waiting_cost`,
`fx_next_obligation`, `fx_next_conversion_amount_usd`,
`fx_next_conversion_amount_nzd`, `fx_debt_strategy_status`,
`fx_weighted_break_even_rate`, `fx_max_rational_wait_days`.

Each obligation becomes **its own device**, named after it, carrying:

`sensor.<name>_remaining`, `_usd_required`, `_daily_waiting_cost`,
`_break_even_days`, `_break_even_rate_30_days`, `_recommendation`,
`_priority_rank`, and `binary_sensor.<name>_overdue`.

An obligation called "Meika repayment" gives
`sensor.meika_repayment_remaining`, and so on. Two obligations sharing a name
are disambiguated by ID.

A figure that cannot be calculated is published as **unknown**, never as zero.
The break-even sensor on an interest-free obligation is the case that matters.

See [examples/lovelace-obligations.yaml](examples/lovelace-obligations.yaml) for
a dashboard built entirely from native cards.

## Notifications

Optional, and each says enough to act on:

- An obligation is due within a configurable number of days
- The maximum rational waiting period has been reached
- A target rate has been reached
- The rate now exceeds the break-even rate for 30 days of waiting
- Waiting has turned net negative
- A critical obligation is still unfunded
- The recommended conversion amount has moved materially

The last one needs NZ$500 of movement before it fires, and a change between the
two waiting states is not reported at all. Messages users learn to ignore are
worse than none.

## Rate quality

Three things are never conflated:

| Label | Meaning |
| --- | --- |
| `market` | An indicative mid-market rate from your provider |
| `wise_estimate` | Derived from a Wise quote, including fees |
| `wise_quote` | An actual Wise quote, executable only inside Wise |

Everything on this page uses `market` unless stated. An indicative rate is not
what you will get.

## Troubleshooting

**USD figures show as unknown.** There is no rate yet, or the provider is
failing. Check Settings → Provider status.

**Everything says REVIEW.** Usually a stale rate. The app will not recommend
waiting on data that is out of date.

**A break-even period is blank.** The obligation has no interest, so there is no
period to compute. This is correct.

**The recommendation looks wrong for a family loan.** Check the relationship
importance. Financial priority ignores it by design; overall priority is the one
to read.

**An annual rate was refused.** Enter it as a percentage in the UI (6.04), or as
a fraction through the API (0.0604). A value above 1 through the API is rejected
as a likely percentage — it would inflate every figure a hundredfold.
