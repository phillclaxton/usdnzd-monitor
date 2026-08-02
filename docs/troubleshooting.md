# Troubleshooting

Start at **Diagnostics**. It shows the version, database state, scheduler,
provider health, Home Assistant and MQTT connections, and the recent log with
credentials scrubbed.

## The app will not start

| In the log | Cause |
| --- | --- |
| `Database migration failed; refusing to start` | A migration did not apply. The app deliberately stops rather than running against a half-migrated database. Restore a backup. |
| `Backend directory is missing` | A broken image. Rebuild or reinstall. |
| Port binding errors | Another add-on is on 8099 with host networking. This app uses none. |

## The sidebar panel is blank

- Hard-refresh: the browser may hold an old bundle.
- Check the log for `starting` with an `ingress_entry`.
- If the page loads but assets 404, the `<base href>` is not being injected —
  file a bug with the log line and your Supervisor version.

## No rate

| Symptom | Check |
| --- | --- |
| "No rate has been collected yet" | Refresh, or enter one manually. |
| Refresh returns an error listing each provider | Read it — it names the failing call. `credential was rejected` means the key; `could not be reached` means the network. |
| Rate marked **Stale** | The last successful poll is older than the staleness threshold. Check provider health in Diagnostics. |
| Rate never updates | Diagnostics → Scheduler running, and check the next run time. |

The app never substitutes a stale rate for a fresh one. If it says stale, it is.

## Notifications are not arriving

Work down this list — it matches the order the delivery rules apply:

1. **Notifications enabled** in Settings?
2. **Services configured?** Settings lists what your installation offers. A
   service that does not exist fails with a message saying so.
3. **Quiet hours?** Non-critical alerts are held. The notification history shows
   `quiet_hours` as the reason.
4. **Cooldown?** A recent identical alert suppresses the next. The history shows
   `cooldown`.
5. **Confirmed?** A target needs two consecutive qualifying samples at least 30
   seconds apart, a non-stale rate, and providers agreeing.
6. **Already notified?** A target only alerts again after the rate falls below
   `target − hysteresis`, returns, and the cooldown expires.
7. **Home Assistant reachable?** If not, messages queue and retry.

**Settings → Recent notifications** shows every attempt with its outcome. A
delivery failure is always visible there — it is never silent.

## A target was reached but nothing was converted

Working as intended. This app never converts. A reached target means the rate
touched your level; you (or your Wise Auto Conversion) perform the conversion,
and you record it here.

## The figures look wrong

| Symptom | Likely cause |
| --- | --- |
| Net proceeds show "Not calculable" | No fee model. Deliberate — the app will not show a zero fee as a fact. |
| Exposure lower than expected | It uses **available** funds, not the total. |
| Blended rate unchanged after a conversion | The conversion was marked simulated, which is excluded from the real position. |
| Remaining balance not falling | Conversions must be recorded. Reaching a target does not move it. |
| Percentages do not total 100 | Intentional if you are holding a reserve; the validation reports it as a warning, not an error. |

## MQTT entities missing or stuck

See [the MQTT guide](mqtt.md). In short: check Diagnostics for the connection,
and remember that an entity showing `unknown` usually means the figure is
genuinely not calculable rather than broken.

## Wise problems

See [the Wise guide](wise.md). The connection test reports which call failed,
which is usually enough to tell a wrong token from a missing profile ID.

## Getting help

Download the diagnostics bundle (**Diagnostics → Download diagnostics bundle**).
It excludes credentials and masks account identifiers — but read it before
posting it anywhere. Open an issue at
<https://github.com/phillclaxton/usdnzd-monitor/issues>.
