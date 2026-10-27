"""Configuration from the UI: the entities read (changed later by reconfiguring) and the tank's parameters (changed
later in the options)."""

from typing import Any

import probatio
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COLD_WATER_C,
    CONF_ENERGY_ENTITY,
    CONF_HOT_WATER_ENTITY,
    CONF_LEARN_LOSSES,
    CONF_LOSSES_W,
    CONF_POWER_ENTITY,
    CONF_RELAY_ENTITY,
    CONF_SET_POINT_C,
    CONF_VOLUME_L,
    DEFAULT_COLD_WATER_C,
    DEFAULT_LOSSES_W,
    DEFAULT_SET_POINT_C,
    DEFAULT_VOLUME_L,
    DOMAIN,
    SOURCE_KEYS,
)


def _number(minimum: float, maximum: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX
        )
    )


def _sources_schema(defaults: dict[str, Any]) -> probatio.Schema:
    def optional_entity(key: str) -> probatio.Optional:
        value = defaults.get(key)
        return probatio.Optional(key, description={"suggested_value": value} if value else None)

    return probatio.Schema(
        {
            probatio.Required(
                CONF_RELAY_ENTITY, default=defaults.get(CONF_RELAY_ENTITY, probatio.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "input_boolean"])),
            probatio.Required(
                CONF_POWER_ENTITY, default=defaults.get(CONF_POWER_ENTITY, probatio.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power")),
            optional_entity(CONF_ENERGY_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
            optional_entity(CONF_HOT_WATER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "counter", "input_number"])
            ),
        }
    )


def _tank_schema(defaults: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_VOLUME_L, default=defaults.get(CONF_VOLUME_L, DEFAULT_VOLUME_L)): _number(
                10, 1000, 1, "L"
            ),
            probatio.Required(CONF_SET_POINT_C, default=defaults.get(CONF_SET_POINT_C, DEFAULT_SET_POINT_C)): _number(
                30, 90, 0.5, "°C"
            ),
            probatio.Required(CONF_LOSSES_W, default=defaults.get(CONF_LOSSES_W, DEFAULT_LOSSES_W)): _number(
                0, 500, 1, "W"
            ),
            probatio.Required(
                CONF_COLD_WATER_C, default=defaults.get(CONF_COLD_WATER_C, DEFAULT_COLD_WATER_C)
            ): _number(0, 30, 0.5, "°C"),
            # Optional: an entry created before it keeps its options as they were (off)
            probatio.Optional(
                CONF_LEARN_LOSSES, description={"suggested_value": defaults.get(CONF_LEARN_LOSSES, False)}
            ): selector.BooleanSelector(),
        }
    )


def _errors(options: dict[str, Any]) -> dict[str, str]:
    """A set point not above the cold water leaves no hot water to estimate."""
    set_point = float(options.get(CONF_SET_POINT_C, DEFAULT_SET_POINT_C))
    cold_water = float(options.get(CONF_COLD_WATER_C, DEFAULT_COLD_WATER_C))
    return {"base": "set_point_not_above_cold_water"} if set_point <= cold_water else {}


class WaterHeaterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = _errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            options = dict(user_input)
            name = options.pop(CONF_NAME)
            await self.async_set_unique_id(options[CONF_RELAY_ENTITY])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=name, data={}, options=options)
        defaults = user_input or {}
        schema = (
            probatio.Schema({probatio.Required(CONF_NAME): selector.TextSelector()})
            .extend(_sources_schema(defaults).schema)
            .extend(_tank_schema(defaults).schema)
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Other source entities (one replaced or removed); the tank's parameters and the estimate stay."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            relay = user_input[CONF_RELAY_ENTITY]
            if any(other.unique_id == relay for other in self._async_current_entries() if other is not entry):
                return self.async_abort(reason="already_configured")
            tank = {key: value for key, value in entry.options.items() if key not in SOURCE_KEYS}
            return self.async_update_reload_and_abort(entry, unique_id=relay, options={**tank, **user_input})
        return self.async_show_form(step_id="reconfigure", data_schema=_sources_schema(dict(entry.options)))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WaterHeaterOptionsFlow:
        return WaterHeaterOptionsFlow()


class WaterHeaterOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = _errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            sources = {key: value for key, value in self.config_entry.options.items() if key in SOURCE_KEYS}
            return self.async_create_entry(data={**sources, **user_input})
        defaults = user_input if user_input is not None else dict(self.config_entry.options)
        return self.async_show_form(step_id="init", data_schema=_tank_schema(defaults), errors=errors)
