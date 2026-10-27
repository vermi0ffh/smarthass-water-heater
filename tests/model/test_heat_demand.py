"""Tests — the thermostat's heat demand: held from its start to the full tank, measured with the relay on, estimated
from learned thresholds otherwise."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.smart_hass_water_heater.model import Reading, Tank, TankConfig, state_from_dict, state_to_dict
from custom_components.smart_hass_water_heater.model.tank import WATER_KWH_PER_L_C

T0 = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
CONFIG = TankConfig(volume_l=200.0, set_point_c=60.0, losses_w=95.0, cold_water_c=8.5)
OFF = Reading(relay_on=False, power_w=0.0)
# 5 °C over 200 L: the first losses threshold (about 20 L of losses, 2026-10-20)
DEFAULT_LOSS_KWH = 200.0 * WATER_KWH_PER_L_C * 5.0


def _full_tank(water_l: float = 0.0) -> Tank:
    tank = Tank(CONFIG)
    tank.mark_full(T0)
    tank.update(T0, Reading(False, 0.0, hot_water_l=water_l))
    return tank


def _hours_of_losses(kwh: float) -> timedelta:
    return timedelta(hours=kwh / 0.095)


def test_no_demand_after_a_full_tank_until_the_losses_reach_the_hysteresis():
    tank = _full_tank()
    assert (tank.heat_demand, tank.heat_demand_measured) == (False, True)
    assert tank.energy_before_demand_kwh == pytest.approx(DEFAULT_LOSS_KWH)
    tank.update(T0 + _hours_of_losses(DEFAULT_LOSS_KWH - 0.1), OFF)
    assert tank.heat_demand is False
    assert tank.energy_before_demand_kwh == pytest.approx(0.1)
    tank.update(T0 + _hours_of_losses(DEFAULT_LOSS_KWH + 0.01), OFF)
    assert (tank.heat_demand, tank.heat_demand_measured) == (True, False)
    assert tank.energy_before_demand_kwh == 0.0


def test_a_draw_starts_the_demand():
    tank = _full_tank(water_l=10.0)
    assert tank.liters_before_demand_l == pytest.approx(1.0)
    tank.update(T0 + timedelta(minutes=10), Reading(False, 0.0, hot_water_l=11.0))
    assert (tank.heat_demand, tank.heat_demand_measured) == (True, False)
    assert tank.liters_before_demand_l == 0.0


def test_the_demand_is_held_when_the_relay_interrupts_a_heating():
    """The thermostat, once restarted, heats up to its set point even after the relay cut it (2026-10-20)."""
    tank = _full_tank(water_l=10.0)
    start = T0 + timedelta(hours=1)
    tank.update(start, Reading(False, 0.0, hot_water_l=60.0))  # a 50 L shower
    tank.update(start + timedelta(minutes=30), Reading(True, 2300.0, hot_water_l=60.0))
    assert (tank.heat_demand, tank.heat_demand_measured) == (True, True)
    tank.update(start + timedelta(minutes=50), Reading(False, 0.0, hot_water_l=60.0))  # deficit now below 1 L
    tank.update(start + timedelta(hours=3), Reading(False, 0.0, hot_water_l=60.0))
    assert tank.heat_demand is True
    assert tank.energy_to_full_kwh > 0


def test_the_relay_on_without_heating_raises_the_losses_threshold():
    """Estimated on, but the relay on does not heat: the thermostat had not restarted yet."""
    tank = _full_tank()
    reached = T0 + _hours_of_losses(DEFAULT_LOSS_KWH + 0.2)
    tank.update(reached, OFF)
    assert tank.heat_demand is True
    tank.update(reached, Reading(True, 0.0))
    assert tank.heat_demand_due_at == reached + timedelta(seconds=60)
    tank.update(reached + timedelta(seconds=60), Reading(True, 0.0))
    assert (tank.heat_demand, tank.heat_demand_measured) == (False, True)
    deficit = tank.energy_to_full_kwh
    assert tank.loss_threshold_kwh == pytest.approx(deficit + 0.05)
    assert tank.energy_before_demand_kwh == pytest.approx(0.05)
    assert tank.heat_demand_due_at is None  # seen: no more check due


def test_a_heating_before_the_losses_threshold_lowers_it():
    """The thermostat restarted by itself, the relay on since the full tank: its hysteresis is measured."""
    tank = _full_tank()
    tank.update(T0, Reading(True, 0.0))
    restart = T0 + _hours_of_losses(0.8)
    tank.update(restart, Reading(True, 0.0))
    assert tank.heat_demand is False
    tank.update(restart, Reading(True, 2300.0))
    assert (tank.heat_demand, tank.heat_demand_measured) == (True, True)
    assert tank.loss_threshold_kwh == pytest.approx(0.8)


def test_a_heating_after_a_draw_learns_the_draws_threshold():
    tank = _full_tank(water_l=10.0)
    tank.update(T0, Reading(True, 0.0, hot_water_l=13.0))
    tank.update(T0 + timedelta(minutes=5), Reading(True, 0.0, hot_water_l=13.0))
    assert tank.heat_demand is False  # 3 L drawn, the relay on for 5 min without heating
    assert tank.draw_threshold_l == pytest.approx(4.0)  # raised just above
    tank.update(T0 + timedelta(minutes=6), Reading(True, 0.0, hot_water_l=16.0))
    tank.update(T0 + timedelta(minutes=7), Reading(True, 2300.0, hot_water_l=16.0))
    assert tank.draw_threshold_l == pytest.approx(6.0)
    assert tank.loss_threshold_kwh == pytest.approx(DEFAULT_LOSS_KWH)  # the draws were the cause


def test_a_relay_on_that_never_heats_stops_the_learning_at_a_quarter_of_the_tank():
    """A fault (the thermostat's safety cut): the demand falls back to estimated on once the relay is off."""
    tank = _full_tank()
    tank.update(T0, Reading(True, 0.0))
    for hours in range(1, 120):
        tank.update(T0 + timedelta(hours=hours), Reading(True, 0.0))
    assert tank.loss_threshold_kwh == pytest.approx(CONFIG.capacity_kwh / 4)
    assert tank.heat_demand is False
    tank.update(T0 + timedelta(hours=121), OFF)
    assert (tank.heat_demand, tank.heat_demand_measured) == (True, False)


def test_a_set_level_estimates_the_demand_again():
    tank = _full_tank()
    tank.set_level_l(150.0)
    assert (tank.heat_demand, tank.heat_demand_measured) == (True, False)
    tank.set_level_l(199.0)
    assert (tank.heat_demand, tank.heat_demand_measured) == (False, False)


def test_unknown_until_the_level_is_known():
    tank = Tank(CONFIG)
    tank.update(T0, OFF)
    assert tank.heat_demand is None
    assert (tank.energy_before_demand_kwh, tank.liters_before_demand_l) == (None, None)


def test_the_demand_and_the_learned_thresholds_survive_a_restart():
    tank = _full_tank()
    tank.update(T0, Reading(True, 0.0))
    tank.update(T0 + _hours_of_losses(0.8), Reading(True, 2300.0))
    restored = Tank(CONFIG, state_from_dict(state_to_dict(tank.state)))
    restored.resume()
    assert (restored.heat_demand, restored.heat_demand_measured) == (True, True)
    assert restored.loss_threshold_kwh == pytest.approx(0.8)


def test_a_state_stored_before_the_demand_estimates_it():
    """A state of the first version has no demand: it is estimated from the deficit at the first update."""
    tank = _full_tank()
    tank.update(T0 + timedelta(hours=1), OFF)
    old = {key: value for key, value in state_to_dict(tank.state).items() if key in _VERSION_1_FIELDS}
    restored = Tank(CONFIG, state_from_dict(old))
    assert restored.heat_demand is None
    restored.update(T0 + timedelta(hours=2), OFF)
    assert (restored.heat_demand, restored.heat_demand_measured) == (False, False)
    assert restored.loss_threshold_kwh == pytest.approx(DEFAULT_LOSS_KWH)


_VERSION_1_FIELDS = {
    "deficit_kwh",
    "last_full",
    "updated_at",
    "reading",
    "energy_meter_kwh",
    "energy_meter_at",
    "hot_water_meter_l",
    "hot_water_meter_at",
    "heated_this_session",
    "idle_since",
}
