# Architecture

## Layers

```
model/  ←  tracker.py  ←  sensor.py, binary_sensor.py, __init__.py (setup, services), config_flow.py
```

- **`model/`** (pure Python, rule 1): `tank.py` (`TankConfig`, `Reading`, `TankState`, `Tank`: the energy balance and
  the end of heating, the heat demand, see [model.md](model.md); `state_to_dict` / `state_from_dict` for persistence), `meter.py`
  (`meter_delta`, rule 12).
- **`tracker.py`** (`WaterHeaterTracker`, one per config entry, rule 2): reads the configured entities into a
  `Reading` (`unavailable` / `unknown` → None, units converted: W / kW, Wh / kWh, L / m³; an unknown unit → None),
  rejects non-finite numbers (`nan`, `inf`), calls `Tank.update` on every state change of those entities (time =
  the event's `time_fired`), every 5 minutes
  (the losses accrue, `hours_since_full` moves) and at `Tank.end_of_heating_due_at` / `Tank.heat_demand_due_at` (no
  reading comes while the power stays at 0, rule 11); then saves the state (`Store`, delayed 30 s) and notifies the entities. The services first
  account for the interval up to now, then reset or set the deficit. **Sources**: each update also follows the
  availability of every configured source (`_unavailable_cause`: missing entity, `unavailable` / `unknown`, not a
  number, unsupported unit); one unavailable for `UNAVAILABLE_AFTER` (15 min, timer at the due time since no event
  may come) is logged once at info with the cause and the consequence, and once when back. The relay or the power
  that long sets `estimate_available` to False. A source that no longer exists (no state at all) that long also
  raises a repair issue `missing_<key>_<entry_id>` (not fixable: the text says to reconfigure; error for the relay
  and the power, warning for a meter), deleted when the entity is back and at every unload (raised again after
  15 min if still true). At start, a stored state is resumed
  (`Tank.resume`, see [model.md](model.md#restart)).
- **Platforms**: `sensor.py` and `binary_sensor.py` expose the tracker's values; no computation of their own.
- **`diagnostics.py`**: the entry's diagnostics — the options, the estimate (level, energy to full, capacity, last
  full, heat demand and its thresholds), the stored model state (`state_to_dict`) and `WaterHeaterTracker.source_states()` (each configured source
  entity's raw state, unit and last change, None when the entity is missing). Nothing to redact.
- **`__init__.py`**: `async_setup` registers the services (`mark_full`, `set_level` with a finite level ≥ 0, keyed by
  `config_entry_id` of a loaded entry, else `ServiceValidationError`);
  `async_setup_entry` starts the tracker (`entry.runtime_data`) then the platforms; unloading stops it and saves;
  `async_remove_entry` (the water heater deleted) removes its stored estimate (`store_key`).
- **`config_flow.py`**: the user step creates the entry with every parameter in `options` (the title = the name;
  unique id = the relay entity: one water heater per relay). The reconfigure step edits the source entities
  (`SOURCE_KEYS`; the unique id follows the relay, refused when another entry has it), the options flow the tank's
  parameters; each keeps the other's keys, both reload the entry. The user step and the options refuse a set point
  not above the cold water (`set_point_not_above_cold_water`).

## Entities (a contract, rule 20)

One device per config entry; `unique_id` = `<entry_id>_<key>`.

| Key | Device class | Unit | Category | Value |
|-----|--------------|------|----------|-------|
| `level_l` | `volume_storage` | L | — | hot water left, in liters at the set point |
| `level_pct` | — | % | — | the same, in percent of the tank |
| `energy_to_full` | `energy_storage` | kWh | — | the deficit: energy to heat until full |
| `hours_since_full` | `duration` | h | diagnostic | since the last full tank |
| `last_full` | `timestamp` | — | diagnostic | time of the last full tank |
| `learned_losses` | `power` | W | diagnostic | the losses learned on the cycles without draw (`Tank.learned_losses_w`, see [model.md](model.md#learned-standing-losses)); attributes `configured_losses_w`, `losses_in_use_w`, `learning_enabled`, `loss_cycles`, `last_loss_cycle_w` (`WaterHeaterTracker.losses_attributes`) |
| `heat_demand` (binary sensor) | — | — | — | the thermostat's heat demand (see [model.md](model.md#heat-demand-the-thermostats-hysteresis)) |

`level_pct`'s attributes, for a planner reading one entity (`WaterHeaterTracker.planner_attributes`): `volume_l`,
`capacity_kwh`, `losses_w` (the losses the estimate counts: the learned ones once `learn_losses` is on, else the option), `set_point_c`, `cold_water_c` (the tank's options), `energy_to_full_kwh`,
`hours_since_full`, `heat_demand`, `heat_demand_measured`, `energy_before_demand_kwh`, `liters_before_demand_l` (the
state it plans from), `loss_threshold_kwh`, `draw_threshold_l` (the demand's thresholds after the next full tank) and
`relay_entity`, `power_entity`, `energy_entity`, `hot_water_entity` (None when not configured). The ones moving at every
update are not recorded (`_unrecorded_attributes`, read per class by Home Assistant): `energy_to_full_kwh`,
`hours_since_full`, `energy_before_demand_kwh`, `liters_before_demand_l`. None while the entity is unavailable (Home
Assistant writes no attributes then).

`heat_demand`'s attributes: `certainty` (`measured` / `estimated`), `loss_threshold_kwh`, `draw_threshold_l` (the
learned thresholds), `energy_before_demand_kwh`, `liters_before_demand_l` (left before each threshold; not recorded,
`_unrecorded_attributes`: they move at every update).

Icons in `icons.json` (entities and services). `PARALLEL_UPDATES = 0`: nothing is polled, the tracker pushes.

Every value is unknown until the first full tank or a `set_level`. `level_l`, `level_pct`, `energy_to_full`
(`needs_sources`) and `heat_demand` are unavailable while `estimate_available` is False; the two time entities stay
available.

## Persistence

`Store` `smart_hass_water_heater.<entry_id>`, version `STATE_VERSION` (1): `state_to_dict(TankState)` — deficit, last
full, time of the last update, last reading, last valid value of each cumulative meter, the heating session (heated
during this relay-on session, idle since, quiet since), the heat demand (measured or not, liters drawn since the full
tank, learned thresholds; added in version 1, read with their default when absent), the learned losses (heated since the last end of heating, the last cycles, the last cycle measured; read with their default when absent: nothing learned) — and `sources`, the source
entities it was measured with. At start, a meter whose entity changed (reconfigured) is forgotten
(`Tank.forget_meters`); a state stored without `sources` keeps its meters. After a restart the tracker resumes the
balance from the stored time of the last update: the losses of the downtime are counted, the meters resume from their
stored value. Changing the options (volume, temperatures) keeps the deficit in kWh: the level in liters follows the
new capacity.
