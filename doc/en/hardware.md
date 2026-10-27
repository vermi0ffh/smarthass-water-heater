# Hardware

*[Version française](../fr/hardware.md)*

The integration reads entities Home Assistant already has: it works with any hardware that provides them. This page
describes what each measure must show, and a hot water meter built with an ESP8266 and ESPHome.

## What the integration needs

| Measure | Required | What it must show |
|---------|----------|-------------------|
| Relay | yes | The switch powering the heater (`switch` or `input_boolean`), whose state follows the real contact |
| Heater power | yes | The power drawn by the heater (W or kW), measured downstream of the relay |
| Hot water meter | recommended | The cumulative volume leaving the tank (L or m³) |
| Heater energy | optional | The heater's cumulative energy (Wh or kWh) |

### Relay and power

The full tank is detected from the thermostat cutting: during a heating, the power falls below 5 W while the relay
stays on, for 60 s. The power sensor must therefore:

- measure the heater itself, not the whole house: a heater cut by its thermostat must read near 0 W;
- report a change within a few seconds (a sensor published only every few minutes delays the full tank, and, without
  an energy meter, makes the heating energy coarse).

A smart relay with power metering does both. Without an energy meter, the energy is the power integrated over time;
with one, its increase is used instead and the power serves only to detect the end of heating.

Tested: a Shelly 1PM switches the heater and measures it (relay with built-in power metering); its power (W) and its
cumulative energy (kWh) sensors are the integration's *Heater power* and *Heater energy*.

### Hot water meter

Without a hot water meter, the level only falls with the standing losses: a draw is not seen, the level stays too
high until the next end of heating sets it back to 100 %. With one, every liter drawn lowers it right away.

- **Placement**: at the tank's **outlet**, before any thermostatic mixing valve. After the valve, the meter counts
  mixed water (hot and cold), which the integration would take for water at the set point.
- **Type**: a meter rated for hot water (≥ 90 °C), with a pulse output (dry contact).
- **Resolution**: 1 pulse per liter is enough.
- **Counting**: a cumulative total (`state_class: total_increasing`). The integration handles a total going back to
  zero (a daily meter at midnight, a counter restarted after a reboot); it cannot recover pulses that were not
  counted.

## Example: pulse meter on an ESP8266 with ESPHome

Setup in use on a 200 L tank:

- a Gioanola 3/4" hot water meter, 1 pulse per liter, at the tank's outlet;
- its dry contact wired between GPIO14 (pin D5) and GND of a Wemos D1 Mini (ESP8266), with the internal pull-up;
- the ESPHome configuration [`../examples/esphome-hot-water-meter.yaml`](../examples/esphome-hot-water-meter.yaml).

The configuration publishes two sensors:

- **the total in liters** (`total_increasing`): the entity to give as the integration's *Hot water meter*, directly
  or through a daily `utility_meter`;
- **the flow** (L/min, back to 0 after 10 s without a pulse), for dashboards.

Points that matter:

- **Debounce**: the contact bounces when it closes; the 50 ms `delayed_on` / `delayed_off` filters count each liter
  once.
- **Reboot**: the total is kept in flash (`restore_value` on the counter, `restore_from_flash` on an ESP8266) and
  published again at boot, so an update or a restart of the ESP keeps it. It is written at most every 5 min
  (`flash_write_interval`): a power cut loses at most the liters of the last 5 min, and the total going down a little
  is taken for a reset by the integration, without counting twice. Without this, the counter starts again from 0 and
  stays unknown until the first liter: the integration counts from the new value, only the liters drawn while the ESP
  was offline are lost. Checked on 2026-10-25: 1 L drawn, ESP restarted 18 min later, the total came back at 1 L.
