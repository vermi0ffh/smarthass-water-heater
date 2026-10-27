# Smart Hass - Water Heater

*[Version française](../fr/README.md)*

A Home Assistant integration that estimates the hot water left in an electric water heater. It reads the measures
Home Assistant already has and **never switches the heater**: your automations, or an energy planner such as
Smart Hass - Energy Optimizer, read its entities to decide when to heat.

> Community project, **not affiliated with, endorsed or sponsored by Home Assistant or the Open Home Foundation**.
> Home Assistant is a trademark of the Open Home Foundation.

## How it estimates

The tank is followed in energy: what is missing to a full tank. The hot water drawn (replaced by cold water) and the
heat the tank loses while idle raise it; the heater's energy lowers it. When the thermostat cuts — the heater was
consuming, then draws nothing for a minute while its relay is still on — the tank is full and the estimate starts
again from 100 %. A relay switched off in the middle of a heating, or switched on while the thermostat is satisfied,
is not taken for a full tank.

The level is given in liters of water at the set point, and in percent.

## Requirements

- Home Assistant 2026.10 or later.
- A relay that powers the heater (`switch` or `input_boolean`) and a sensor of the heater's power.
- Recommended: a cumulative meter of the hot water **leaving the tank**, before any mixing valve (liters or m³).
  Without it, a draw is not seen until the next end of heating.
- Optional: the heater's cumulative energy meter (otherwise the energy is computed from the power).

What each measure must show, and an example hot water meter on an ESP8266 with ESPHome: [Hardware](hardware.md).

## Installation

With HACS: add `https://github.com/vermi0ffh/smarthass-water-heater` as a custom repository (category
*Integration*) — or [open it in your Home Assistant](https://my.home-assistant.io/redirect/hacs_repository/?owner=vermi0ffh&repository=smarthass-water-heater&category=integration)
—, install **Smart Hass - Water Heater**, restart Home Assistant, then *Settings → Devices & services → Add
integration*.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| Name | — | The device's name |
| Relay | — | The switch powering the heater |
| Heater power | — | Power sensor (W or kW) |
| Heater energy | none | Cumulative energy meter (Wh or kWh) |
| Hot water meter | none | Cumulative meter at the tank's outlet (L or m³) |
| Tank volume | 200 L | |
| Thermostat set point | 60 °C | |
| Standing losses | 95 W | Heat lost while idle (93.8 W measured on a 200 L tank) |
| Cold water temperature | 12 °C | Water entering the tank |
| Learn the losses | off | Count the losses measured on your tank (the *Learned losses* sensor) in place of the standing losses above |

The tank's parameters can be changed later (*Configure* on the integration), the entities read by reconfiguring it
(*Reconfigure* in its menu): the estimate stays, a replaced meter is counted from its first reading.

## Entities

| Entity | Unit | Description |
|--------|------|-------------|
| Hot water | L | Hot water left, in liters at the set point |
| Hot water level | % | The same, in percent of the tank |
| Energy to full | kWh | Energy the heater needs to fill the tank |
| Time since full | h | Since the last full tank (diagnostic) |
| Last full | time | When the tank was last full (diagnostic) |
| Learned losses | W | The standing losses measured on your tank, unknown until a first cycle (diagnostic, see below) |
| Heat demand | on / off | Would the heater heat with the relay on (the thermostat's demand, see below) |

They are **unknown until the first full tank**: the integration never guesses. To start right away, call
`smart_hass_water_heater.set_level` with your current estimate, or `smart_hass_water_heater.mark_full` after a
heating.

**Heat demand**: after a full tank, the thermostat restarts only once the water has cooled by its hysteresis
(about 5 °C): until then, switching the relay on heats nothing. Once restarted, it heats up to the set point, even if
the relay interrupts it. The entity is *measured* when the relay on shows it (consumption, or 60 s without), else
*estimated* from two thresholds learned on your tank — the losses since the full tank, and the liters drawn (cold
water entering next to the probe restarts it much sooner). Its attributes give the thresholds and what is left
before each one: an energy planner can tell from when a heating is possible.

**When a source entity is unavailable** (or missing, not a number, in an unsupported unit):

- the relay or the heater power, for more than 15 minutes: the hot water, the level and the energy to full become
  *unavailable* (a heating could go unseen) until it comes back, and so does the heat demand; the time since and of
  the last full tank stay;
- the heater energy or the hot water meter: the entities stay; the meter's increase is counted when it comes back.

Shorter outages (a restart of Home Assistant, an update of the device) change nothing. Every source unavailable for
15 minutes is logged once, with the cause, and once when it is back. A source entity that **no longer exists**
(renamed, its device removed) also raises a repair in *Settings* > *Repairs*: reconfigure the water heater to choose
its replacement.

## Services

- `smart_hass_water_heater.mark_full` (`config_entry_id`): the tank is full now.
- `smart_hass_water_heater.set_level` (`config_entry_id`, `level_l`): sets the hot water left, in liters at the set
  point.

## How the data is updated

Nothing is polled. The estimate follows **every state change** of the source entities, and is refreshed every
5 minutes in between (the standing losses accrue, the time since full moves). The end of heating is checked 60 s
after the power falls, without waiting for a new reading. The estimate is saved 30 s after a change and when Home
Assistant stops: a restart loses nothing, and the losses of the downtime are counted.

## Use cases

- **Heat only when needed**: top up off-peak, or on solar surplus, only below a level — instead of heating every
  night.
- **Plan the heating**: an energy planner (e.g. Smart Hass - Energy Optimizer) reads the level and the energy to full
  to place the heating in the cheapest hours and keep enough hot water for the evening. The *Hot water level* entity
  carries, as attributes, everything it needs: the tank (volume, capacity, losses, temperatures), the energy to full,
  the time since full, the heat demand and its thresholds, and the source entities — one entity to configure.
- **Anti-legionella**: heat the whole tank when the time since full exceeds a few days.
- **Know before the shower**: a dashboard or a notification when the hot water runs low.

## Examples

The entity ids below are those of a water heater named *Water heater*, with Home Assistant in English; yours
follow the name you gave and Home Assistant's language when it was created.

Top up at 02:00 only when below half a tank (the thermostat cuts when full; the relay is switched off once full):

```yaml
automation:
  - alias: Water heater - off-peak top-up
    triggers:
      - trigger: time
        at: "02:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.water_heater_hot_water_level
        below: 50
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.water_heater
  - alias: Water heater - relay off once full
    triggers:
      - trigger: state
        entity_id: sensor.water_heater_last_full
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.water_heater
```

Anti-legionella, 72 h without a full tank:

```yaml
automation:
  - alias: Water heater - anti-legionella
    triggers:
      - trigger: numeric_state
        entity_id: sensor.water_heater_time_since_full
        above: 72
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.water_heater
```

A notification when less than 40 L are left:

```yaml
automation:
  - alias: Water heater - low hot water
    triggers:
      - trigger: numeric_state
        entity_id: sensor.water_heater_hot_water
        below: 40
    actions:
      - action: notify.notify
        data:
          message: "Less than 40 L of hot water left."
```

## Known limitations

- **One temperature for the whole tank**: the level is an equivalent in liters at the set point. A real tank is
  stratified (hot at the top), and a mixing valve stretches hot water: you may get more usable water than shown.
- **Constant parameters**: the cold water temperature (about 8 °C in winter, 18 °C in summer) and the standing
  losses (they depend on the room's temperature) are fixed options. An error on them shows as a jump at the next
  full tank; adjust them if the jump is always in the same direction.
- **The losses are learned on cycles without any draw**: between two full tanks at least 12 h apart, with a hot water
  meter that counted no liter, what was heated is what was lost. Such cycles mostly happen during an absence (it
  takes about 12 h of losses alone to restart the thermostat); the *Learned losses* sensor shows the losses of the
  last five, its attributes the configured ones, those in use and the last cycle measured (even when rejected, beyond
  ¼ to 4 times the configured ones: a fault). They replace the standing losses only once *Learn the losses* is on.
- **Calibrated by the thermostat only**: the estimate is reset by a full tank. A tank never heated until the
  thermostat cuts (the relay always switched off before) drifts; heat until full now and then, or call `mark_full`.
- **Without a hot water meter**, a draw is seen only at the next end of heating, and the heat demand is estimated
  from the losses only.
- **The heat demand learns from the relay**: its thresholds are corrected when the relay is switched on (heating or
  not). The draws threshold starts at the first liter, which errs toward allowing a heating, and rises as the relay
  on shows that a small draw does not restart the thermostat.
- **Resistive heaters only**: a heat pump water heater's power is not the heat it gives (its efficiency varies), and
  a heater with its own timer or eco mode may cut without the tank being full.
- **One relay, one tank**: tanks in series or with several elements on separate relays are not supported.

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| The level stays *unknown* | No full tank seen yet: call `set_level` with your estimate, or let one heating run until the thermostat cuts |
| A full tank is never seen | The relay must stay on until the thermostat cuts, and the power sensor must read under 5 W within a minute (a sensor published every few minutes is too slow, see [Hardware](hardware.md)) |
| The level is *unavailable* | The relay or the power has been unavailable for 15 minutes: the log says which and why; *Settings* > *Repairs* if the entity no longer exists |
| The level falls too fast or too slowly | The standing losses and the cold water temperature (options); the meter's unit (L or m³) and its placement (before any mixing valve) |
| A draw is counted twice or not at all | The meter's total: a total that goes back to 0 is a reset and counts from there; pulses lost while the meter was offline cannot be recovered |

To report a problem, attach the **diagnostics** (*Settings* > *Devices & services* > *Smart Hass - Water Heater* >
the water heater > ⋮ > *Download diagnostics*): the options, the estimate, the model's state and what each source
entity shows.

## Removal

1. *Settings* > *Devices & services* > *Smart Hass - Water Heater* > ⋮ on each water heater > *Delete*: its entities
   and its stored estimate are removed.
2. In HACS, remove *Smart Hass - Water Heater*, then restart Home Assistant.

Update first what reads its entities (automations, dashboards, an energy planner): they become unavailable.
