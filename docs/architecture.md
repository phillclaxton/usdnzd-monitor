# Architecture

One container, one process, one database. There are no microservices here.

```
Home Assistant
   │  Ingress (authenticated)          MQTT discovery / REST fallback
   ▼                                            ▲
┌──────────────────────────────────────────────────────────┐
│ addon_fx_strategy                                        │
│                                                          │
│  React SPA (Vite build, served by FastAPI)               │
│      │  relative URLs resolved against <base href>       │
│      ▼                                                   │
│  FastAPI  ── /api/v1/…                                   │
│      │                                                   │
│      ├── services/         business rules                │
│      │     calculations.py     pure Decimal maths        │
│      │     strategy_service    persistence               │
│      │     summary_service     dashboard payload         │
│      │     alert_service       target state machine      │
│      │     notifications       delivery + suppression    │
│      │     monitor             post-refresh pipeline     │
│      │     conversion_service  the financial record      │
│      │     publisher           entity publication        │
│      │     wise_service        read-only Wise            │
│      │     backup / simulation                           │
│      │                                                   │
│      ├── providers/        rate sources (vendor-specific)│
│      ├── home_assistant/   REST client, MQTT, entities   │
│      ├── scheduler/        APScheduler jobs              │
│      └── models/           SQLAlchemy → SQLite (WAL)     │
│                                                          │
│  /data/fx_strategy.db      /data/secrets.json (0600)     │
└──────────────────────────────────────────────────────────┘
              │
              ▼  HTTPS only, verification never disabled
      Rate provider / Wise API
```

## Layering rules

- **`services/calculations.py` knows nothing but Decimals.** No database, no
  network, no clock beyond what is passed in. Every financial rule lives there
  and is directly testable. It is held to 95% coverage.
- **Only `providers/` knows about a vendor.** Everything else talks to the
  `FxRateProvider` protocol and receives rates already normalised to *target per
  one source unit*.
- **`monitor.py` is the only place that decides whether to notify.** Keeping
  that decision in one module is what stops the suppression rules drifting apart
  between call sites.
- **`home_assistant/entities.py` is the single entity table.** MQTT discovery,
  MQTT state publication and the REST fallback all read from it, so they cannot
  disagree about what an entity is called or what its state means.

## Money never touches a float

SQLite has no decimal type, and SQLAlchemy's `Numeric` degrades to binary
floating point on it. Financial columns therefore use `DecimalText`, a
`TypeDecorator` storing a canonical fixed-scale string:

| Type | Scale | Used for |
| --- | --- | --- |
| `RateText` | 8 places | exchange rates |
| `MoneyText` | 4 places | amounts and fees |

Round-tripping is exact, and a test asserts it. `rate_samples` additionally
carries an unindexed `rate_numeric` float column so SQL `MIN`/`MAX` over a year
of samples stays fast; the matching rows are then re-read, so no displayed
figure ever comes from that float.

Decimals cross the API boundary as JSON **strings**. Emitting them as JSON
numbers would hand the browser a float and reintroduce exactly the rounding this
design exists to avoid. The frontend's `lib/decimal.ts` formats digit strings
directly and never calls `Number()` on a financial value.

## Request flow under Ingress

1. The Supervisor forwards a request with `X-Ingress-Path: /api/hassio_ingress/<token>`.
2. `web.py` injects `<base href="/api/hassio_ingress/<token>/">` into `index.html`.
3. The bundle, built with Vite `base: './'`, resolves every asset relative to it.
4. `lib/basePath.ts` derives the router `basename` and every API and WebSocket
   URL from `document.baseURI`.

Nothing anywhere assumes the app is served from `/`. The end-to-end suite runs
behind a proxy that mimics Ingress, so a regression here fails the tests rather
than only failing on a real installation.

## Background work

`APScheduler` runs two jobs in the same process:

- **Rate refresh** — interval from settings, with jitter, separate active and
  idle cadences, and a staggered first run so a Home Assistant restart does not
  fire every add-on's outbound calls at once. Each run polls the provider chain,
  then hands the outcome to the registered callbacks: `monitor.run_after_refresh`
  and `publisher.after_refresh`.
- **Housekeeping** — every six hours: build hourly and daily aggregates, purge
  raw samples past the retention window (always *after* aggregating), and
  checkpoint the write-ahead log.

A failing callback is logged and swallowed, so a broken consumer cannot lose a
rate sample that has already been collected.

## Failure behaviour

| Failure | Behaviour |
| --- | --- |
| A provider errors | Fall through the chain; per-provider exponential backoff; the last rate is retained but marked stale and never presented as live. |
| Every provider errors | The refresh reports failure with the reason from each. No sample is invented. |
| Providers disagree | A warning, and target confirmation is withheld until two consecutive samples agree. |
| The rate is stale | Targets cannot confirm from it, and the confirmation count does not advance. |
| Home Assistant is down | Notifications queue (bounded at 50, six attempts) and retry on the next cycle. Rate collection continues. |
| MQTT is down | Reconnect with backoff; entities go unavailable via the last-will message rather than showing a frozen value. |
| The database is unusable | Readiness fails. Nothing attempts automatic repair. |
| Wise is down | Wise features report unavailable. It is never inferred that no conversion happened. |
