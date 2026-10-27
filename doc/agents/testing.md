# Tests

`make test` runs everything (`pytest`, `asyncio_mode = auto`) and fails under 95 % coverage of the integration; `make test-model` only the model.

## Families (rule 14)

- **`tests/model/`**: the pure model, no Home Assistant, no file. Times are fixed `datetime`s with a zone; the
  values come from the measures of a real tank when there are some (cite them, rule 9).
- **`tests/integration/`**: the integration inside a test Home Assistant (`pytest-homeassistant-custom-component`,
  the `hass` fixture; `tests/conftest.py` enables the custom integrations). Entities are set with
  `hass.states.async_set`, time is moved with `async_fire_time_changed`, entries are `MockConfigEntry`s.

## Files

| File | Covers |
|------|--------|
| `tests/model/test_meter.py` | `meter_delta`: counted, daily reset, jitter, first and unavailable readings, increase beyond the physics |
| `tests/model/test_tank.py` | Unknown until full, losses over a night (measured 2026-10-20), a draw, heating by energy meter = by power, an unavailable energy meter counted once, a transient meter drop and the daily reset, the deficit within the tank, end of heating (after 60 s idle, not before, not with the relay off or unknown, not without heating in the session), unavailable power, a late reading, no capacity, a non-finite level, a restart during a heating, state round trip and unreadable stored states, a replaced meter counted from its first reading and a kept one not forgotten |
| `tests/model/test_losses.py` | Learned standing losses: a night without draw (93.8 W, 2026-10-20), measured but counted only once enabled, no cycle with a draw, shorter than 12 h, from a full tank told or a level set, or without a hot water meter, a fault rejected but shown, the last five cycles weighted by duration, kept across a restart with an energy meter and dropped without one, by power, unreadable stored cycles |
| `tests/model/test_heat_demand.py` | Heat demand: off after a full tank until the losses reach the hysteresis, on at the first liter drawn, held when the relay interrupts a heating, the relay on without heating raises the losses threshold (after 60 s, then no check due), a heating before the threshold lowers it, the draws threshold learned from a heating after draws, the learning stopped at a quarter of the tank (a fault), estimated again by `set_level`, unknown with the level, kept with the thresholds over a restart, estimated for a state stored before it |
| `tests/integration/test_water_heater.py` | Config flow (entry created, one per relay, set point not above the cold water refused then accepted once corrected), options flow (the tank only: reload, full tank kept full, the same error then corrected, the sources kept), reconfigure (a replaced hot water meter counted from its first reading, a new relay and no meter, the relay of another entry refused), a state stored without the sources keeps its meters, the time entities in the diagnostic category, the diagnostics (estimate, heat demand, stored state, sources, a missing one), the heat demand entity (unknown, off measured at the full tank, on estimated from a set level, off measured after 60 s of relay on without heating with its threshold raised; unavailable with the level), the level's attributes for a planner (the tank, the estimate, the demand, the sources; on the percent entity only), level unknown then full at the end of heating without a new reading, a draw lowers the level, units (m³, kW) converted and unavailable / `nan` states ignored, the periodic refresh, `set_level` survives a reload and refuses `nan`, services refuse an unknown entry, unavailable sources (the level unavailable after 15 min of unavailable power, logged once with the cause and when back, the losses of the outage counted; a short outage not logged; an unsupported unit on the hot water meter only logged; a removed relay raises a repair issue, cleared by reconfiguring; a missing meter back clears its issue; an unavailable entity raises none), deleting the water heater removes its stored estimate, the learned losses sensor and the option enabling them — timers read the clock, so these tests move it with `freezer` |
| `review/tests/` | The review tool (see [review.md](review.md)) |
