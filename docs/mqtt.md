# MQTT setup

MQTT is **optional**. Without a broker the app runs normally; it simply
publishes fewer entities, and those it does publish do not survive a Home
Assistant restart.

## Why it is preferred

MQTT discovery gives Home Assistant real entities with attributes, availability
and a device entry. They survive restarts of either side, and when the app stops
they go *unavailable* rather than showing a frozen last value as if it were
current.

The REST fallback writes states directly. Those states vanish on a Home
Assistant restart, which is why the app says so on the diagnostics page rather
than letting you discover it later.

## Setting it up

1. Install the **Mosquitto broker** add-on (or point at your own).
2. Set up the **MQTT integration** in Home Assistant.
3. Restart FX Strategy Manager. It picks up the broker the Supervisor provides —
   no configuration needed.

To use a different broker, set `mqtt_host`, `mqtt_port`, `mqtt_username` and
`mqtt_password` in the app's configuration panel.

## Topics

| Purpose | Topic |
| --- | --- |
| Availability | `fx_strategy/status` (`online` / `offline`, retained) |
| Discovery | `homeassistant/{component}/fx_strategy/{object_id}/config` |
| State | `fx_strategy/{object_id}/state` |
| Attributes | `fx_strategy/{object_id}/attributes` |
| Commands | `fx_strategy/{object_id}/set` |

The connection registers a last-will on the availability topic, so an abrupt
stop marks the entities unavailable.

## Entities

25 sensors, 7 binary sensors, 5 buttons and 2 optional writable numbers. See
[the full list](../fx_strategy/DOCS.md) or **Settings → Home Assistant →
Preview entities**, which shows exactly what would be published without needing
a broker.

Notable behaviours:

- A figure that cannot be calculated is published as an **empty state**, which
  Home Assistant shows as `unknown`. It is never published as `0`. With no fee
  model, the fee sensors are blank.
- `binary_sensor.fx_strategy_target_reached` carries an attribute stating that a
  reached target has converted nothing.
- `binary_sensor.fx_strategy_attention_required` lists its reasons in an
  attribute, so an automation can act on the specific cause.
- No writable entity exposes a target rate. Changing a target goes through the
  validating, audited API.

## Commands

Button presses and writable numbers arrive over MQTT and are validated exactly
as the equivalent API call, and audited the same way. An invalid value is
rejected and logged; it never reaches the database.

## Removing the entities

Switch off **publish entities** in Settings, or use the Diagnostics controls.
The app clears the retained discovery messages, which is what makes Home
Assistant drop the entities rather than leaving them as unavailable orphans.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No entities appear | Diagnostics → MQTT connected. If not, check the broker credentials. |
| Entities are unavailable | The app is stopped, or the broker connection dropped — the last-will fired. |
| A sensor shows `unknown` | The figure is not calculable. Usually a missing fee model or no rate yet. |
| Entities remain after uninstalling | The retained discovery messages were not cleared. Remove them from the broker, or use an MQTT client to publish an empty retained payload to the config topics. |
