# Rate providers

The app talks to an interface, not a vendor. Any provider can be primary,
secondary or absent.

## The chain

1. **Primary** — tried first.
2. **Secondary** — tried if the primary fails, and compared against the primary
   for disagreement.
3. **Manual fallback** — the last rate you entered by hand.

A provider that fails backs off exponentially (60s doubling to a cap) and is
skipped until the window passes, including for the disagreement comparison.

If every provider fails, the refresh **reports the failure** with the reason
from each one. The last rate is kept but marked stale, and a stale rate cannot
confirm a target.

## Disagreement

When the primary and secondary differ by more than the threshold (default
0.30%, relative), the app shows a warning and withholds target confirmation
until two consecutive samples agree. This is deliberate: a target crossing that
only one of two sources agrees with is not something to wake you for.

## Manual

Always available. Enter a rate on the dashboard; it becomes the current rate and
appears in history. Import a CSV of past rates from Settings for backfill.

## Wise

See [the Wise guide](wise.md). Note that Wise's `/v1/rates` returns the
**mid-market reference rate**, not the rate a transfer settles at. The app
labels it as such everywhere. Request a quote for a figure that includes fees.

## Generic API provider

Configured in **Settings → Generic API provider**, below the provider list.

1. Pick a **preset**, or leave it on *Custom* and fill the fields in yourself.
2. Enter the **API key** if the authentication style needs one. It is stored
   encrypted outside the database and is never shown again — only the last four
   characters.
3. Press **Test**. One real call is made and the result is reported as a rate or
   as the precise reason it failed.
4. Press **Enable**, then choose the provider as **primary** or **secondary** in
   the provider list above. Enabling alone does not select it.

Every part of the request and the response mapping is configuration:

| Setting | Meaning | Example |
| --- | --- | --- |
| Base URL | Provider root | `https://api.example.com` |
| Rate path | Endpoint | `/latest` |
| Auth style | `header`, `query`, `bearer` or `none` | `header` |
| Auth name | Header or parameter name | `apikey` |
| Source / target param | Query parameter names | `base` / `symbols` |
| Rate JSON path | Dotted path; `{target}` and `{source}` expand | `rates.{target}` |
| Timestamp JSON path | Where the provider's own timestamp lives | `timestamp` |
| Convention | `target_per_source` or `source_per_target` | inverted automatically |
| Minimum seconds between calls | Respect your plan | `60` |

Leave the target parameter empty for providers that take a single combined
symbol; the app then sends `USD/NZD` in the source parameter.

### Presets

| Preset | Notes |
| --- | --- |
| Frankfurter | Free, no key. One ECB reference rate per working day — good for backfill, too coarse to drive target alerts alone. |
| exchangerate.host | Key required. Quotes keyed by concatenated pair, e.g. `USDNZD`. |
| Open Exchange Rates | Free plan supports a USD base only — which is exactly what USD → NZD needs. |
| apilayer Exchange Rates Data | Key sent in an `apikey` header. |
| Twelve Data | Single `symbol` parameter in the form `USD/NZD`. |

Presets fill in the same configurable fields; edit anything afterwards. They are
tested against each vendor's documented response shape using recorded payloads —
CI makes no live calls.

## Provider states

| State | Meaning |
| --- | --- |
| **Healthy** | Last attempt succeeded |
| **Failing** | Configured, but the last attempt failed. Backs off, retries |
| **Not configured** | Present but with nothing to work with — no credential, or no manual rate entered |
| **Not in use** | Set up, but not chosen as primary, secondary or fallback |

**Not configured is not a fault.** The manual fallback is in the chain by
default and only reached when everything above it fails, so on a working
installation it usually has nothing entered. It is shown as not configured, it
does not count towards the Home Assistant provider-problem sensor, it is not
listed under failing providers in the diagnostics bundle, and **it never sends a
notification.**

Entering a manual rate makes it configured.

### A provider only fails when it is asked

Every refresh, the app corrects the recorded state of every provider that
refresh **did not ask** — and only a provider it did ask can be reported as
failing or alerted on.

Being in the chain is not being asked, and being configured is not either. The
chain stops at the first success, so while the primary answers, the fallback
behind it is never contacted — whether or not a manual rate has been entered
against it. A failure recorded once would otherwise stand for ever, with nothing
able to clear it, and would keep raising "has been failing for *N* minutes"
alerts about a provider that is not being used.

A provider skipped *because* it is backing off still counts as in use: not
asking it is a consequence of it being broken, so it keeps its state and still
alerts.

If you have seen such an alert, it clears itself on the next poll after
upgrading. Nothing needs to be reset by hand.

## Implausible rates

A provider glitch and a real market move look identical in a single sample.
There is no way to tell them apart from one number, so the app does not try:

- A quote more than **2%** from the last good rate (configurable) is **refused
  on arrival**. It does not become the current rate, it is not charted, and no
  figure is calculated from it.
- The refusal is **not treated as a provider failure**. The call worked; it is
  the number that is in doubt. The chain carries on to the next provider, so a
  working secondary answers instead.
- If the next few polls **agree on the new level**, the market really has moved
  and the new level is accepted. The default is three quotes. A genuine jump
  costs a few minutes' delay; a one-off spike never lands.
- Nothing is hidden. The refused observation is stored, marked with the reason,
  reported in the refresh result, and written to the audit trail.

With no recent rate to compare against — a fresh install, or one returning from
a long outage — a quote is accepted. Refusing on no evidence would leave the app
unable to collect anything at all.

| Setting | Default | Meaning |
| --- | --- | --- |
| Refuse implausible jumps | on | Turn the guard off entirely |
| Implausible move threshold | 0.0200 | Relative; 0.02 is 2% |
| Quotes needed to believe a jump | 3 | Consecutive agreeing quotes before the new level is accepted |

Set the threshold wider than the pair's normal daily range, or a real move gets
delayed on every busy day. For USD/NZD, 2% is roughly three cents.

## Removing a bad point

Anything that got through before the guard existed — or a rate you entered by
hand and would rather forget — can be taken out from **Chart → Rate data
points**.

The panel lists the points in the chosen range, each measured against the median
of the dozen either side of it, and flags the ones standing further out than the
threshold. **Exclude** removes a point from the chart, the high and low, the
averages, and the hourly and daily rollups behind the longer ranges.

**The observation is kept, not deleted.** What a provider actually returned is
the evidence for why a wrong figure appeared, and keeping it is what makes the
action reversible — **Restore** puts it back. Both are written to the audit
trail.

Excluding a point rebuilds the aggregate buckets it fell in. Without that the
spike would disappear from the 7-day chart and reappear on the 3-month one,
which is drawn from the rollups rather than the raw samples.

## Polling

| Setting | Default | Notes |
| --- | --- | --- |
| Active interval | 300s | Weekdays (UTC) |
| Idle interval | 900s | Weekends |
| Minimum | 60s | Enforced floor |
| Jitter | 20s | Stops requests landing on exact clock boundaries |
| Stale after | 900s | Beyond this the rate is stale |
| Max backoff | 3600s | Cap on the failure backoff |

Do not poll more often than your provider's terms allow. The minimum-seconds
setting on the generic provider is there to make that easy to respect.

## When a rate is stale

- The dashboard shows **Stale** as a word and a glyph, not only a colour.
- Targets do not confirm, and the confirmation count does not advance.
- The walk-away, deadline and reversal rules do not fire.
- Figures are still shown, clearly marked, because a two-hour-old rate is often
  still worth seeing.
