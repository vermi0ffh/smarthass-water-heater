# The tank model (`custom_components/smart_hass_water_heater/model/`)

Pure Python (rule 1): it receives readings with their time and keeps the tank's state; it never reads the clock or
Home Assistant.

## Energy balance

The tank is described by its **deficit**: the energy (kWh) missing to a full tank, at the set point. Between two
readings, with the previous inputs held (zero-order hold):

- standing losses raise it: `losses_w × Δt`;
- each liter drawn raises it: `liters × 0.001162 kWh/(L·°C) × (set point − cold water)`, the water drawn being
  replaced by cold water (the hot water meter is at the tank's outlet, before any mixing valve);
- heating lowers it: the heater's energy meter increase when one is configured (`has_energy_meter`: the power is then
  never integrated — while the meter is unavailable the heating is paused, and its increase counts when it comes
  back), else `power × Δt` with the power held since the previous reading.

The deficit is kept within `[0, capacity]`, `capacity = volume × 0.001162 × (set point − cold water)`. The level is
expressed in liters at the set point: `level_l = volume × (1 − deficit / capacity)`, `level_pct = 100 × (1 − deficit /
capacity)`. This is the formula of the Home Assistant templates it replaces (`200 − used − losses + heating /
(0.001162 × (60 − cold water))`), written in energy so that the losses and the heating need no conversion.

A reading older than the last update accounts for no interval. The capacity must be positive (set point above the
cold water, checked by the config flow); otherwise every value is unknown.

The deficit is **unknown** until the first full tank (end of heating) or until the user sets it (`mark_full`,
`set_level`): the integration never guesses a level.

## End of heating (full tank)

The thermostat cuts the heater when the tank reaches its set point. A **real** end of heating is: the relay on, the
heater consuming (power above `heating_threshold_w`, 100 W) during this relay-on session, then the power below
`idle_power_w` (5 W) for `end_of_heating_s` (60 s), the relay still on. The deficit is then reset to 0 and the time
of the full tank recorded.

Not an end of heating: the relay switched off while heating (an interrupted heating, the tank is not full); the relay
switched on while the thermostat is satisfied (no consumption in this session: the tank may be anywhere above the
thermostat's restart point). Both were mistaken for full tanks by the former automation (2026-10-18).

## Heat demand (the thermostat's hysteresis)

After a full tank, the thermostat restarts only once the water at its probe has cooled by its hysteresis: until
then, the relay on heats nothing. Once restarted, it heats up to its set point, even if the relay interrupts it in
between. The model keeps this **heat demand** (`heat_demand`: would the heater heat with the relay on), held from its
start to the full tank:

- **measured** (`heat_demand_measured`) by a relay-on session: the heater consuming → on; the relay on for
  `end_of_heating_s` (60 s) without consuming in this session → off (`heat_demand_due_at` asks the tracker for that
  update, no reading coming while the power stays at 0); a full tank (end of heating, `mark_full`) → off;
- **estimated** otherwise, from two thresholds: the deficit since the full tank reaching `loss_threshold_kwh`, or the
  liters drawn since the full tank (`drawn_since_full_l`) reaching `draw_threshold_l`, turns it on until the next
  full tank. A draw lets cold water in at the bottom, next to the probe: it restarts the thermostat much sooner than
  the losses. `set_level` estimates it again from the given level; it is unknown while the deficit is.

The thresholds are **learned** when a relay-on session contradicts the estimate: a heating starting while the
estimate was off lowers the threshold of its cause to what was reached (the draws if any since the full tank, else
the losses); a relay on without heating while the estimate was on raises each threshold reached just above it
(+0.05 kWh, +1 L) — kept up while the relay stays on, until the thermostat restarts: its hysteresis is then measured.
A threshold never rises beyond a quarter of the tank: there, a relay on without heating is a fault (the
thermostat's safety cut, a broken heater), and the demand falls back to estimated on once the relay is off.

`energy_before_demand_kwh` and `liters_before_demand_l` are what is left before each threshold (0 while it demands,
None when unknown): with the forecast losses and draws, a planner knows from when a heating is possible.

## Learned standing losses

Between two **real** ends of heating (not a full tank told by `mark_full`, nor after `set_level`) with no liter drawn,
the tank is full at both ends: what was heated (the energy meter's increase, else the power integrated) is what was
lost. `heated_since_full_kwh` counts it from an end of heating; at the next one the cycle gives `heated / hours` if:

- it lasted at least `MIN_LOSS_CYCLE_H` (12 h): from 2026-10-17 to 21, five cycles of 12.6 to 47.6 h measured 87 to
  98 W on a 200 L tank, one of 6.3 h 115 W (the end of heating's spread weighs on a short cycle);
- a hot water meter was read at both ends and counted nothing (without one, a cycle could hide a shower);
- the result is within ¼ to 4 times `losses_w` (beyond, a fault: an energy meter stuck or jumping, a water meter not
  counting; with `losses_w` at 0 there is no reference and any cycle counts). `last_loss_cycle_w` keeps it anyway, to
  show why nothing is learned.

`learned_losses_w` is the duration-weighted mean of the last `LOSS_CYCLES_KEPT` (5) cycles (`loss_cycles`, persisted).
A restart keeps the cycle in progress with an energy meter (it counts the downtime's heating), drops it without one;
so do a replaced meter (`forget_meters`: the heating or the draws between the two are not seen) and, without an energy
meter, the power unavailable while the relay is not off (a heating could go unseen).
The estimate counts the learned losses (`Tank.losses_in_use_w`) only with the option `learn_losses` (rule 25: off until
validated); off, they are measured and published only. Replayed on the history of 2026-10-15 → 25 (200 L, energy and
hot water meters): 94.6 W learned from the 5 long cycles, the 6.3 h one left out, the ends of heating unchanged, the
level at most 1.45 % above the configured 95 W (2026-10-19, with the first cycle's 87 W alone).

## Cumulative meters

The hot water meter (liters) and the heater's energy meter (kWh) are cumulative and may reset (daily meters reset at
midnight). `meter_delta` (in `model/meter.py`) gives the increase between two readings: a drop below half the
previous value is a reset (the new value counts fully), a small drop is jitter (ignored), an increase faster than the
physics allows since the meter's last valid reading — 60 L/min of hot water, 12 kW of heating, plus one minute of
slack — is a spurious value (ignored: a transient drop to a low value then back counts only the low value), an
unavailable reading pauses the meter (the next valid reading resumes from the last valid one, stored with its time).
`Tank.forget_meters` drops a meter's last valid value when another meter replaces it: its next reading is the base,
counted from there (a new meter's total is no draw nor heating).

## Restart

After a restart (`Tank.resume`), only the downtime's duration is known: its losses are counted at the first update,
the heating by the energy meter's increase if there is one; the power last read is **not** held over the downtime (a
restart in the middle of a heating would otherwise count hours of heating), and a heating session in progress is
forgotten (its end, if any, was not seen) — the heat demand and the learned thresholds are kept. The persisted state is read field by field: an unreadable field (truncated
or edited file, `nan`, a time without zone) takes its default, which leaves the estimate unknown rather than wrong.

`hours_since_full` is computed at the last update (the tracker updates at least every 5 minutes), never from the
clock.

## Parameters

| Parameter | Default | Source |
|-----------|---------|--------|
| `volume_l` | 200 | config flow |
| `set_point_c` | 60 | config flow |
| `losses_w` | 95 | config flow; 93.8 W measured on 2026-10-20 (1.241 kWh reheated over 13.23 h without draw) |
| `cold_water_c` | 12 | config flow (fixed value) |
| `learn_losses` | off | config flow (options); the estimate counts the learned losses once on |
| `MIN_LOSS_CYCLE_H`, loss bounds, `LOSS_CYCLES_KEPT` | 12 h, ¼–4 × `losses_w`, 5 | constants |
| `heating_threshold_w`, `idle_power_w`, `end_of_heating_s` | 100 W, 5 W, 60 s | constants |
| losses threshold | `volume × 0.001162 × 5 °C` (1.16 kWh for 200 L) | learned; the thermostat restarted after about 20 L of losses on 2026-10-20 |
| draws threshold | 1 L | learned; not measured yet, the first liter errs toward allowing a heating |
