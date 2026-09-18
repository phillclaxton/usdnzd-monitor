# First-run setup

The wizard is at **Setup** the first time you open the app, or at `/setup` any
time. It is four steps, everything it asks can be changed later, and everything
except the balance can be left empty for now.

If you would rather not use it, the dashboard shows the same position form until
something is saved.

## 1. Rate

Where the rate comes from:

- **Manual or simulation** — works immediately, needs no account.
- **Wise** — needs an API token; see [the Wise guide](wise.md).
- **Generic API provider** — any JSON rate API; see
  [rate providers](rate-providers.md).

There is a test button. Use it — a provider that looks configured but returns
nothing is worse than no provider.

The pair defaults to USD → NZD, quoted as **NZD per 1 USD** — for example
`1 USD = 1.7500 NZD`. It is fixed once anything has been recorded, so historical
records stay comparable.

## 2. What you hold

| Field | Notes |
| --- | --- |
| USD still held | What has not been converted yet. The only required field. |
| Baseline rate | The rate you would otherwise have accepted. Every improvement figure is measured against it. |
| Baseline date | Optional, and only a note to yourself about when that rate applied. |

**Without a baseline the app still works.** It records what you hold and what it
is worth, and says "set a baseline rate" wherever a gain would otherwise appear
— rather than showing a gain of zero, which would be a different and wrong
claim.

## 3. Your mortgage

| Field | Notes |
| --- | --- |
| Offset shortfall | The part of the offset account that is still unfunded. |
| Floating loan rate | **A fraction, not a percentage**: 6.04% is `0.0604`. |
| Monthly spending | Optional. Used for "months of spending held". |

Those two give the **carrying cost**: what the unfunded part of the offset costs
you for every day the money is still in USD. It is charged on the shortfall,
never on the whole balance — assuming otherwise would overstate it by an order
of magnitude.

The app refuses a loan rate above 1, because `6.04` instead of `0.0604` would
inflate the carrying cost a hundredfold and still look plausible.

## 4. Notifications

Choose one or more Home Assistant notify services. The Settings page lists what
your installation actually offers — no device name is hard-coded. Send a test.

Quiet hours hold non-critical alerts overnight; a provider outage still gets
through if you allow critical overrides.

What earns an alert, and how far the rate has to move before it says so again,
is in **Settings → FX alerts**: the absolute move, the intraday percentage, new
7/30/90-day highs, the levels you want watched, round-number breaks, how much
the position's value has to change, and your mortgage thresholds.

## After setup

1. **Record what has already been converted**, under **Conversions** — or import
   a whole history at once from the position page, or reconcile from the Wise
   API. Until you do, the realised improvement has nothing to add up.
2. **Set up your conversions in Wise.** The app does not create them and never
   will; it records what happened.
3. Leave it a day and see how many alerts you get. Every threshold is a setting,
   and the defaults are a starting point rather than a recommendation.

## Recording versus history

Two routes record a conversion, and the difference is deliberate:

| | |
| --- | --- |
| **Record a conversion** on the position page | Reduces your held balance. This is the one to use when Wise has just converted something. |
| **Conversions page**, and the CSV or JSON importers | Record history and change nothing else. The balance in an imported document is already the balance *after* those conversions. |

Neither changes your offset shortfall. Not every conversion goes to the
mortgage, and assuming one did would quietly corrupt the carrying cost — so the
shortfall moves only when you say so.

Correcting or deleting a conversion afterwards never credits the balance back
either. The balance is yours to state; a correction is restated on the position
form, and the edit dialog says so where the question actually occurs to you.
