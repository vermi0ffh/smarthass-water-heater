"""The thermostat's heat demand: would the heater heat if the relay were on."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WaterHeaterConfigEntry
from .const import DOMAIN
from .tracker import WaterHeaterTracker

# No polling: the tracker pushes every change
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: WaterHeaterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HeatDemandSensor(entry.runtime_data)])


class HeatDemandSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "heat_demand"
    # They move with every update: their history would only fill the database
    _unrecorded_attributes = frozenset({"energy_before_demand_kwh", "liters_before_demand_l"})

    def __init__(self, tracker: WaterHeaterTracker) -> None:
        self._tracker = tracker
        entry = tracker.entry
        self._attr_unique_id = f"{entry.entry_id}_heat_demand"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)

    @property
    def available(self) -> bool:
        """As the level: a heating could go unseen without the relay or the power."""
        return self._tracker.estimate_available

    @property
    def is_on(self) -> bool | None:
        return self._tracker.tank.heat_demand

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tank = self._tracker.tank
        return {
            "certainty": "measured" if tank.heat_demand_measured else "estimated",
            "loss_threshold_kwh": round(tank.loss_threshold_kwh, 3),
            "draw_threshold_l": round(tank.draw_threshold_l, 1),
            "energy_before_demand_kwh": _round(tank.energy_before_demand_kwh, 3),
            "liters_before_demand_l": _round(tank.liters_before_demand_l, 1),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._tracker.add_listener(self.async_write_ha_state))


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
