"""Smart Hass - Water Heater: the hot water left in an electric water heater, estimated from Home Assistant's
measures. Reads only: it never switches the heater (rule 3)."""

import math

import probatio
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_CONFIG_ENTRY_ID, ATTR_LEVEL_L, DOMAIN, SERVICE_MARK_FULL, SERVICE_SET_LEVEL
from .model.tank import STATE_VERSION
from .tracker import WaterHeaterTracker, store_key

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

type WaterHeaterConfigEntry = ConfigEntry[WaterHeaterTracker]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _finite(value: float) -> float:
    """ "nan" and "inf" coerce to floats: refused, they would become the persisted estimate."""
    if not math.isfinite(value):
        raise probatio.Invalid("not a finite number")
    return value


_MARK_FULL_SCHEMA = probatio.Schema({probatio.Required(ATTR_CONFIG_ENTRY_ID): cv.string})
_SET_LEVEL_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_LEVEL_L): probatio.All(probatio.Coerce(float), _finite, probatio.Range(min=0)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The services, registered once for every water heater."""

    def tracker_of(call: ServiceCall) -> WaterHeaterTracker:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entry",
                translation_placeholders={"entry": call.data[ATTR_CONFIG_ENTRY_ID]},
            )
        tracker: WaterHeaterTracker = entry.runtime_data
        return tracker

    async def mark_full(call: ServiceCall) -> None:
        tracker_of(call).mark_full()

    async def set_level(call: ServiceCall) -> None:
        tracker_of(call).set_level_l(call.data[ATTR_LEVEL_L])

    hass.services.async_register(DOMAIN, SERVICE_MARK_FULL, mark_full, schema=_MARK_FULL_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_LEVEL, set_level, schema=_SET_LEVEL_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WaterHeaterConfigEntry) -> bool:
    tracker = WaterHeaterTracker(hass, entry)
    await tracker.async_start()
    entry.runtime_data = tracker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WaterHeaterConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: WaterHeaterConfigEntry) -> None:
    """The water heater deleted: its stored estimate goes with it."""
    await Store[dict](hass, STATE_VERSION, store_key(entry.entry_id)).async_remove()
