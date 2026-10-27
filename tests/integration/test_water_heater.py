"""Tests — the integration in Home Assistant: config flow, entities following the measures, end of heating,
restart, services."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from probatio import Invalid
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.smart_hass_water_heater.const import DOMAIN

RELAY, POWER, WATER = "switch.water_heater", "sensor.water_heater_power", "sensor.hot_water"
OPTIONS = {
    "relay_entity": RELAY,
    "power_entity": POWER,
    "hot_water_entity": WATER,
    "volume_l": 200.0,
    "set_point_c": 60.0,
    "losses_w": 95.0,
    "cold_water_c": 8.5,
}
TANK = {key: OPTIONS[key] for key in ("volume_l", "set_point_c", "losses_w", "cold_water_c")}
LEVEL, LAST_FULL = "sensor.tank_hot_water", "sensor.tank_last_full"
DEMAND = "binary_sensor.tank_heat_demand"


def _measures(hass: HomeAssistant, relay: str, power_w: float, water_l: float = 0.0) -> None:
    hass.states.async_set(RELAY, relay)
    hass.states.async_set(POWER, str(power_w), {"unit_of_measurement": "W"})
    hass.states.async_set(WATER, str(water_l), {"unit_of_measurement": "L"})


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Tank", options=OPTIONS, unique_id=RELAY)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_the_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Tank", **OPTIONS})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tank" and result["options"] == OPTIONS


async def test_one_entry_per_relay(hass: HomeAssistant) -> None:
    await _setup(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Again", **OPTIONS})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_level_unknown_until_the_first_end_of_heating(hass: HomeAssistant) -> None:
    _measures(hass, "on", 2300.0)
    await _setup(hass)
    assert hass.states.get(LEVEL).state == "unknown"
    # The thermostat cuts, relay still on: full once idle for 60 s, without any new reading
    _measures(hass, "on", 0.0)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(200.0)
    assert hass.states.get(LAST_FULL).state != "unknown"


async def test_a_draw_lowers_the_level(hass: HomeAssistant) -> None:
    _measures(hass, "off", 0.0, water_l=10.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    _measures(hass, "off", 0.0, water_l=50.0)
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(160.0, abs=0.1)


async def test_the_estimate_survives_a_restart(hass: HomeAssistant) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "set_level", {"config_entry_id": entry.entry_id, "level_l": 150.0}, blocking=True
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(150.0, abs=0.1)


async def test_unknown_entry_refused_by_the_services(hass: HomeAssistant) -> None:
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": "nope"}, blocking=True)


async def test_options_flow_reloads_with_the_new_tank(hass: HomeAssistant) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**TANK, "volume_l": 300.0})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(300.0, abs=0.1)  # a full tank stays full


async def test_learned_losses_published(hass: HomeAssistant) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    losses = hass.states.get("sensor.tank_learned_losses")
    assert losses.state == "unknown"  # no cycle without draw measured yet
    assert losses.attributes["unit_of_measurement"] == "W"
    assert (losses.attributes["configured_losses_w"], losses.attributes["losses_in_use_w"]) == (95.0, 95.0)
    assert (losses.attributes["learning_enabled"], losses.attributes["loss_cycles"]) == (False, 0)
    assert losses.attributes["last_loss_cycle_w"] is None
    assert er.async_get(hass).async_get("sensor.tank_learned_losses").entity_category is er.EntityCategory.DIAGNOSTIC
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**TANK, "learn_losses": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {**OPTIONS, "learn_losses": True}
    assert hass.states.get("sensor.tank_learned_losses").attributes["learning_enabled"] is True


async def test_learned_losses_counted_once_enabled(hass: HomeAssistant, hass_storage: dict) -> None:
    """Restored with two cycles learned (90 W): the level's losses_w, read by a planner, follows the option."""
    now = dt_util.utcnow().isoformat()
    hass_storage[f"{DOMAIN}.tank"] = {
        "version": 1,
        "key": f"{DOMAIN}.tank",
        "data": {"deficit_kwh": 0.0, "last_full": now, "updated_at": now, "loss_cycles": [[12.0, 1.08], [24.0, 2.16]]},
    }
    _measures(hass, "off", 0.0)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Tank", options={**OPTIONS, "learn_losses": True}, unique_id=RELAY, entry_id="tank"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    losses = hass.states.get("sensor.tank_learned_losses")
    assert float(losses.state) == pytest.approx(90.0)
    assert (losses.attributes["losses_in_use_w"], losses.attributes["loss_cycles"]) == (90.0, 2)
    assert hass.states.get("sensor.tank_hot_water_level").attributes["losses_w"] == 90.0


async def test_set_point_not_above_cold_water_refused(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Tank", **OPTIONS, "set_point_c": 30.0, "cold_water_c": 30.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "set_point_not_above_cold_water"}
    # The form keeps what was entered: corrected, the entry is created
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Tank", **OPTIONS})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["options"] == OPTIONS


async def test_options_refuse_then_accept_the_corrected_set_point(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TANK, "set_point_c": 30.0, "cold_water_c": 30.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "set_point_not_above_cold_water"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**TANK, "set_point_c": 55.0})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**OPTIONS, "set_point_c": 55.0}  # the sources kept


async def test_time_entities_are_diagnostic(hass: HomeAssistant) -> None:
    await _setup(hass)
    registry = er.async_get(hass)
    categories = {
        entity_id: registry.async_get(entity_id).entity_category
        for entity_id in (LEVEL, "sensor.tank_time_since_full", LAST_FULL)
    }
    assert categories == {
        LEVEL: None,
        "sensor.tank_time_since_full": er.EntityCategory.DIAGNOSTIC,
        LAST_FULL: er.EntityCategory.DIAGNOSTIC,
    }


async def test_units_converted_and_unavailable_ignored(hass: HomeAssistant) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    hass.states.async_set(WATER, "0.010", {"unit_of_measurement": "m³"})  # 10 L drawn since the 0 L at setup
    await hass.async_block_till_done()
    hass.states.async_set(WATER, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(WATER, "nan", {"unit_of_measurement": "m³"})
    await hass.async_block_till_done()
    hass.states.async_set(WATER, "0.030", {"unit_of_measurement": "m³"})  # 20 L more, the gaps counted nothing
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(170.0, abs=0.1)


async def test_power_in_kilowatts_heats(hass: HomeAssistant) -> None:
    _measures(hass, "on", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "set_level", {"config_entry_id": entry.entry_id, "level_l": 100.0}, blocking=True
    )
    hass.states.async_set(POWER, "2.3", {"unit_of_measurement": "kW"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))  # the periodic refresh
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) > 100.0


async def test_set_level_refuses_a_non_finite_level(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    with pytest.raises(Invalid):
        await hass.services.async_call(
            DOMAIN, "set_level", {"config_entry_id": entry.entry_id, "level_l": "nan"}, blocking=True
        )


async def test_diagnostics_show_the_estimate_and_the_sources(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    _measures(hass, "off", 0.0, water_l=10.0)
    entry = await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "set_level", {"config_entry_id": entry.entry_id, "level_l": 150.0}, blocking=True
    )
    hass.states.async_remove(POWER)  # a source gone: shown as missing
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "diagnostics", {})
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["options"] == OPTIONS
    assert diagnostics["estimate"]["level_l"] == pytest.approx(150.0, abs=0.1)
    assert diagnostics["state"]["deficit_kwh"] == pytest.approx(diagnostics["estimate"]["energy_to_full_kwh"])
    assert diagnostics["sources"]["relay_entity"]["state"] == "off"
    assert diagnostics["sources"]["hot_water_entity"]["unit"] == "L"
    assert diagnostics["sources"]["power_entity"] is None
    assert "energy_entity" not in diagnostics["sources"]  # not configured
    assert diagnostics["estimate"]["heat_demand"] is True  # 50 L below full: estimated on
    assert diagnostics["estimate"]["heat_demand_measured"] is False


async def _later(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delay: timedelta) -> None:
    """Home Assistant's timers read the clock when they fire: move it, then fire them."""
    freezer.tick(delay)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_the_level_waits_15_minutes_before_an_unavailable_power(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    hass.states.async_set(POWER, "unavailable")  # e.g. the smart plug rebooting
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=14))
    assert hass.states.get(LEVEL).state != "unavailable"
    await _later(hass, freezer, timedelta(minutes=1, seconds=1))
    assert hass.states.get(LEVEL).state == "unavailable"
    assert hass.states.get("sensor.tank_energy_to_full").state == "unavailable"
    assert hass.states.get(LAST_FULL).state != "unavailable"  # a known fact
    assert f"Tank: the heater power {POWER} is unavailable since" in caplog.text
    assert "(state unavailable): the hot water level is unavailable until it comes back" in caplog.text
    await _later(hass, freezer, timedelta(minutes=30))
    assert caplog.text.count("is unavailable since") == 1  # logged once, not at every refresh
    hass.states.async_set(POWER, "0.0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    # Back, with the losses of the 46 min outage (95 W: 0.073 kWh, about 1.2 L at 60 °C)
    assert float(hass.states.get(LEVEL).state) == pytest.approx(198.8, abs=0.1)
    assert f"Tank: the heater power {POWER} is available again" in caplog.text


async def test_heat_demand_seen_when_the_relay_on_does_not_heat(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    assert hass.states.get(DEMAND).state == "unknown"
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    demand = hass.states.get(DEMAND)
    assert (demand.state, demand.attributes["certainty"]) == ("off", "measured")
    assert demand.attributes["draw_threshold_l"] == pytest.approx(1.0)
    await hass.services.async_call(
        DOMAIN, "set_level", {"config_entry_id": entry.entry_id, "level_l": 170.0}, blocking=True
    )
    demand = hass.states.get(DEMAND)
    assert (demand.state, demand.attributes["certainty"]) == ("on", "estimated")  # 30 L below full: past 5 °C
    # The relay on without heating, no new reading: seen after 60 s, the threshold raised above the deficit
    _measures(hass, "on", 0.0)
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(seconds=61))
    demand = hass.states.get(DEMAND)
    assert (demand.state, demand.attributes["certainty"]) == ("off", "measured")
    deficit = float(hass.states.get("sensor.tank_energy_to_full").state)
    assert demand.attributes["loss_threshold_kwh"] == pytest.approx(deficit + 0.05, abs=0.001)
    assert demand.attributes["energy_before_demand_kwh"] == pytest.approx(0.05, abs=0.001)


async def test_the_level_gives_a_planner_the_tank_and_its_sources(hass: HomeAssistant) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "set_level", {"config_entry_id": entry.entry_id, "level_l": 170.0}, blocking=True
    )
    attributes = hass.states.get("sensor.tank_hot_water_level").attributes
    assert attributes["volume_l"] == 200.0
    assert attributes["capacity_kwh"] == pytest.approx(200 * 0.001162 * (60 - 8.5), abs=0.001)
    assert (attributes["losses_w"], attributes["set_point_c"], attributes["cold_water_c"]) == (95.0, 60.0, 8.5)
    assert attributes["energy_to_full_kwh"] == pytest.approx(30 * 0.001162 * (60 - 8.5), abs=0.001)
    assert attributes["hours_since_full"] is None  # never full yet
    assert (attributes["heat_demand"], attributes["heat_demand_measured"]) == (True, False)
    assert (attributes["energy_before_demand_kwh"], attributes["liters_before_demand_l"]) == (0.0, 0.0)
    # the thresholds a planner applies after the next full tank: 5 °C over 200 L, 1 L
    assert attributes["loss_threshold_kwh"] == pytest.approx(200 * 0.001162 * 5, abs=0.001)
    assert attributes["draw_threshold_l"] == 1.0
    assert (attributes["relay_entity"], attributes["power_entity"]) == (RELAY, POWER)
    assert (attributes["hot_water_entity"], attributes["energy_entity"]) == (WATER, None)
    assert "volume_l" not in hass.states.get(LEVEL).attributes  # on the percent entity only


async def test_heat_demand_unavailable_with_the_level(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    hass.states.async_set(RELAY, "unavailable")
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=16))
    assert hass.states.get(DEMAND).state == "unavailable"


async def test_a_short_outage_is_not_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    _measures(hass, "off", 0.0)
    await _setup(hass)
    hass.states.async_set(RELAY, "unknown")
    await hass.async_block_till_done()
    hass.states.async_set(RELAY, "off")
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=16))
    assert "unavailable since" not in caplog.text


async def test_an_unavailable_hot_water_meter_is_only_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    hass.states.async_set(WATER, "12", {"unit_of_measurement": "gal"})
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=16))
    assert hass.states.get(LEVEL).state != "unavailable"
    assert "(unit 'gal' is not supported): the draws are counted when it comes back" in caplog.text


async def test_a_removed_relay_makes_the_level_unavailable(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    hass.states.async_remove(RELAY)
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=16))
    assert hass.states.get(LEVEL).state == "unavailable"
    assert f"the relay {RELAY} is unavailable since" in caplog.text
    assert "(the entity does not exist)" in caplog.text
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"missing_relay_entity_{entry.entry_id}")
    assert issue is not None and issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders == {"name": "Tank", "entity": RELAY}
    # Reconfigured to the new relay: the issue goes with the reload
    hass.states.async_set("switch.new_relay", "off")
    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"relay_entity": "switch.new_relay", "power_entity": POWER, "hot_water_entity": WATER}
    )
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"missing_relay_entity_{entry.entry_id}") is None
    assert hass.states.get(LEVEL).state != "unavailable"


async def test_a_missing_meter_back_clears_its_issue(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    hass.states.async_remove(WATER)
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=16))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"missing_hot_water_entity_{entry.entry_id}")
    assert issue is not None and issue.severity is ir.IssueSeverity.WARNING
    hass.states.async_set(WATER, "5", {"unit_of_measurement": "L"})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"missing_hot_water_entity_{entry.entry_id}") is None


async def test_an_unavailable_entity_raises_no_issue(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Unavailable is the device's state: it comes back by itself, the log is enough."""
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    hass.states.async_set(POWER, "unavailable")
    await hass.async_block_till_done()
    await _later(hass, freezer, timedelta(minutes=16))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"missing_power_entity_{entry.entry_id}") is None


async def test_reconfigure_replaces_the_hot_water_meter(hass: HomeAssistant) -> None:
    """A new meter's value is no draw: counted from its first reading. The tank's parameters and level stay."""
    _measures(hass, "off", 0.0, water_l=50_000.0)
    hass.states.async_set("sensor.new_meter", "120", {"unit_of_measurement": "L"})
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    sources = {"relay_entity": RELAY, "power_entity": POWER, "hot_water_entity": "sensor.new_meter"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], sources)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.options == {**TANK, **sources}
    assert float(hass.states.get(LEVEL).state) == pytest.approx(200.0, abs=0.1)
    hass.states.async_set("sensor.new_meter", "130", {"unit_of_measurement": "L"})
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(190.0, abs=0.1)


async def test_reconfigure_to_a_new_relay_and_without_a_meter(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"relay_entity": "switch.other", "power_entity": POWER}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "switch.other"
    assert "hot_water_entity" not in entry.options


async def test_reconfigure_refuses_the_relay_of_another_water_heater(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    MockConfigEntry(domain=DOMAIN, title="Other", options=OPTIONS, unique_id="switch.other").add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"relay_entity": "switch.other", "power_entity": POWER}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.unique_id == RELAY


async def test_a_state_stored_before_the_sources_keeps_its_meters(hass: HomeAssistant, hass_storage: dict) -> None:
    """Stored by a version that did not record the source entities: the meters are not taken for replaced."""
    now = dt_util.utcnow().isoformat()
    hass_storage[f"{DOMAIN}.tank"] = {
        "version": 1,
        "key": f"{DOMAIN}.tank",
        "data": {"deficit_kwh": 0.0, "last_full": now, "updated_at": now, "hot_water_meter_l": 100.0},
    }
    _measures(hass, "off", 0.0, water_l=110.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Tank", options=OPTIONS, unique_id=RELAY, entry_id="tank")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(LEVEL).state) == pytest.approx(190.0, abs=0.1)


async def test_deleting_the_water_heater_removes_its_stored_estimate(hass: HomeAssistant, hass_storage: dict) -> None:
    _measures(hass, "off", 0.0)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "mark_full", {"config_entry_id": entry.entry_id}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)  # saves the estimate
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
