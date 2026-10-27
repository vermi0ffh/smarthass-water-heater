# Changelog

Versions follow [semantic versioning](https://semver.org/). Before 1.0.0, a minor version may change the options or
the entities; such a change is described here, with what to do.

## 0.1.0 (2026-10-27)

First version.

- Hot water left (liters at the set point, percent), energy to full, time since and time of the last full tank.
- Energy balance: draws from a hot water meter at the tank's outlet, standing losses, heating from the heater's
  energy meter or its power; full at a real end of heating (the thermostat cuts, relay on).
- Services `mark_full` and `set_level`; the estimate survives restarts.
- Heat demand (binary sensor): would the heater heat with the relay on — the thermostat's hysteresis, measured
  when the relay is on, else estimated from losses and draws thresholds learned on the tank.
- The hot water level's attributes give a planner the tank, the estimate, the heat demand and its thresholds, and the
  source entities.
- Reconfigure: the entities read; the options: the tank's parameters. A replaced meter is counted from its first
  reading.
- A source unavailable for 15 minutes is logged with the cause; without the relay or the power, the level becomes
  unavailable until it comes back.
- A repair when a source entity no longer exists, telling to reconfigure.
- Diagnostics: the options, the estimate, the model's state and the source entities' current states.
- Icon and logo, shown by Home Assistant (light and dark themes).
- Icons; the time since and the time of the last full tank in the diagnostic category.
- Standing losses learned between two full tanks at least 12 h apart without any draw (sensor *Learned losses*,
  diagnostic); counted in place of the configured losses only with the option *Learn the losses* (off by default).
- Deleting a water heater removes its stored estimate.
- Documentation: data updates, use cases, automation examples, known limitations, troubleshooting, removal.
- Hardware documentation: what each measure needs, an ESPHome hot water meter example.
