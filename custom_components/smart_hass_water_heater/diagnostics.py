"""Diagnostics: the options, the estimate, the model's stored state and what the source entities show now — what is
needed to understand an estimate. Nothing to redact: entity ids and measures only."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import WaterHeaterConfigEntry
from .model import state_to_dict


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: WaterHeaterConfigEntry) -> dict[str, Any]:
    tracker = entry.runtime_data
    tank = tracker.tank
    last_full = tank.last_full
    return {
        "options": dict(entry.options),
        "estimate": {
            "level_l": tank.level_l,
            "level_pct": tank.level_pct,
            "energy_to_full_kwh": tank.energy_to_full_kwh,
            "capacity_kwh": tank.config.capacity_kwh,
            "hours_since_full": tank.hours_since_full,
            "last_full": None if last_full is None else last_full.isoformat(),
            "heat_demand": tank.heat_demand,
            "heat_demand_measured": tank.heat_demand_measured,
            "loss_threshold_kwh": tank.loss_threshold_kwh,
            "draw_threshold_l": tank.draw_threshold_l,
        },
        "state": state_to_dict(tank.state),
        "sources": tracker.source_states(),
    }
