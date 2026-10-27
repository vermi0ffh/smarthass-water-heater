"""The estimate as sensors: level (liters, percent), energy to full, hours since and time of the last full tank, the
learned standing losses."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WaterHeaterConfigEntry
from .const import DOMAIN
from .tracker import WaterHeaterTracker

# No polling: the tracker pushes every change
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WaterHeaterSensorDescription(SensorEntityDescription):
    value: Callable[[WaterHeaterTracker], float | datetime | None]
    # Unavailable while the relay or the power is (the time of the last full tank stays a known fact)
    needs_sources: bool = False
    attributes: Callable[[WaterHeaterTracker], dict[str, Any]] | None = None


SENSORS = (
    WaterHeaterSensorDescription(
        key="level_l",
        needs_sources=True,
        translation_key="level_l",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda t: t.tank.level_l,
    ),
    WaterHeaterSensorDescription(
        key="level_pct",
        needs_sources=True,
        # Everything a planner needs in one entity (contract, doc/agents/architecture.md)
        attributes=lambda t: t.planner_attributes(),
        translation_key="level_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda t: t.tank.level_pct,
    ),
    WaterHeaterSensorDescription(
        key="energy_to_full",
        needs_sources=True,
        translation_key="energy_to_full",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda t: t.tank.energy_to_full_kwh,
    ),
    WaterHeaterSensorDescription(
        key="hours_since_full",
        translation_key="hours_since_full",
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda t: t.tank.hours_since_full,
    ),
    WaterHeaterSensorDescription(
        key="learned_losses",
        translation_key="learned_losses",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attributes=lambda t: t.losses_attributes(),
        value=lambda t: t.tank.learned_losses_w,
    ),
    WaterHeaterSensorDescription(
        key="last_full",
        translation_key="last_full",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda t: t.tank.last_full,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: WaterHeaterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(WaterHeaterSensor(entry.runtime_data, description) for description in SENSORS)


class WaterHeaterSensor(SensorEntity):
    entity_description: WaterHeaterSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False
    # The level's attributes that move with every update: their history would only fill the database (Home Assistant
    # reads this per class)
    _unrecorded_attributes = frozenset(
        {"hours_since_full", "energy_to_full_kwh", "energy_before_demand_kwh", "liters_before_demand_l"}
    )

    def __init__(self, tracker: WaterHeaterTracker, description: WaterHeaterSensorDescription) -> None:
        self._tracker = tracker
        self.entity_description = description
        entry = tracker.entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attributes = self.entity_description.attributes
        return None if attributes is None else attributes(self._tracker)

    @property
    def available(self) -> bool:
        return not self.entity_description.needs_sources or self._tracker.estimate_available

    @property
    def native_value(self) -> float | datetime | None:
        return self.entity_description.value(self._tracker)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._tracker.add_listener(self.async_write_ha_state))
