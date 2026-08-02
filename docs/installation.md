# Installation

## Requirements

- Home Assistant OS or Supervised (the app runs under the Supervisor).
- Supervisor 2024.4 or newer.
- `amd64`, `aarch64`, or `armv7`.
- An MQTT broker is optional. Without one the app runs normally and publishes
  fewer entities.

## Steps

1. **Settings → Apps → App Store**.
2. Three-dot menu → **Repositories**.
3. Add `https://github.com/phillclaxton/usdnzd-monitor` and close.
4. Find **FX Strategy Manager** in the list and click **Install**. The first
   install builds the image locally, which takes a few minutes on a Raspberry Pi.
5. **Start**, then open it from the sidebar.

## Configuration

Everything except logging, simulation and the MQTT override is configured inside
the app, where it can be validated and audited.

| Option | Default | Meaning |
| --- | --- | --- |
| `log_level` | `info` | `trace`, `debug`, `info`, `warning`, `error`. Credentials are scrubbed at every level. |
| `simulation_mode` | `false` | Start in simulation with a permanent warning banner. |
| `mqtt_host` | *(unset)* | Leave empty to use the broker Home Assistant already provides. |
| `mqtt_port` | `1883` | Broker port. |
| `mqtt_username` / `mqtt_password` | *(unset)* | Optional; never written to the log. |

## Verifying the install

1. The app appears in the sidebar and opens without a second login.
2. **Diagnostics** shows the version, architecture and a running scheduler.
3. Enter a rate manually on the dashboard; it appears immediately.
4. With MQTT configured, `sensor.fx_strategy_usd_nzd_rate` appears in
   **Developer tools → States**.

## Upgrading

Use **Update** in the app panel. Migrations run at start-up; if one fails the
app refuses to start rather than running against a half-migrated database. The
log says which migration failed.

Take a backup first — Home Assistant's own backup includes this app's `/data`.

## Uninstalling

Uninstalling removes `/data`, including your strategies, conversion history and
credentials. Export a backup first if you want to keep the record.

With MQTT configured, clear the published entities before uninstalling
(**Diagnostics → Publish entities** has the controls), or Home Assistant keeps
them as unavailable entities from the retained discovery messages.
