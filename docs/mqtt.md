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

18 sensors, 6 binary sensors, 4 buttons and 1 optional writable number. See
**Settings → Home Assistant → Preview entities**, which shows exactly what
would be published without needing a broker.

Notable behaviours:

- A figure that cannot be calculated is published as an **empty state**, which
  Home Assistant shows as `unknown`. It is never published as `0`. Before a
  position is saved, or before a trusted rate has arrived, the figures derived
  from them are blank rather than zero.
- `sensor.fx_strategy_realised_improvement_nzd` publishes the **confirmed**
  figure as its state. Anything reconstructed rather than read off a receipt is
  in `estimated_additional` and `total_including_estimates`, so an automation
  reading the state alone cannot treat an estimate as a fact.
- `binary_sensor.fx_strategy_position_saved` is off until something has been
  entered, which is also why the figures above it are unknown.
- `binary_sensor.fx_strategy_attention_required` lists its reasons in an
  attribute, so an automation can act on the specific cause. A rate that has
  merely *moved* is not one of them — that is what the alerts are for.
- No writable entity exposes the balance. It is the figure every other one is
  derived from, and changing it goes through the validating, audited API rather
  than a number box that keeps no record of who moved it.

### Entities removed in 2.0.0

Eighteen entities went with the conversion ladder. A removed entity goes
*unavailable* and its history is orphaned, so an automation or dashboard card
naming one of these needs editing: `sensor.fx_strategy_` `usd_initial`,
`usd_available`, `percent_converted`, `nzd_received_net`, `blended_rate_gross`,
`blended_rate_effective`, `next_target_rate`, `next_target_usd`,
`next_target_upside_nzd`, `one_cent_exposure_nzd`, `convert_all_now_nzd`,
`estimated_wise_fee_nzd`, `days_to_deadline` and `strategy_status`;
`binary_sensor.fx_strategy_target_reached` and `_deadline_warning`;
`button.fx_strategy_recalculate`; and `number.fx_strategy_available_usd`.

Discovery is republished on upgrade, which clears their retained configs so
Home Assistant drops them rather than leaving orphans.

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
| A sensor shows `unknown` | The figure is not calculable. Usually no position saved, no baseline rate, or no trusted rate yet. |
| Entities remain after uninstalling | The retained discovery messages were not cleared. Remove them from the broker, or use an MQTT client to publish an empty retained payload to the config topics. |
