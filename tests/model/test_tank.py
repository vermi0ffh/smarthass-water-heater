"""Tests — the tank model: energy balance, end of heating, persistence."""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.smart_hass_water_heater.model import (
    Reading,
    Tank,
    TankConfig,
    state_from_dict,
    state_to_dict,
)
from custom_components.smart_hass_water_heater.model.tank import WATER_KWH_PER_L_C

T0 = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
# The tank of the house the model was measured on: 200 L at 60 °C, cold water 8.5 °C, 95 W of losses
CONFIG = TankConfig(volume_l=200.0, set_point_c=60.0, losses_w=95.0, cold_water_c=8.5)
OFF = Reading(relay_on=False, power_w=0.0)


def _full_tank() -> Tank:
    tank = Tank(CONFIG)
    tank.mark_full(T0)
    tank.update(T0, OFF)
    return tank


def test_unknown_until_full_or_set():
    tank = Tank(CONFIG)
    tank.update(T0, OFF)
    assert (tank.level_l, tank.level_pct, tank.energy_to_full_kwh) == (None, None, None)
    tank.set_level_l(150.0)
    assert tank.level_l == pytest.approx(150.0)


def test_losses_over_a_night_without_draw():
    """2026-10-20: 13.23 h without draw, 1.241 kWh reheated: the model loses 1.257 kWh at 95 W, 0.3 L of difference."""
    tank = _full_tank()
    tank.update(T0 + timedelta(hours=13.23), OFF)
    assert tank.energy_to_full_kwh == pytest.approx(95.0 * 13.23 / 1000)
    assert tank.level_l == pytest.approx(200.0 * (1 - 1.257 / CONFIG.capacity_kwh), abs=0.01)
    assert tank.hours_since_full == pytest.approx(13.23)


def test_a_draw_replaced_by_cold_water():
    tank = _full_tank()
    tank.update(T0, Reading(False, 0.0, hot_water_l=100.0))
    tank.update(T0, Reading(False, 0.0, hot_water_l=140.0))  # a 40 L shower
    assert tank.energy_to_full_kwh == pytest.approx(40 * WATER_KWH_PER_L_C * (60 - 8.5))
    assert tank.level_l == pytest.approx(160.0)


def _full_tank_with_meter() -> Tank:
    tank = Tank(replace(CONFIG, has_energy_meter=True))
    tank.mark_full(T0)
    tank.update(T0, OFF)
    return tank


def test_heating_by_energy_meter_and_by_power():
    by_meter, by_power = _full_tank_with_meter(), _full_tank()
    for tank in (by_meter, by_power):
        tank.set_level_l(100.0)
    start = T0 + timedelta(hours=1)
    by_meter.update(start, Reading(True, 2300.0, energy_kwh=10.0))
    by_meter.update(start + timedelta(hours=1), Reading(True, 2300.0, energy_kwh=12.3))
    by_power.update(start, Reading(True, 2300.0))
    by_power.update(start + timedelta(hours=1), Reading(True, 2300.0))
    assert by_meter.energy_to_full_kwh == pytest.approx(by_power.energy_to_full_kwh, abs=1e-6)
    expected = CONFIG.capacity_kwh / 2 + 0.095 * 2 - 2.3  # half empty, 2 h of losses, 2.3 kWh heated
    assert by_power.energy_to_full_kwh == pytest.approx(expected)


def _heat_then_idle(tank: Tank, idle_s: float, relay_on_at_end: bool = True) -> datetime:
    start = T0 + timedelta(hours=2)
    tank.update(start, Reading(True, 0.0))
    tank.update(start + timedelta(seconds=5), Reading(True, 2300.0))
    stop = start + timedelta(minutes=30)
    tank.update(stop, Reading(relay_on_at_end, 0.0))
    tank.update(stop + timedelta(seconds=idle_s), Reading(relay_on_at_end, 0.0))
    return stop


def test_end_of_heating_sets_the_tank_full():
    tank = _full_tank()
    tank.set_level_l(120.0)
    stop = _heat_then_idle(tank, idle_s=60)
    assert tank.energy_to_full_kwh == 0.0
    assert tank.last_full == stop + timedelta(seconds=60)


def test_not_full_before_the_idle_delay():
    tank = _full_tank()
    tank.set_level_l(120.0)
    stop = _heat_then_idle(tank, idle_s=30)
    assert tank.energy_to_full_kwh > 0
    assert tank.end_of_heating_due_at == stop + timedelta(seconds=60)


def test_relay_switched_off_while_heating_is_not_full():
    tank = _full_tank()
    tank.set_level_l(120.0)
    _heat_then_idle(tank, idle_s=120, relay_on_at_end=False)
    assert tank.energy_to_full_kwh > 0
    assert tank.last_full == T0


def test_relay_on_without_heating_is_not_full():
    """2026-10-18: the former automation saw a full tank when the relay was switched on and the thermostat was
    satisfied (no consumption), with a tank not full."""
    tank = _full_tank()
    tank.set_level_l(170.0)
    tank.update(T0 + timedelta(hours=3), Reading(True, 0.0))
    tank.update(T0 + timedelta(hours=4), Reading(True, 0.0))
    assert tank.last_full == T0 and tank.energy_to_full_kwh > 0


def test_unavailable_power_pauses_the_heating():
    tank = _full_tank()
    tank.set_level_l(100.0)
    deficit = tank.energy_to_full_kwh
    tank.update(T0 + timedelta(hours=1), Reading(True, None))
    tank.update(T0 + timedelta(hours=2), Reading(True, None))
    assert tank.energy_to_full_kwh == pytest.approx(deficit + 0.095 * 2)  # losses only, never a heating at 0 W read


def test_state_survives_a_restart():
    tank = _full_tank()
    tank.update(T0, Reading(False, 0.0, energy_kwh=6969.9, hot_water_l=12.0))
    tank.update(T0 + timedelta(hours=1), Reading(False, 0.0, energy_kwh=6969.9, hot_water_l=30.0))
    restored = Tank(CONFIG, state_from_dict(state_to_dict(tank.state)))
    assert restored.energy_to_full_kwh == tank.energy_to_full_kwh
    assert restored.last_full == T0
    restored.update(T0 + timedelta(hours=1), Reading(False, 0.0, energy_kwh=6969.9, hot_water_l=40.0))
    assert restored.level_l == pytest.approx(tank.level_l - 10.0, abs=0.01)  # resumes from the stored meter


def test_energy_meter_unavailable_counts_the_heating_once():
    """The power is never integrated with an energy meter: the meter's increase counts when it comes back."""
    tank = _full_tank_with_meter()
    tank.set_level_l(100.0)
    deficit = tank.energy_to_full_kwh
    start = T0 + timedelta(hours=1)
    tank.update(start, Reading(True, 2300.0, energy_kwh=10.0))
    tank.update(start + timedelta(minutes=30), Reading(True, 2300.0, energy_kwh=None))  # meter unavailable
    tank.update(start + timedelta(hours=1), Reading(True, 2300.0, energy_kwh=12.3))
    assert tank.energy_to_full_kwh == pytest.approx(deficit + 0.095 * 2 - 2.3)


def test_a_transient_meter_drop_is_not_a_huge_draw():
    tank = _full_tank()
    tank.update(T0, Reading(False, 0.0, hot_water_l=180.0))
    tank.update(T0 + timedelta(seconds=10), Reading(False, 0.0, hot_water_l=0.0))  # glitch read as a reset
    tank.update(T0 + timedelta(seconds=20), Reading(False, 0.0, hot_water_l=180.0))  # back: 180 L in 10 s, ignored
    assert tank.level_l == pytest.approx(200.0, abs=0.01)


def test_the_daily_meter_reset_keeps_counting():
    tank = _full_tank()
    tank.update(T0, Reading(False, 0.0, hot_water_l=180.0))
    tank.update(T0 + timedelta(minutes=10), Reading(False, 0.0, hot_water_l=4.0))  # midnight: reset, then 4 L drawn
    losses_l = 200.0 * (0.095 / 6) / CONFIG.capacity_kwh  # 10 min of losses
    assert tank.level_l == pytest.approx(196.0 - losses_l, abs=0.01)


def test_the_deficit_stays_within_the_tank():
    tank = _full_tank()
    tank.update(T0, Reading(True, 2300.0))
    tank.update(T0 + timedelta(hours=2), Reading(True, 2300.0))  # heating a full tank: stays full
    assert tank.energy_to_full_kwh == 0.0
    tank.update(T0 + timedelta(hours=2), Reading(False, 0.0, hot_water_l=0.0))
    tank.update(T0 + timedelta(hours=3), Reading(False, 0.0, hot_water_l=900.0))  # more than the tank
    assert tank.level_l == 0.0


def test_relay_unknown_ends_the_heating_session():
    tank = _full_tank()
    tank.set_level_l(120.0)
    start = T0 + timedelta(hours=2)
    tank.update(start, Reading(True, 2300.0))
    tank.update(start + timedelta(minutes=30), Reading(None, 0.0))  # relay unavailable
    tank.update(start + timedelta(minutes=31), Reading(True, 0.0))
    tank.update(start + timedelta(minutes=33), Reading(True, 0.0))
    assert tank.last_full == T0  # no consumption seen in the new session


def test_a_late_reading_accounts_for_no_interval():
    tank = _full_tank()
    tank.update(T0 + timedelta(hours=2), OFF)
    deficit = tank.energy_to_full_kwh
    tank.update(T0 + timedelta(hours=1), OFF)
    assert tank.energy_to_full_kwh == deficit


def test_no_capacity_no_estimate():
    tank = Tank(replace(CONFIG, set_point_c=8.5))
    tank.mark_full(T0)
    tank.set_level_l(100.0)
    assert (tank.level_l, tank.energy_to_full_kwh) == (None, None)


def test_a_non_finite_level_is_refused():
    tank = _full_tank()
    tank.set_level_l(math.nan)
    assert tank.level_l == 200.0


def test_a_restart_during_a_heating_holds_no_power():
    """HA stopped at 2300 W for 3 h: only the losses of the downtime are known, not hours of heating."""
    tank = _full_tank()
    tank.set_level_l(100.0)
    tank.update(T0 + timedelta(hours=1), Reading(True, 2300.0))
    restored = Tank(CONFIG, state_from_dict(state_to_dict(tank.state)))
    restored.resume()
    deficit = restored.energy_to_full_kwh
    restored.update(T0 + timedelta(hours=4), Reading(True, 0.0))
    assert restored.energy_to_full_kwh == pytest.approx(deficit + 0.095 * 3)
    restored.update(T0 + timedelta(hours=4, minutes=2), Reading(True, 0.0))
    assert restored.last_full == T0  # the heating session before the restart is forgotten


@pytest.mark.parametrize(
    "stored",
    [None, [], {"deficit_kwh": "nan"}, {"deficit_kwh": float("nan")}, {"last_full": "yesterday"}, {"reading": 3}],
)
def test_unreadable_stored_state_leaves_the_estimate_unknown(stored):
    state = state_from_dict(stored)
    assert state.deficit_kwh is None or math.isfinite(state.deficit_kwh)
    assert Tank(CONFIG, state).level_l is None


def test_a_replaced_meter_is_counted_from_its_first_reading():
    """The old hot water meter read 50 000 L, the new one 120 L: no 120 L "reset" draw, nor any heating."""
    tank = Tank(replace(CONFIG, has_energy_meter=True))
    tank.mark_full(T0)
    tank.update(T0, Reading(False, 0.0, energy_kwh=6985.4, hot_water_l=50_000.0))
    tank.forget_meters(energy=True, hot_water=True)
    tank.update(T0, Reading(False, 0.0, energy_kwh=12.0, hot_water_l=120.0))
    assert tank.level_l == pytest.approx(200.0)
    tank.update(T0, Reading(False, 0.0, energy_kwh=12.0, hot_water_l=130.0))
    assert tank.level_l == pytest.approx(190.0)


def test_a_kept_meter_is_not_forgotten():
    tank = _full_tank()
    tank.update(T0, Reading(False, 0.0, hot_water_l=100.0))
    tank.forget_meters(energy=True, hot_water=False)
    tank.update(T0, Reading(False, 0.0, hot_water_l=110.0))
    assert tank.level_l == pytest.approx(190.0)
