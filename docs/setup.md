# First-run setup

The wizard is at **Setup** the first time you open the app, or at `/setup` any
time. Everything it asks can be changed later.

## 1. Welcome

States what the app does, and what it does not: it does not predict rates, and
it does not move money. The targets are yours.

## 2. Currency pair

Defaults to USD → NZD, quoted as **NZD per 1 USD** — for example
`1 USD = 1.7500 NZD`. The pair is fixed once a strategy exists so historical
records stay comparable.

## 3. Amount

| Field | Default | Notes |
| --- | --- | --- |
| Total to convert | 800,000 | The whole amount the strategy plans for. |
| Available now | 0 | Exposure figures use **this**, not the total. |
| Expected arrival | — | Used for the funds-arrived alert. |
| Final deadline | — | Drives the deadline warnings. |

Setting "available now" honestly matters: if only part of the money has arrived,
the one-cent exposure figure should reflect what is actually at risk today.

## 4. Rate provider

- **Manual or simulation** — works immediately, needs no account.
- **Wise** — needs an API token; see [the Wise guide](wise.md).
- **Generic API provider** — any JSON rate API; see
  [rate providers](rate-providers.md).

There is a test button. Use it — a provider that looks configured but returns
nothing is worse than no provider.

## 5. Strategy

| Template | What it does |
| --- | --- |
| Recommended staged ladder | 15% at 1.7200, 20% at 1.7400, 25% at 1.7600, 20% at 1.7800, 20% at 1.8000 |
| Equal tranches | Five equal tranches, two cents apart from today's rate |
| Monitor only | No tranches; watch the rate and record conversions as you make them |

The recommended ladder is a **starting point**, not advice. On USD 800,000 it
allocates 120k / 160k / 200k / 160k / 160k, and if every target were reached
would produce NZD 1,409,600 at a blended 1.7620.

Also set a **walk-away rate** — the level at which finishing is good enough. The
dashboard then shows what converting the remainder now would produce against
what holding out for a higher target would add, and what stays exposed while you
wait.

The Strategy page also has an **Edit as JSON** view if you would rather paste a
whole plan than fill in fields. See
[editing a strategy as JSON](strategy-json.md).

## 6. Fees

| Option | Result |
| --- | --- |
| Exclude fees | Everything is shown gross and labelled "Fee not included". Net cannot be calculated. |
| Percentage estimate | A single percentage of the converted amount. |
| Fixed plus percentage | Both, with optional minimum and maximum. |
| Live Wise quote | Fees come from a real quote; no estimate is invented. |

Excluding fees is honest but limiting. The app will never show you a zero fee as
if it were a fact.

## 7. Notifications

Choose one or more Home Assistant notify services. The Settings page lists what
your installation actually offers — no device name is hard-coded. Send a test.

Quiet hours hold non-critical alerts overnight; a missed deadline or a provider
outage still gets through if you allow critical overrides.

## 8. Review

Shows total allocation, the gross outcome if every target is reached, estimated
fees, estimated net, the blended target rate, one-cent exposure and the
deadline. Then **Create strategy**.

## After setup

1. Create the matching Auto Conversions in Wise, if that is your plan. The app
   calculates the instructions; it does not create them.
2. Activate the strategy so targets are monitored.
3. When Wise performs a conversion, record it under **Conversions** — or
   reconcile from the Wise API. Until you do, the remaining balance and blended
   rate do not move.
