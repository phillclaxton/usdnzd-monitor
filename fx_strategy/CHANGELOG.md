# Changelog

All notable changes to this app are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-01

Rate monitoring.

### Added

- Provider abstraction (`FxRateProvider`) with manual, Wise, generic HTTP and
  simulation implementations. Nothing outside `app.providers` knows about a
  specific vendor.
- Presets for five known rate vendors, each just a set of defaults for the same
  configurable request and response mapping.
- Provider chain with automatic fallback, exponential backoff per provider and
  a disagreement check that withholds target confirmation when two sources
  differ by more than the configured threshold.
- Rate storage with exact Decimals, hourly and daily aggregates built before any
  retention purge, and staleness that distinguishes live, delayed and stale.
- Background scheduler with jitter, separate market-active and idle intervals,
  and a housekeeping job that aggregates, purges and checkpoints the database.
- Encrypted credential store at `/data/secrets.json`, mode 0600, with the key
  held separately. Credentials never appear in the API, logs or exports.
- Rate CSV import with a dry-run preview, and CSV export in the same format.
- `/api/v1/rates` endpoints: current, history, refresh, manual, import, export,
  provider health.
- Rate chart with six ranges, an average overlay and CSV export; dashboard rate
  header showing status, changes over four windows, and 24-hour and 6-month
  ranges.

### Notes

- A failed refresh is reported as a failure. The app never substitutes a stale
  rate for a fresh one, and never invents a sample to fill a gap.

## [0.1.0] - 2026-08-01

App shell and foundations.

### Added

- Home Assistant app packaging: manifest, multi-architecture build definition,
  Dockerfile and s6 service, with Ingress and no external port.
- FastAPI backend on Python 3.13 with structured JSON logging that scrubs
  credential-shaped values from every record.
- SQLite storage in WAL mode with Alembic migrations, and Decimal-safe column
  types so money and rates never pass through binary floating point.
- Settings document with the defaults from the product specification, and an
  append-only audit trail covering every settings change.
- Health, readiness and liveness endpoints.
- React 18 + TypeScript + Vite frontend that works under a dynamic Ingress path,
  with light and dark themes derived from Home Assistant's own CSS variables.
- Content Security Policy, security headers, cross-origin guard and rate
  limiting on sensitive endpoints.
- Backend and frontend test suites, including exact-decimal round-trip tests and
  Ingress base-path tests.
