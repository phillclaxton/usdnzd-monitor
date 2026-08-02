# Changelog

All notable changes to this app are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-08-02

The generic API provider can now be configured from the interface.

### Added

- **Settings → Generic API provider**: a preset picker covering the five known
  vendors, every request and response-mapping field, the API key, and a **Test**
  button that makes one live call and reports either the rate it received or the
  precise reason it failed.
- `GET /providers/presets`, `GET`/`PUT /providers/generic`,
  `POST /providers/generic/preset/{key}`, `POST /providers/generic/test` and
  `DELETE /providers/generic/credentials`.
- Presets are applied server-side, so their defaults have one definition — the
  same one the provider tests run against.

### Fixed

- The generic provider was selectable as primary or secondary but there was no
  way to configure it: the provider, its presets and its reserved credential
  slot all existed, with nothing connecting them to the interface. Choosing it
  produced a provider that could not start.

### Notes

- The API key goes to the encrypted secret store. It is never returned by any
  endpoint, never written to the settings document, and the audit trail records
  only that it changed. Tests assert each of those.
- Enabling the provider does not select it; it still has to be chosen as the
  primary or secondary provider, and the panel says so.

## [1.0.1] - 2026-08-02

Fixes the app image failing to build on a Home Assistant server.

### Fixed

- `ARG BUILD_FROM` was declared after the first `FROM`, which scopes it to the
  frontend stage rather than the global scope. Only an `ARG` declared before
  any `FROM` can be used in a `FROM` instruction, so the base image name
  resolved to an empty string and the build stopped with
  `base name (${BUILD_FROM}) should not be blank`. The build arguments are now
  declared at the top of the Dockerfile and re-declared inside the stage that
  uses them.
- The Dockerfile's fallback base is now the multi-platform
  `ghcr.io/home-assistant/base-debian:trixie`, so a plain `docker build` works
  without `build.yaml`. The Supervisor still supplies the exact per-architecture
  image from `build.yaml`, which is what keeps armv7 — absent from that
  manifest — building.

### Added

- A CI job that builds the app image with exactly the arguments the Supervisor
  passes, and again with no `BUILD_FROM` so the Dockerfile's own default has to
  resolve on its own. Nothing built the image before, which is how this reached
  a release.
- `.dockerignore`, so a local build no longer copies a developer's virtualenv,
  `node_modules` or previous build output into the image.

### Notes

- The Supervisor logs a deprecation warning for `build.yaml`. It is still read,
  and it is what supplies the armv7 base image, so it stays for now. See
  `docs/upstream-notes.md`.

## [1.0.0] - 2026-08-01

First complete release. Everything the specification describes is implemented,
tested and documented.

### Added

- **Simulation mode.** Inject a rate, replay a sequence of rates through the
  whole pipeline — targets, confirmation, notifications, deadlines — and reset
  afterwards. Every simulated record is marked as such and excluded from the
  real position, the blended rate and every exposure figure. A permanent banner
  is shown while it is on, and `simulation/reset` deletes only simulated
  records.
- **Backup and restore.** A portable JSON document of every table, with
  `contains_secrets: false` and a note that credentials must be re-entered.
  Restore refuses to merge into a database that already holds strategies unless
  `replace` is set, so it cannot silently duplicate a portfolio.
- **Diagnostics bundle.** Version, architecture, database size and integrity,
  provider health, scheduler state, Home Assistant and MQTT status, and the last
  100 log lines — with credentials absent and account and transaction
  identifiers masked.
- **Clock-drift warning** when the provider's timestamp and the host clock
  differ by more than an hour, since sample ordering drives the confirmation
  rules.
- **End-to-end tests** running the real backend behind a proxy that mimics
  Ingress, covering the full specification narrative: create the ladder,
  activate, cross a target, record the conversion, check the blended rate,
  export, reload. A separate mobile project checks the phone layout, and one
  test asserts that no credential appears anywhere in the diagnostics bundle.
- **Documentation**: installation, first-run setup, rate providers, Wise,
  entities, backup and restore, CSV formats, troubleshooting, development,
  release process, and notes on where upstream APIs differ from the
  specification.
- **CI**: lint, format, type-check, tests at 85% overall and 95% on the
  financial calculation modules, frontend build and tests, end-to-end tests, and
  manifest validation.

### Fixed

- Target confirmation during replay used the wall clock rather than the sample's
  own timestamp, so the two-sample spacing rule was never satisfied and a replay
  produced no notifications. Sample time is now threaded through
  `evaluate_targets`.
- The dashboard scrolled sideways on a phone: the main region is a grid item, so
  its default `min-width: auto` let a wide table stretch the page instead of
  scrolling inside its own wrapper.
- Restoring a backup mis-typed columns whose SQLAlchemy type declines to report
  a `python_type`. Coercion now dispatches on the column type itself.

### Notes

- Coverage measurement needs `concurrency = ["thread", "greenlet"]`: SQLAlchemy
  runs handler code inside greenlets, and without it request handlers that touch
  the database are reported as unexecuted.
- Still no code path that converts or transfers money.

## [0.7.0] - 2026-08-01

Wise read-only integration.

### Added

- Wise connection test that reports precisely which call failed, profile
  discovery, balance reading and completed-conversion reading.
- Reconciliation between Wise's completed conversions and the records held
  here, defaulting to a dry run. Matching is on the Wise reference, so running
  it twice imports nothing twice.
- Quote endpoint for fee estimation, with the result labelled as an estimate
  and marked not executable by this application.
- Encrypted credential storage with a masked hint, a connection test, and
  audit events that record that a credential changed without recording its
  value.
- `app/services/execution.py`: the `ConversionExecutor` interface a future
  module would implement, the eleven conditions such a module would have to
  satisfy, and `DisabledExecutor`, which refuses both methods.
- `GET /api/v1/wise/execution-policy` states the position, and `POST
  /api/v1/wise/execute` returns an explicit refusal rather than a 404.

### Notes

- There is no code path in this application that converts or transfers money.
  A test asserts the route table contains no such endpoint.

## [0.6.0] - 2026-08-01

Home Assistant entities.

### Added

- MQTT discovery publishing every entity the specification lists: 25 sensors,
  7 binary sensors, 5 buttons and 2 optional writable numbers, with a device
  entry, availability topic and a last-will message so entities go unavailable
  rather than showing a frozen value when the app stops.
- Entity attributes on the rate and strategy sensors as specified, including
  next target, distance to target, 24-hour and six-month ranges, tranche counts
  and the walk-away rate.
- Commands from Home Assistant (button presses, writable numbers) validated
  exactly as the equivalent API call, and audited.
- REST fallback for installations without a broker, publishing a smaller set of
  states and saying plainly that they do not survive a restart.
- `POST /api/v1/home-assistant/publish` and `GET .../entities`, the latter
  showing exactly what would be published without needing a broker.
- Entity cleanup: clearing retained discovery configs removes the entities from
  Home Assistant rather than orphaning them.

### Notes

- A figure that cannot be calculated is published as an empty state, which Home
  Assistant shows as unknown. It is never published as zero.
- No writable entity exposes a target rate: changing one has to go through the
  validating, audited API.

## [0.5.0] - 2026-08-01

Conversion recording.

### Added

- Conversion CRUD with validation: positive amounts, and a refusal to record
  more than is still unconverted unless the user explicitly says they are
  correcting an earlier record.
- Duplicate detection on the provider transaction ID, so a reconciliation run
  or a re-imported CSV cannot double-count a conversion.
- Splitting one conversion across several tranches, with the rounding residue
  placed on the last part so the pieces sum exactly to the entered amount.
- Tranche status derived from what was actually converted: partially completed,
  then completed. Deleting a conversion reopens its tranche.
- Corrections and deletions keep every previous value in the audit trail.
- Conversion CSV import with a dry-run preview that reports rejected rows,
  duplicates and unresolved tranche references; export in the same format.
- Conversions page with a manual entry form, the implied effective rate shown
  live, CSV import preview, and a delete flow that asks for a reason.

### Notes

- Conversions marked simulated are excluded from the real position, the blended
  rate and every exposure figure.

## [0.4.0] - 2026-08-01

Notifications.

### Added

- Per-target alert state machine: below, near, reached_unconfirmed,
  reached_confirmed, notified, acknowledged, completed, reset.
- Target confirmation requires two consecutive qualifying samples at least 30
  seconds apart, a fresh (non-stale) rate, and providers agreeing within the
  configured threshold. All three are configurable.
- Reset hysteresis: a target can only alert again after the rate falls below
  `target - hysteresis`, returns, and the cooldown expires.
- Approaching-target, walk-away, deadline, rate-reversal and provider-outage
  alerts.
- Notification delivery through Home Assistant notify services discovered from
  the running installation. Cooldowns per rule and entity, quiet hours with a
  critical override, a bounded retry queue for when Home Assistant is down, and
  a log of every attempt including failures.
- `/api/v1/home-assistant` endpoints: status, service discovery, test
  notification and notification history.
- Notification settings panel with a test button and delivery history.

### Notes

- A target being reached is a statement about the rate, not about money. It
  never marks a tranche completed, and every message says the app has not
  converted anything.
- A stale rate cannot confirm a target, and does not advance the confirmation
  count.

## [0.3.0] - 2026-08-01

Strategies, tranches and the dashboard.

### Added

- Calculation engine as pure Decimal functions: gross, fee, net, effective and
  blended rates; remaining balance; one-cent exposure; sensitivity; target
  upside; walk-away analysis; deadline bands; scenario evaluation.
- Strategy and tranche models with percentage, fixed-amount and remainder
  allocation. Rounding residue is pushed onto the last percentage tranche so the
  parts always sum exactly to the whole.
- Recommended ladder template (15/20/25/20/20% at 1.7200-1.8000), equal-tranche
  and monitor-only templates.
- Strategy lifecycle: draft, activate, pause, resume, complete, duplicate. A
  strategy carrying recorded conversions is archived rather than deleted.
- Dashboard summary endpoint returning position, opportunity, tranche progress,
  exposure, walk-away analysis, dated requirements and comparisons in one call.
- Scenario comparison across convert-now, the target ladder, an equal schedule
  and a user-supplied rate, presented as trade-offs with no "best" label.
- Configurable rate zones with the specification's default bands.
- Dashboard, strategy editor and scenario pages.

### Notes

- Every displayed amount states whether it is gross, an estimate or an actual
  result. With no fee model configured the app shows "Fee not included" rather
  than a zero fee, and reports net as not calculable.
- Moving a tranche's target rate resets its reached state, so a raised target is
  never left flagged as met at the old level.

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
