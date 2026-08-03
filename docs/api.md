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

## Obligations

Debts and commitments that may be funded by converting USD. Decision support
only: there is no endpoint here that pays, converts or transfers anything, and
`POST /obligations/pay` returns an explicit refusal.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/obligations?nzd_available=` | Every obligation, analysed and ranked twice |
| `POST` | `/obligations` | Add one |
| `GET`/`PATCH`/`DELETE` | `/obligations/{id}` | Read, edit, remove |
| `POST` | `/obligations/{id}/funding` | Record NZD applied to it |
| `GET` | `/obligations/{id}/funding` | Funding history |
| `POST` | `/obligations/{id}/complete` | Mark funded |
| `POST` | `/obligations/{id}/archive` | Remove from the active book |
| `GET` | `/obligations/portfolio?usd_on_hand=` | Totals, costs, the next thing to fund |
| `GET` | `/obligations/allocations` | The three standard conversion plans |
| `POST` | `/obligations/allocations` | A scenario at a given amount or hypothetical rate |

Two figures are withheld rather than guessed: `total_usd_required` is `null` if
any obligation could not be priced, and the "USD remaining after the critical
obligations" figures appear only when `usd_on_hand` is supplied.

## Strategies

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`POST` | `/strategies` | List, create |
| `GET`/`PUT`/`DELETE` | `/strategies/{id}` | Read, update, delete (archives when it holds conversions) |
| `POST` | `/strategies/{id}/activate` \| `/pause` \| `/resume` \| `/complete` \| `/duplicate` | Lifecycle |
| `GET` | `/strategies/{id}/summary` | The whole dashboard in one payload |
| `GET` | `/strategies/{id}/scenarios?periods=4&custom_rate=` | Side-by-side comparison |
| `GET` | `/strategies/{id}/validate` | Allocation errors and warnings |
| `GET` | `/summary` | Summary for the active strategy |
| `GET` | `/strategy-templates` | Recommended ladder, equal tranches, monitor-only |
| `GET`/`POST`/`DELETE` | `/fee-models` | Fee assumptions |

## Tranches

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`POST` | `/strategies/{id}/tranches` | List, add |
| `PUT`/`DELETE` | `/tranches/{id}` | Update (moving a target resets its alert state), delete |
| `POST` | `/tranches/reorder` | Renumber |
| `POST` | `/tranches/{id}/acknowledge` | Silence repeat alerts — **does not** mark it converted |
| `POST` | `/tranches/{id}/skip` | Close it without converting |

## Conversions

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`POST` | `/conversions` | List with aggregates; record one, optionally split across tranches |
| `GET`/`PUT`/`DELETE` | `/conversions/{id}` | Read, correct, delete — all audited with the previous values |
| `POST` | `/conversions/import?strategy_id=&commit=false` | CSV import, previews by default |
| `GET` | `/conversions/export?strategy_id=` | CSV |

## Wise — read-only

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/wise/status`, `POST /wise/test` | Connection state; reports which call failed |
| `PUT`/`DELETE` | `/wise/credentials` | Store or remove the token |
| `GET` | `/wise/balances`, `/wise/transactions?days=90` | Read-only account access |
| `POST` | `/wise/quote?source_amount=` | Fee estimate, labelled not executable |
| `POST` | `/wise/reconcile?commit=false` | Compare and optionally import; idempotent on the Wise reference |
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
| 409 | A conflict — a duplicate transaction, a tranche with conversions |
| 429 | Rate limited; `Retry-After` says for how long |
| 502 | An upstream provider failed; `details.errors` lists each attempt |

Every response carries `X-Correlation-ID`, which also appears on the audit
events and log lines produced while handling it.
