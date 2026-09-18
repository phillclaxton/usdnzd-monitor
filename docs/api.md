# API reference

Base path: `/api/v1`. Interactive documentation, generated from the code, is at
`api/docs` on the running app.

Every monetary value and exchange rate is a JSON **string**, not a number. A
figure that cannot be calculated is `null` — never `0`.

## Health

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Overall status, version, architecture |
| `GET` | `/health/live` | Liveness; succeeds while the process runs |
| `GET` | `/health/ready` | Readiness; 503 when the database is unusable |

## Rates

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/rates/current` | Rate, status (`live`/`delayed`/`stale`/`unavailable`), changes over 1h/24h/7d/30d, 24-hour and six-month ranges |
| `GET` | `/rates/history?range=30d` | Series; resolution chosen automatically from raw samples, hourly or daily aggregates |
| `POST` | `/rates/refresh` | Poll now. **502 with the error from each provider** if none succeeded |
| `POST` | `/rates/manual` | Record a hand-entered rate |
| `POST` | `/rates/import?commit=false` | CSV import; previews by default |
| `GET` | `/rates/export?range=30d` | CSV in the format the importer accepts |
| `GET` | `/rates/providers` | Per-provider health and backoff state |

### Rate data quality

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/rates/samples?range=7d&suspicious_only=` | Stored observations, each measured against its neighbours |
| `POST` | `/rates/samples/exclude` | Stop the given samples contributing to any figure |
| `POST` | `/rates/samples/restore` | Put them back |

Both take `{"sample_ids": [...], "reason": "..."}`. An excluded sample keeps its
row: it is the evidence for why a wrong figure appeared, and keeping it is what
makes the action reversible. Excluding rebuilds the hourly and daily aggregates
covering the sample, so the point also leaves the long-range chart.

`/rates/refresh` carries `refused`: sample IDs stored but refused by the
plausibility guard on arrival.

## Provider configuration

`/rates/providers` reports health; these configure the generic provider.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/providers/presets` | Known vendors and their defaults |
| `GET` | `/providers/generic` | Stored configuration plus its status |
| `PUT` | `/providers/generic` | Update any field; `api_key` is write-only |
| `POST` | `/providers/generic/preset/{key}` | Apply a preset's defaults |
| `POST` | `/providers/generic/test` | One live call; failures come back as a message, not an error status |
| `DELETE` | `/providers/generic/credentials` | Remove the stored key |

The API key is never returned. Responses carry `key_hint` — the last four
characters — and nothing else.

## Retired: obligations

"Debts and conversion priorities" was removed. Mortgage and offset figures come
from the FX position instead — `current_offset_shortfall_nzd` and
`floating_loan_rate` on `/fx/state`, with the daily and monthly carrying cost
derived from them.

The data is kept. Every backup contains the `obligations` and
`obligation_fundings` tables, and `GET /legacy-export` downloads those two on
their own.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/legacy-export` | The retired obligations data as JSON, with a dated filename |

## FX position

The position is one row, so these paths take no ID. `GET /fx/state` answers
**200 with `"position": null`** before anything has been entered — an empty
position is a normal state to describe, not an error.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/fx/state` | The position, every derived figure, the latest conversion and the count |
| `POST` | `/fx/state` | Replace the whole position |
| `PATCH` | `/fx/state` | Change some of it. A field sent as `null` is cleared; a field left out is untouched |
| `GET` | `/fx/conversions` | History with per-row improvement against the baseline, a running cumulative, and the totals |
| `POST` | `/fx/conversions` | Record a conversion **and reduce the balance** |
| `GET` | `/fx/alerts?limit=&offset=` | What the app has said, newest first, undelivered rows included |
| `GET` | `/fx/state/export` | The whole position and its history as one document |
| `POST` | `/fx/state/import?commit=false&replace_conversions=false` | Load one; previews by default |

Two rules worth stating plainly, because both are easy to assume the other way:

- **`POST /fx/conversions` reduces `current_source_balance`; `POST /conversions`
  does not.** That difference is the only reason both exist. Correcting or
  deleting through `/conversions/{id}` never credits the balance back either —
  the balance is user-owned, and a correction is restated on the position form.
- **Neither touches the offset shortfall.** Not every conversion goes to the
  mortgage, and assuming one did would corrupt the carrying cost.

`POST /fx/state/import` writes the balance **exactly as given**: the balance in
a state document is already the balance after its conversions, so putting them
through the live path would decrement it a second time. Importing the same
history twice would double the realised gain with nothing downstream able to
tell, so a document carrying conversions is refused while any are already
recorded unless `replace_conversions=true`.

### Realised improvement is three fields, never one

Everywhere it appears — `/fx/state`, `/fx/conversions`, `/fx/state/export` — it
is `confirmed`, `estimated`, `total` and `includes_estimates`. A row whose
amounts were reconstructed rather than read off a receipt carries
`amounts_estimated`, and a consumer reading only `confirmed` therefore cannot
pick up an estimate by accident. All three are `null` together, and only when no
baseline rate is set; with a baseline and no estimated rows, `estimated` is a
real `0.00`.

## Alert settings

**Settings → FX alerts** is the screen for these. `fx_alerts` is also a section
of the settings document, so it is read and written through `GET`/`PUT /settings`
like any other, and a `PUT` replaces the whole section rather than merging it: the absolute and intraday
thresholds, which period highs to report, the watch levels, round-number breaks,
the value and mortgage thresholds, the new-high cooldown, and the minimum change
since the last alert.

## Fee models

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`POST` | `/fee-models` | List, create |
| `DELETE` | `/fee-models/{id}` | Delete |

Nothing computes with a fee model today — the figures that did belonged to the
conversion ladder — but the rows are entered by hand and are in every backup, so
they keep a way in and out.

## Conversions

The record of what actually moved. A conversion belongs to no plan, so these
paths take no strategy.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`POST` | `/conversions` | List with aggregates; record one **without** changing the balance |
| `GET`/`PUT`/`DELETE` | `/conversions/{id}` | Read, correct, delete — all audited with the previous values |
| `POST` | `/conversions/import?commit=false` | CSV import, previews by default |
| `GET` | `/conversions/export` | CSV in the format the importer accepts |

`POST /conversions` returns the created object. A `tranche_reference` column in
an imported CSV is accepted and ignored, so a file exported by an earlier
version still imports.

A repeated `provider_transaction_id` is refused with a 409, which is what makes
a reconciliation run or a re-imported file safe to repeat.

## Wise — read-only

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/wise/status`, `POST /wise/test` | Connection state; reports which call failed |
| `PUT`/`DELETE` | `/wise/credentials` | Store or remove the token |
| `GET` | `/wise/balances`, `/wise/transactions?days=90` | Read-only account access |
| `POST` | `/wise/quote?source_amount=` | Fee estimate, labelled not executable |
| `POST` | `/wise/reconcile?commit=false&days=90` | Compare and optionally import; idempotent on the Wise reference. The pair comes from the settings |
| `GET` | `/wise/execution-policy` | States that execution is not implemented |

There is no execution endpoint. `POST /wise/execute` returns an explicit refusal.

## Home Assistant

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/home-assistant/status` | Connection, discovered notify services |
| `GET` | `/home-assistant/services` | Notify services on this installation |
| `POST` | `/home-assistant/test-notification` | Send a test |
| `POST` | `/home-assistant/publish?force_discovery=` | Publish entities now |
| `GET` | `/home-assistant/entities` | Preview exactly what would be published |
| `GET` | `/home-assistant/notifications` | Delivery history, including failures |

## System

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`PUT` | `/simulation` | Status, configure |
| `POST` | `/simulation/rate` \| `/simulation/replay` \| `/simulation/reset` | Inject, replay a series, delete simulated data |
| `POST` | `/backup` | Download a backup (no credentials) |
| `POST` | `/restore?replace=false` | Restore; refuses to merge into a populated install |
| `GET` | `/diagnostics`, `/diagnostics/bundle` | Diagnostics, download |
| `POST` | `/diagnostics/integrity-check` | Read-only database check |
| `GET`/`PUT` | `/settings` | Settings document; `PUT` replaces only the supplied sections |
| `GET` | `/audit-events` | Append-only history |

## Errors

```json
{ "error": { "code": "provider_error", "message": "…", "details": { } } }
```

| Status | Meaning |
| --- | --- |
| 400 / 422 | The request violates a rule; `message` says which |
| 403 | Cross-origin state change, or a disabled feature |
| 404 | No such record |
| 409 | A conflict — a duplicate transaction, or an import that would double a recorded history |
| 429 | Rate limited; `Retry-After` says for how long |
| 502 | An upstream provider failed; `details.errors` lists each attempt |

Every response carries `X-Correlation-ID`, which also appears on the audit
events and log lines produced while handling it.
