"""Tests — the standing losses learned between two ends of heating without any draw."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.smart_hass_water_heater.model import Reading, Tank, TankConfig, state_from_dict, state_to_dict

T0 = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
# The tank of the house the model was measured on, with its energy meter: 200 L at 60 °C, 95 W configured
CONFIG = TankConfig(volume_l=200.0, set_point_c=60.0, losses_w=95.0, cold_water_c=8.5, has_energy_meter=True)
METER_KWH = 6969.0


def _reheat(tank: Tank, full_at: datetime, energy_kwh: float | None, water_l: float | None = 0.0) -> None:
    """A heating up to the thermostat's cut at `full_at`: 2.3 kW for 29 min, the energy meter (None: none) reading
    `energy_kwh` from its end."""
    start = full_at - timedelta(minutes=30)
    tank.update(start, Reading(True, 2300.0, energy_kwh=None, hot_water_l=water_l))
    tank.update(full_at - timedelta(seconds=60), Reading(True, 0.0, energy_kwh=energy_kwh, hot_water_l=water_l))
    tank.update(full_at, Reading(True, 0.0, energy_kwh=energy_kwh, hot_water_l=water_l))
    assert tank.last_full == full_at


def _cycle(tank: Tank, hours: float, heated_kwh: float, start_kwh: float = METER_KWH) -> float:
    """A full tank at T0, then another `hours` later after `heated_kwh` of heating; returns the meter at the end."""
    _reheat(tank, T0, start_kwh)
    _reheat(tank, T0 + timedelta(hours=hours), start_kwh + heated_kwh)
    return start_kwh + heated_kwh


def test_a_night_without_draw_measures_the_losses():
    """2026-10-20: 1.241 kWh reheated over 13.23 h without draw: 93.8 W."""
    tank = Tank(CONFIG)
    _cycle(tank, 13.23, 1.241)
    assert tank.learned_losses_w == pytest.approx(93.8, abs=0.05)
    assert (tank.loss_cycle_count, tank.last_loss_cycle_w) == (1, pytest.approx(93.8, abs=0.05))


def test_measured_but_not_counted_until_enabled():
    tank = Tank(CONFIG)
    _cycle(tank, 13.23, 1.241)
    tank.update(T0 + timedelta(hours=23.23), Reading(False, 0.0, energy_kwh=METER_KWH + 1.241, hot_water_l=0.0))
    assert tank.losses_in_use_w == 95.0
    assert tank.energy_to_full_kwh == pytest.approx(0.95)  # 10 h at the configured 95 W


def test_counted_once_enabled():
    tank = Tank(replace(CONFIG, learn_losses=True))
    assert tank.losses_in_use_w == 95.0  # the configured ones until a cycle is measured
    _cycle(tank, 13.23, 1.241)
    tank.update(T0 + timedelta(hours=23.23), Reading(False, 0.0, energy_kwh=METER_KWH + 1.241, hot_water_l=0.0))
    assert tank.losses_in_use_w == pytest.approx(93.8, abs=0.05)
    assert tank.energy_to_full_kwh == pytest.approx(0.938, abs=0.001)


def test_a_draw_is_no_cycle_of_losses():
    tank = Tank(CONFIG)
    _reheat(tank, T0, METER_KWH)
    tank.update(T0 + timedelta(hours=6), Reading(False, 0.0, energy_kwh=METER_KWH, hot_water_l=1.0))  # 1 L drawn
    _reheat(tank, T0 + timedelta(hours=14), METER_KWH + 1.4, water_l=1.0)
    assert (tank.learned_losses_w, tank.loss_cycle_count) == (None, 0)


def test_a_short_cycle_is_not_counted():
    """2026-10-21: 6.3 h without draw measured 115 W — the end of heating's spread weighs on a short cycle."""
    tank = Tank(CONFIG)
    _cycle(tank, 6.3, 0.73)
    assert (tank.learned_losses_w, tank.last_loss_cycle_w) == (None, None)


def test_a_fault_is_not_learned():
    """An energy meter that stopped counting: 0.1 kWh over 24 h is 4 W, not losses."""
    tank = Tank(CONFIG)
    _cycle(tank, 24.0, 0.1)
    assert tank.learned_losses_w is None
    assert tank.last_loss_cycle_w == pytest.approx(4.2, abs=0.05)  # shown, to understand why nothing is learned


def test_a_jump_of_the_energy_meter_is_not_learned():
    """10 kWh over 12 h is 833 W, beyond 4 × 95 W: a meter that jumped, not losses."""
    tank = Tank(CONFIG)
    _cycle(tank, 12.0, 10.0)
    assert tank.learned_losses_w is None
    assert tank.last_loss_cycle_w == pytest.approx(833.3, abs=0.1)


def test_learned_without_bounds_when_configured_at_zero():
    """No losses configured (0 W): no reference to call a cycle a fault, it is learned."""
    tank = Tank(replace(CONFIG, losses_w=0.0))
    _cycle(tank, 13.23, 1.241)
    assert tank.learned_losses_w == pytest.approx(93.8, abs=0.05)


def test_the_last_cycles_weighted_by_their_duration():
    tank = Tank(CONFIG)
    meter = _cycle(tank, 12.0, 1.2)  # 100 W
    _reheat(tank, T0 + timedelta(hours=60), meter + 4.8 * 0.9)  # 48 h, 90 W
    assert tank.learned_losses_w == pytest.approx(1000 * (1.2 + 4.32) / 60)
    assert tank.loss_cycle_count == 2


def test_only_the_last_five_cycles_count():
    tank = Tank(CONFIG)
    meter, at = METER_KWH, T0
    _reheat(tank, at, meter)
    for watts in (200.0, 100.0, 100.0, 100.0, 100.0, 100.0):
        at, meter = at + timedelta(hours=12), meter + watts * 12 / 1000
        _reheat(tank, at, meter)
    assert tank.loss_cycle_count == 5
    assert tank.learned_losses_w == pytest.approx(100.0)  # the first cycle, 200 W, dropped


def test_no_cycle_from_a_full_tank_told_or_a_level_set():
    told, set_level = Tank(CONFIG), Tank(CONFIG)
    for tank in (told, set_level):
        _reheat(tank, T0, METER_KWH)
    told.mark_full(T0 + timedelta(minutes=1))
    set_level.set_level_l(190.0)
    for tank in (told, set_level):
        _reheat(tank, T0 + timedelta(hours=14), METER_KWH + 1.33)
        assert tank.learned_losses_w is None


def test_no_cycle_without_a_hot_water_meter():
    """Without one the draws are unknown: a cycle could hide a shower."""
    tank = Tank(CONFIG)
    _reheat(tank, T0, METER_KWH, water_l=None)
    _reheat(tank, T0 + timedelta(hours=14), METER_KWH + 1.33, water_l=None)
    assert tank.learned_losses_w is None


def test_a_hot_water_meter_lost_at_the_end_is_no_cycle():
    """Unavailable at the end of heating, the meter may hide a draw: no cycle, and none starts."""
    tank = Tank(CONFIG)
    _reheat(tank, T0, METER_KWH)
    meter = METER_KWH + 1.33
    _reheat(tank, T0 + timedelta(hours=14), meter, water_l=None)
    _reheat(tank, T0 + timedelta(hours=28), meter + 1.33)
    assert tank.learned_losses_w is None


def test_a_replaced_meter_drops_the_cycle_in_progress():
    """The heating or the draws between the two meters are not seen."""
    for energy, hot_water in ((True, False), (False, True)):
        tank = Tank(CONFIG)
        _reheat(tank, T0, METER_KWH)
        tank.forget_meters(energy=energy, hot_water=hot_water)
        new_meter = 500.0 if energy else METER_KWH  # the new meter's first reading is its base
        tank.update(T0 + timedelta(hours=1), Reading(False, 0.0, energy_kwh=new_meter, hot_water_l=0.0))
        _reheat(tank, T0 + timedelta(hours=14), new_meter + 1.33)
        assert tank.learned_losses_w is None, (energy, hot_water)


def test_an_unavailable_power_drops_the_cycle_without_energy_meter():
    """By power, a heating while the power is unavailable would go unseen."""
    tank = Tank(replace(CONFIG, has_energy_meter=False))
    _reheat(tank, T0, None)
    tank.update(T0 + timedelta(hours=5), Reading(True, None, hot_water_l=0.0))
    tank.update(T0 + timedelta(hours=6), Reading(False, 0.0, hot_water_l=0.0))
    _reheat(tank, T0 + timedelta(hours=14), None)
    assert tank.learned_losses_w is None


def test_a_state_stored_before_the_learning():
    """Stored by a version without the losses' fields: nothing learned, no cycle in progress."""
    data = state_to_dict(Tank(CONFIG).state)
    for key in ("heated_since_full_kwh", "loss_cycles", "last_loss_cycle_w"):
        del data[key]
    tank = Tank(CONFIG, state_from_dict(data))
    assert (tank.learned_losses_w, tank.last_loss_cycle_w, tank.loss_cycle_count) == (None, None, 0)


def test_the_learning_survives_a_restart():
    tank = Tank(CONFIG)
    meter = _cycle(tank, 13.23, 1.241)
    restored = Tank(CONFIG, state_from_dict(state_to_dict(tank.state)))
    restored.resume()
    assert restored.learned_losses_w == tank.learned_losses_w
    # The cycle in progress goes on across the restart: the energy meter counted the heating of the downtime
    _reheat(restored, T0 + timedelta(hours=13.23 + 12), meter + 1.2)
    assert restored.loss_cycle_count == 2


def test_a_restart_without_energy_meter_drops_the_cycle_in_progress():
    """By power, the heating during the downtime is not seen: the cycle would count it as no loss."""
    config = replace(CONFIG, has_energy_meter=False)
    tank = Tank(config)
    _reheat(tank, T0, None)
    restored = Tank(config, state_from_dict(state_to_dict(tank.state)))
    restored.resume()
    _reheat(restored, T0 + timedelta(hours=14), None)
    assert restored.learned_losses_w is None


def test_by_power_without_an_energy_meter():
    """The heating integrated from the power: 2.3 kW for 29 min is 1.11 kWh over 12.5 h, 89 W."""
    tank = Tank(replace(CONFIG, has_energy_meter=False))
    _reheat(tank, T0, None)
    _reheat(tank, T0 + timedelta(hours=12.5), None)
    assert tank.learned_losses_w == pytest.approx(1000 * 2.3 * (29 / 60) / 12.5, abs=0.5)


@pytest.mark.parametrize("cycles", [None, "x", [[1.0]], [["a", 2.0]], [[-1.0, 2.0]]])
def test_unreadable_stored_cycles_are_dropped(cycles):
    data = state_to_dict(Tank(CONFIG).state) | {"loss_cycles": cycles}
    assert Tank(CONFIG, state_from_dict(data)).learned_losses_w is None
