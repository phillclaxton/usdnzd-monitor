# Changelog

All notable changes to this app are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-09-18

### Added

- **Settings → FX alerts.** Every alert threshold now has a screen. 2.0.0 shipped
  the settings section and the documentation telling you where to find it, but
  not the panel — the only way to change any of it was `PUT /api/v1/settings`.
  That was a documentation bug on my part, and this is the fix.

  The panel groups them the way they are decided: the rate moving (absolute and
  intraday), new highs and how long one stays quiet, levels and round numbers,
  what the position is worth, and your mortgage thresholds. The one most worth
  knowing about is at the top — **"say nothing again until the rate has moved"**,
  which is what stops a rate oscillating around a level talking to you all day.

- The panel states what the gates are, because they are not obvious from the
  fields: a condition says nothing the first time it is evaluated, then has to
  clear and re-arm, move the minimum change, and survive confirmation and the
  cooldown.

### Changed

- The **Notifications** card now says it is about delivery, and that FX alerts
  decides whether there is anything to deliver. Its quiet-hours note no longer
  offers "missed deadline" as an example of a critical alert; a failing rate
  provider is the only one left.
- Text fields in the alerts panel save when you leave them rather than on every
  keystroke. The settings document lives on the server, so saving per character
  would be one request per character.

### Removed

- Four notification settings that nothing has read since 2.0.0 took the
  conversion ladder: `near_threshold`, `repeat_interval_minutes`,
  `reversal_threshold` and `deadline_warning_days`. One of them —
  `near_threshold`, "alert when within this distance of a target" — was still on
  screen in Settings, offering a knob that did nothing.

  A settings row that still carries them is fine: unknown keys have always been
  ignored rather than rejected, deliberately, so that a downgrade cannot wedge
  the app.

## [2.0.0] - 2026-09-18

This app no longer plans your conversions. It records them, values what is
left, and tells you when something has actually changed.

The ladder made sense when this app was where the plan lived. It is not: Wise
executes the conversions, and every change to a Wise auto-conversion meant
editing a tranche here to match. So the app now answers the one question it is
good at — **"what is my FX position, and has anything important changed?"** —
and stops answering "what should I convert next?".

**Nothing you entered has been deleted.** Every strategy, tranche, deadline
requirement, obligation and conversion is still in the database and still in
every backup. The screens that read them are gone; the rows are not.

### Added

- **A position.** What you still hold, the baseline rate you measure against,
  your offset shortfall, your floating loan rate and your monthly spending.
  Everything else is computed from those five figures on every read, so no
  stored total can drift out of step with what it came from.
- **A dashboard built on it**: what the balance is worth at the current rate,
  unrealised improvement, realised improvement, total improvement, what has
  been converted and what it cost in fees.
- **The carrying cost.** What the unfunded part of your offset costs you for
  every day the money is still in USD — charged on the shortfall, never on the
  whole balance — with the monthly figure beside it.
- **Movement alerts**, replacing the target alerts: an absolute move, an
  intraday percentage move, a new 7/30/90-day high, a level crossed, a change
  in what the position is worth, and mortgage milestones. Each message says
  what happened, the rate, why it matters and what it is worth. **None says
  what to convert.**
  Volume is the thing an alert system gets wrong, so four rules stand between a
  condition and your phone: it primes on first sight and says nothing; it has
  to have cleared and re-armed; it has to have moved a further half cent since
  it last spoke; and it still has to survive confirmation and the cooldown.
- **A rate what-if** on the position page: what the balance would be worth at a
  rate you type, and how that compares with your baseline. It replaces the
  scenarios page.
- **Conversion editing**, through a form that says on screen that correcting a
  historical record does **not** change your current balance.
- **Import and export of the whole position** as one JSON document, treated as
  historical backfill: the balance is written exactly as given, because it is
  already the balance after those conversions. An example with invented figures
  is in `docs/examples/fx-state-example.json`.
- **An estimate is never shown as a fact.** A conversion whose amounts were
  reconstructed rather than read off a receipt is flagged, and every total it
  contributes to is split into confirmed / estimated / total wherever it
  appears — the dashboard, the history, the API and the export. Enter the real
  receipt and the flag clears on its own.

### Removed

- **The conversion ladder**: strategies, tranches, deadline requirements,
  allocation validation, the scenarios page, the strategy JSON editor, the
  walk-away and deadline analyses, and the target state machine.
- **Debts and conversion priorities**, retired in 1.4.x, with its data archived
  into the backup first.
- **API routes**: `/strategies/*`, `/tranches/*`, `/summary`,
  `/strategy-templates`, and the strategy document routes. `POST /conversions`
  no longer takes `strategy_id`, `tranche_id` or `allocations`, and returns one
  object rather than a list. `POST /wise/reconcile` no longer takes
  `strategy_id`.
- **Eighteen Home Assistant entities.** A removed entity goes *unavailable* and
  its history is orphaned, so if an automation or dashboard card names one of
  these, it needs editing:

  | Removed | |
  | --- | --- |
  | `sensor.fx_strategy_usd_initial` | `sensor.fx_strategy_usd_available` |
  | `sensor.fx_strategy_percent_converted` | `sensor.fx_strategy_nzd_received_net` |
  | `sensor.fx_strategy_blended_rate_gross` | `sensor.fx_strategy_blended_rate_effective` |
  | `sensor.fx_strategy_next_target_rate` | `sensor.fx_strategy_next_target_usd` |
  | `sensor.fx_strategy_next_target_upside_nzd` | `sensor.fx_strategy_one_cent_exposure_nzd` |
  | `sensor.fx_strategy_convert_all_now_nzd` | `sensor.fx_strategy_estimated_wise_fee_nzd` |
  | `sensor.fx_strategy_days_to_deadline` | `sensor.fx_strategy_strategy_status` |
  | `binary_sensor.fx_strategy_target_reached` | `binary_sensor.fx_strategy_deadline_warning` |
  | `button.fx_strategy_recalculate` | `number.fx_strategy_available_usd` |

  Eight arrive in their place: `sensor.fx_strategy_nzd_value`,
  `_realised_improvement_nzd`, `_unrealised_improvement_nzd`,
  `_total_improvement_nzd`, `_offset_shortfall_nzd`,
  `_daily_carrying_cost_nzd`, `_months_of_burn`, and
  `binary_sensor.fx_strategy_position_saved`. Twenty-one are unchanged,
  including every rate sensor and `sensor.fx_strategy_usd_remaining`, which now
  reads as what is still held.

  Thirty-nine entities before, twenty-nine after. Discovery is republished on
  upgrade, so Home Assistant drops the retained configs for the ones that have
  gone rather than leaving them as orphans.

### Changed

- **The setup wizard is four steps**, not eight: where the rate comes from,
  what you hold, your mortgage, and notifications. Everything except the
  balance can be left empty and filled in later.
- **Recording a conversion reduces the balance** — that is what
  `POST /fx/conversions` is for, and the only reason it is separate from
  `POST /conversions`, which records history and changes nothing. Neither
  touches your offset shortfall: not every conversion goes to the mortgage, and
  assuming one did would quietly corrupt the carrying cost.
- **Wise reconciliation takes its currency pair from your settings.** It used
  to need an active strategy and refused without one.
- The CSV importer accepts a `tranche_reference` column and ignores it, so a
  file exported by an earlier version still imports.
- A figure that cannot be calculated still says what is missing — "set a
  baseline rate" — rather than showing `0.00`, on every screen and as an empty
  Home Assistant state. That rule now covers the position figures too.

### Upgrading

The database migrates in place and nothing is dropped. Take a backup first
anyway, then: open the app, fill in the position form on the dashboard, and
record what has already been converted (or import a state document). Your
conversion history is already there; it just needs a baseline rate before the
improvement figures can mean anything.

## [1.4.2] - 2026-09-17

### Fixed

- **Every high was a low and every low was a high.** `extremes()` returns the
  pair as `(low, high)`, and two of its three callers unpacked it the other way
  round — so the dashboard's 24-hour and 6-month high and low, and the
  `fx_strategy_six_month_high` / `_low` sensors, have been showing each as the
  other. The reversal alert, which unpacked it correctly, was unaffected.
- The pair is now a `RateExtremes` with named `low` and `high` fields rather
  than a bare tuple. Two values of the same type read the same whichever way
  round they are unpacked, which is how this survived unnoticed; with named
  fields the type checker found every call site, and the mistake cannot be
  repeated silently.

## [1.4.1] - 2026-09-17

### Fixed

- **The rate data panel added in 1.4.0 pegged a CPU core.** It compared every
  observation against every other one to find the outliers, which is quadratic:
  at five-minute polling, opening the chart cost **23 seconds** of CPU for a
  30-day range and **110 seconds** for a year — measured on a machine far
  faster than a Home Assistant box, and repeated whenever the panel refetched.

  The neighbours were always a contiguous slice of a time-ordered list, so the
  scan was never needed. The same figures now take 0.14s and 0.33s. A test
  reviews 12,000 samples and fails if the cost goes back to scanning the whole
  series.
- The same endpoint built a result object for every sample in the range — tens
  of thousands for a year — and then returned one page of them. It now builds
  only the rows it returns.

## [1.4.0] - 2026-09-17

### Added

- **A rate that jumps implausibly far is refused on arrival.** A quote more than
  2% from the last good rate (configurable) does not become the current rate, is
  not charted, and no figure is calculated from it. A provider glitch and a real
  market move look identical in one sample, so the app does not guess: it
  refuses, and if the next few polls agree on the new level, the level has moved
  and is accepted. The default is three quotes, so a genuine jump is delayed by
  a few minutes and a one-off spike never lands.

  A refusal is **not** a provider failure — the call worked, it is the number
  that is in doubt — so the provider stays healthy and the chain carries on to
  the next one. The refused observation is stored with its reason, reported in
  the refresh result, and written to the audit trail. Nothing is hidden.

  With no recent rate to compare against, a quote is accepted: refusing on no
  evidence would leave a fresh install unable to collect anything at all.
- **Chart → Rate data points**, for anything that got through before. The panel
  measures each observation against the median of the dozen either side and
  flags those standing further out than the threshold. **Exclude** removes a
  point from the chart, the high and low, the averages and the rollups behind
  the longer ranges; **Restore** puts it back. Both are audited.

  The observation is kept rather than deleted. What a provider actually returned
  is the evidence for why a wrong figure appeared, and keeping it is what makes
  the action reversible.
- Excluding a point rebuilds the hourly and daily aggregates it fell in, and
  removes a bucket left with nothing in it. Without that the spike would vanish
  from the 7-day chart and reappear on the 3-month one, which is drawn from the
  rollups rather than the raw samples.
- New endpoints: `GET /rates/samples`, `POST /rates/samples/exclude` and
  `POST /rates/samples/restore`. `/rates/refresh` now carries `refused`.

### Changed

- Every path that reads a rate — the current rate, the chart, the high and low,
  the change indicators, the CSV export, the aggregates — now goes through one
  shared rule for whether an observation may be used, so a new read path cannot
  quietly forget to skip an excluded one.

## [1.3.3] - 2026-08-06

### Fixed

- **The manual fallback still alerted on 1.3.2 if a manual rate had ever been
  entered.** 1.3.2 excused a provider from the reconciliation when it was "in
  the chain and configured", on the reasoning that it had earned whatever state
  it was in. The manual fallback is both of those things and is *still* never
  contacted while the primary answers — so on an installation with a manual
  rate stored, nothing changed and the alert kept arriving every six hours.

  The test is now what the refresh **actually asked**, not what is configured
  and not what is in the chain. Only a provider that was asked can be reported
  as failing; every other stored status is corrected on each poll. A provider
  skipped *because* it is backing off still counts as in use, so a real outage
  is unaffected.
- A provider consulted only for the disagreement comparison now counts as in
  use, and one that turns out to have nothing to work with is recorded as
  unconfigured there too, matching the main chain.

### Note

The message in one of these alerts is the error recorded **at the time of the
failure**, not a description of the provider now — "No manual rate has been
entered yet" can appear on an installation that has one. That is what made
1.3.2 look like a complete fix when it was not.

## [1.3.2] - 2026-08-06

### Fixed

- **A provider nobody selected kept raising critical alerts.** "Rate provider
  manual has been failing for 5119 minutes — no manual rate has been entered
  yet" on an installation where the manual fallback was neither chosen nor
  needed. 1.2.3 stopped that state counting towards the Home Assistant
  problem sensor and the diagnostics bundle, but never touched the
  notification, which read the same stale row and woke people up about it.
- The state itself could not clear. 1.2.3 corrected it when the provider was
  polled — but the chain stops at the first success, so on a healthy
  installation the fallback is never polled, and a failure recorded once stood
  for ever. Every refresh now corrects the recorded state of **every provider
  it is not polling**, whether or not that provider was reached. An
  installation carrying the bad state fixes itself on the first poll after
  upgrading; nothing needs resetting by hand.
- A provider you stop using — dropped as primary, secondary or fallback — no
  longer stays reported as failing on whatever error it last saw. It is
  reported as not in use, which is what it is.
- A provider that is genuinely configured and genuinely failing still alerts,
  unchanged. The suppression is about not being set up, not about being quiet.
- An adapter that raised an unexpected error while the app described the
  provider list could stop a refresh outright. Describing a provider now
  reports the problem instead of propagating it, since it runs on the polling
  path.

## [1.3.1] - 2026-08-05

### Fixed

- **The field editor and the JSON view showed different strategies.** The fields
  were copied from the server once when the page loaded and never refreshed, so
  after saving from the JSON view the form still showed the old plan — and
  saving from it would have put the old plan back. Both views are now two
  renderings of one value: switching converts what is on screen instead of
  reloading, so an unsaved change made in either view is there in the other, and
  a save from one updates the other from what the server actually stored.
- **Saving from the field editor silently deleted parts of the strategy.** The
  form's payload left out dated requirements, per-tranche minimum rates and
  deadlines, the rate provider and the start date. Both the field editor and the
  JSON document replace the whole definition on save, so every one of those was
  cleared the next time the form was used — and pasting a document that set them
  made this far easier to hit. The draft now carries every field the server
  accepts, and a test asserts the round trip is lossless.
- If the JSON cannot be read as an object, switching to the fields is refused
  with the reason rather than showing values that no longer match the document
  on screen.
- If the strategy is changed elsewhere while you have unsaved edits, the page
  says so and leaves the edits alone until you choose to reload, instead of
  either discarding your work or hiding the change.

## [1.3.0] - 2026-08-05

### Added

- **A strategy can be edited as a JSON document.** Strategy → **Edit as JSON**
  shows the whole plan as one document to copy, paste and save, instead of
  working through the fields one at a time. The document is exactly the shape
  the create and update endpoints already accept, so what is copied out of one
  installation is a valid request body for another — there is no separate
  export format that could drift from the real one.
- The document is checked against the server as it is edited. Every problem
  says where it is: a syntax error gives a line and column, a validation failure
  gives the field path (`tranches[2].target_rate`). "Invalid JSON" on its own is
  useless when the document is a hundred lines long.
- Before saving, the panel lists what would change field by field, and warns
  about the consequences that are easy to miss in a paste: a tranche being
  removed that has recorded conversions against it, a moved target rate
  resetting its reached-and-notified state, and how many recorded conversions
  the edit leaves untouched.
- New endpoints: `GET`/`PUT /strategies/{id}/document`,
  `POST /strategies/{id}/document/preview`, `POST /strategies/document` and
  `POST /strategies/document/preview`. A preview writes nothing; a rejected save
  returns the same located problems in `error.details`.
- "Editing a strategy as JSON" (removed in 2.0.0) documented the format,
  what it deliberately omits, and how tranche identity is preserved.

### Notes

- Saving a document goes through the same update path as the field editor, so
  tranche identity is kept by sequence number and recorded conversions, alert
  state and the audit trail are unaffected. The document carries the **plan**;
  it never carries or alters the record of what happened.
- Money and rates in the document are JSON strings, never numbers. A JSON number
  is a binary float and `1.72` does not survive the trip intact.

## [1.2.3] - 2026-08-04

### Fixed

- **The manual fallback was reported as failing when it simply had no rate
  entered.** It is in the provider chain by default but only reached when
  everything above it fails, so once a single attempt was recorded against it
  the state stuck permanently: the chain stops at the first success, so a
  provider it never reaches can never record a success to clear it. Having
  nothing entered is now a configuration state, not an outage.
- That stale failure also turned on the Home Assistant provider-problem sensor
  and listed the provider under `failing_providers` in the diagnostics bundle,
  so a perfectly healthy installation reported a fault. Both now ignore
  providers that are not configured. A configured provider that fails is still
  reported — the distinction is whether it is set up, not which provider it is.
- The provider status table described a registry built without the stored
  manual rate, so the manual provider would have shown as not configured even
  after a rate was entered. The read-only call sites now use the same primed
  registry the scheduler does.

## [1.2.2] - 2026-08-04

### Fixed

- **Viewing and editing an obligation were not discoverable.** The detail
  appeared as a card further down the page, reached by clicking a row with
  nothing to indicate it could be clicked, and pressing Edit then opened the
  form higher up — off-screen on a phone. Both now open in a dialog over the
  page: the name is a link, every row has a visible **Edit** button, and
  editing from the detail replaces the contents of the same dialog rather than
  moving somewhere else.
- The dialog closes on Escape, on the ✕, or by clicking outside; focus moves
  into it on open and returns to the control that opened it on close; the page
  behind does not scroll. On a phone it fills the screen rather than sitting in
  a box with margins.
- A rate-service test seeded samples at offsets from "now", which put them
  either side of UTC midnight and changed how many day buckets existed. It
  failed only between roughly midnight and 03:00 UTC. Now seeded at fixed
  timestamps.

## [1.2.1] - 2026-08-03

### Fixed

- **Obligations could not be edited.** The API supported it, but nothing in the
  interface reached the endpoint — `api.patch` did not exist on the client, so
  there was no way to correct a mistake short of deleting and re-adding. The
  add form now does double duty as an edit form, opened with **Edit** on the
  detail card.
- **An optional field could not be cleared.** A due date, target rate or
  maximum waiting period added by accident had no way out. Each now has a Clear
  button, and an emptied field is sent as an explicit null so the server removes
  it. Some browsers hide the native clear control on a date input, hence an
  explicit one.

Clearing a due date changes the recommendation, since the deadline may have been
the only thing forcing a conversion. Both values are kept in the audit trail.

## [1.2.0] - 2026-08-03

Debts and conversion priorities.

### Added

- **Obligations**: record debts, loans, offset requirements and other NZD
  commitments that may be funded by converting USD. Each carries its own
  interest basis, due date, priority, relationship importance, target rate and
  maximum acceptable wait.
- **Break-even in both directions**: how many days a given rate improvement
  pays for, and what rate would repay a given wait. Net benefit is quoted at 7,
  14, 30, 60 and 90 days.
- **Two priority scores**, because they genuinely differ. Financial priority
  counts the due date, interest cost, size and deadline; overall priority adds
  the priority you set and the non-financial importance. An interest-free family
  loan is financially unhurried and may still be the first thing to fund. Every
  component of the score is shown.
- **Portfolio view**: totals, cost of waiting, amounts due within 7 and 30 days,
  the next obligation to fund, a weighted break-even rate across the book, and
  the maximum rational waiting period.
- **Conversion allocation**: three standard plans plus a custom scenario at any
  amount or hypothetical rate. An obligation that refuses partial payment is
  skipped rather than part-funded.
- **Home Assistant entities**: ten portfolio sensors, and a device per
  obligation carrying eight sensors each. An obligation called "Meika repayment"
  becomes `sensor.meika_repayment_remaining` and so on.
- **Seven notification triggers**, each stating the amounts in full.
- A **Debts** page in the app: summary, priority table, per-obligation detail
  with the full working, allocation planner and three charts.
- `docs/obligations.md`, and `docs/examples/lovelace-obligations.yaml` built
  entirely from native Home Assistant cards.

### Notes

- Nothing here pays, converts or transfers money. `POST /obligations/pay`
  returns an explicit refusal rather than a 404.
- A zero-interest obligation has no financial break-even period. It is reported
  as unknown, not as zero or infinity — the concept does not apply.
- A stale rate never supports a recommendation to wait, and the entities publish
  an uncalculable figure as unknown rather than as zero.
- An annual rate above 1 is refused through the API as a likely percentage:
  6.04 instead of 0.0604 would inflate every figure a hundredfold.

### Fixed

- The structural test asserting no conversion endpoint exists was vacuous:
  `app.routes` returns included routers as opaque objects, so it inspected seven
  paths rather than 110 and would have passed whatever the API exposed.

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
