# FX Strategy Manager

## What this app is

A decision-support tool for watching a large balance you are converting from one
currency into another. It watches the rate, values what you still hold, keeps a
record of what you converted and what it gained, and tells you when the market
has moved enough to be worth hearing about.

It does not tell you what to convert next. Your provider executes; this records
and monitors.

**It does not provide financial advice, and it does not automatically transfer
or convert money.** Nothing in this app can move funds.

## Installation

1. **Settings → Apps → App Store → ⋮ → Repositories**.
2. Add `https://github.com/phillclaxton/usdnzd-monitor`.
3. Install **FX Strategy Manager**, then **Start**.
4. Open it from the Home Assistant sidebar.

The app runs behind Ingress. It uses your Home Assistant login, opens no port,
and needs no port forwarding or reverse proxy.

## Configuration

| Option | Default | Meaning |
| --- | --- | --- |
| `log_level` | `info` | `trace`, `debug`, `info`, `warning` or `error`. Credentials are scrubbed from log output at every level. |
| `simulation_mode` | `false` | Start with simulated rates and a permanent warning banner. |
| `mqtt_host` | *(unset)* | Optional. Leave empty to use the broker Home Assistant already provides. |
| `mqtt_port` | `1883` | Broker port. |
| `mqtt_username` | *(unset)* | Optional broker username. |
| `mqtt_password` | *(unset)* | Optional broker password. Never written to the log. |

Everything else — currency pair, your position, providers, alert thresholds,
notification services, retention — is configured inside the app, not in this
panel, so it can be validated and audited.

### MQTT

If an MQTT broker is configured in Home Assistant, the app publishes its sensors
through MQTT discovery, which gives properly-created entities with attributes
and availability. Without a broker the app still runs; it simply publishes fewer
entities. MQTT is never required.

## Storage

Everything persistent lives in `/data`, which Home Assistant includes in its own
backups:

| Path | Contents |
| --- | --- |
| `/data/fx_strategy.db` | The database: your position, rate history, conversions, alert state, audit trail. |
| `/data/secrets.json` | API credentials, mode `0600`, encrypted at rest, excluded from normal exports. |
| `/data/secret.key` | The key that encrypts `secrets.json`, stored separately. |

`/config` is the app's shared configuration directory, useful for dropping in
CSV files to import.

## First run

Four steps: where the rate comes from, what you hold and the baseline rate you
measure against, your mortgage offset shortfall and floating rate, and which
Home Assistant services to notify.

Everything except the balance can be left empty and filled in later, and the
dashboard shows the same form until something is saved.

## Support

Issues and questions: <https://github.com/phillclaxton/usdnzd-monitor/issues>

Guides:

- [Installation](../docs/installation.md)
- [First-run setup](../docs/setup.md)
- [Rate providers](../docs/rate-providers.md)
- [Wise API setup](../docs/wise.md)
- [MQTT setup](../docs/mqtt.md)
- [Backup and restore](../docs/backup-restore.md)
- [CSV formats](../docs/csv-formats.md)
- [Troubleshooting](../docs/troubleshooting.md)
- [Security model](../docs/security.md)
