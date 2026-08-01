# Backup and restore

## Home Assistant backups

The app stores everything in `/data`, which Home Assistant's own backups
include. For most people that is the whole answer: take Home Assistant backups,
and this app's strategies, conversions, rate history and audit trail come with
them.

`/data` contains:

| Path | Contents |
| --- | --- |
| `fx_strategy.db` | Strategies, tranches, conversions, rates, audit trail |
| `secrets.json` | API credentials, encrypted, mode 0600 |
| `secret.key` | The encryption key |

A Home Assistant backup includes the credentials. The app's own export does not.

## The app's export

**Diagnostics → Download backup** produces a JSON document containing every
table and the settings, with `contains_secrets: false`. Use it to move between
installations, or to keep a copy outside Home Assistant.

Credentials are deliberately excluded. Re-enter your API tokens after restoring.

Decimals are exported as exact strings and restored as exact Decimals — a test
asserts a value like `123456.7891` survives the round trip unchanged.

## Restoring

**Diagnostics → Restore**. The app refuses to merge into an installation that
already has strategies, because that would silently duplicate a portfolio. Tick
replace to overwrite instead.

A restore is audited, recording the row counts and the backup's timestamp.

Then:

1. Re-enter your Wise or provider API tokens.
2. Check the Diagnostics page: rate provider healthy, scheduler running.
3. Check the dashboard figures against what you expect.

## Before an upgrade

Take a backup. Migrations run at start-up and the app refuses to start on a
failed migration rather than running against a half-migrated database — but a
backup means you always have a way back.

## Database recovery

**Diagnostics → Run integrity check** runs SQLite's own check. It is read-only
and never repairs anything: automatic repair of a financial database is not
something this app will do behind your back.

If problems are reported:

1. Stop the app.
2. Copy `/data/fx_strategy.db*` (including the `-wal` and `-shm` files) off the
   host.
3. Restore from a backup into a fresh installation.
4. Reconcile against Wise to recover any conversions recorded since the backup.

The write-ahead log is checkpointed every six hours, on shutdown, and before
every backup, so a filesystem snapshot taken at any moment captures a complete
database.

## Retention

| Data | Default |
| --- | --- |
| Fine-grained rate samples | 12 months |
| Hourly aggregates | 5 years |
| Daily aggregates | Indefinite |
| Strategies, conversions, audit | Indefinite |
| Logs | 30 days |
| Raw provider payloads | Not stored |

Aggregates are always built **before** raw samples are purged, so trimming
history can never destroy the only copy of a period. Strategies, conversions and
audit events are never purged automatically.
