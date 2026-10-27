"""The Home Assistant glue (rule 2): reads the configured entities, feeds the tank model, persists its state and
tells the entities when the estimate changed."""

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, UnitOfEnergy, UnitOfPower, UnitOfVolume
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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
from .model import Reading, Tank, TankConfig, state_from_dict, state_to_dict
from .model.tank import STATE_VERSION

_LOGGER = logging.getLogger(__name__)

# The losses accrue between readings: the entities are refreshed at least this often
REFRESH_INTERVAL = timedelta(minutes=5)
# A source unavailable this long is logged; the relay or the power that long makes the estimate unavailable (a
# heating could go unseen). Long enough for a restart of Home Assistant or an OTA update of the device.
UNAVAILABLE_AFTER = timedelta(minutes=15)
# A burst of readings (power every few seconds) is saved once
SAVE_DELAY_S = 30

_POWER_TO_W: dict[str, float] = {UnitOfPower.WATT: 1.0, UnitOfPower.KILO_WATT: 1000.0}
_ENERGY_TO_KWH: dict[str, float] = {UnitOfEnergy.WATT_HOUR: 0.001, UnitOfEnergy.KILO_WATT_HOUR: 1.0}
_VOLUME_TO_L: dict[str, float] = {UnitOfVolume.LITERS: 1.0, UnitOfVolume.CUBIC_METERS: 1000.0}
_REQUIRED_KEYS = (CONF_RELAY_ENTITY, CONF_POWER_ENTITY)
_FACTORS: dict[str, dict[str, float]] = {
    CONF_POWER_ENTITY: _POWER_TO_W,
    CONF_ENERGY_ENTITY: _ENERGY_TO_KWH,
    CONF_HOT_WATER_ENTITY: _VOLUME_TO_L,
}
# What the log says of each source, and what its absence costs
_SOURCE_LABELS = {
    CONF_RELAY_ENTITY: "relay",
    CONF_POWER_ENTITY: "heater power",
    CONF_ENERGY_ENTITY: "heater energy meter",
    CONF_HOT_WATER_ENTITY: "hot water meter",
}
_CONSEQUENCES = {
    CONF_RELAY_ENTITY: "the hot water level is unavailable until it comes back",
    CONF_POWER_ENTITY: "the hot water level is unavailable until it comes back",
    CONF_ENERGY_ENTITY: "the heating is counted when it comes back",
    CONF_HOT_WATER_ENTITY: "the draws are counted when it comes back",
}


def store_key(entry_id: str) -> str:
    """Where a water heater's estimate is stored (.storage/)."""
    return f"{DOMAIN}.{entry_id}"


def tank_config(options: dict[str, Any]) -> TankConfig:
    return TankConfig(
        volume_l=float(options.get(CONF_VOLUME_L, DEFAULT_VOLUME_L)),
        set_point_c=float(options.get(CONF_SET_POINT_C, DEFAULT_SET_POINT_C)),
        losses_w=float(options.get(CONF_LOSSES_W, DEFAULT_LOSSES_W)),
        cold_water_c=float(options.get(CONF_COLD_WATER_C, DEFAULT_COLD_WATER_C)),
        has_energy_meter=bool(options.get(CONF_ENERGY_ENTITY)),
        learn_losses=bool(options.get(CONF_LEARN_LOSSES, False)),
    )


class WaterHeaterTracker:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._options = dict(entry.options)
        self._store: Store[dict[str, Any]] = Store(hass, STATE_VERSION, store_key(entry.entry_id))
        self.tank = Tank(tank_config(self._options))
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._check: CALLBACK_TYPE | None = None
        # Each source unavailable since when (first seen so), and those already logged as unavailable
        self._unavailable_since: dict[str, datetime] = {}
        self._reported: set[str] = set()
        self._source_check: CALLBACK_TYPE | None = None

    # --- Lifecycle ---

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        if stored is not None:
            self.tank = Tank(tank_config(self._options), state_from_dict(stored))
            self.tank.resume()
            # A meter replaced (reconfigured): its value is no increase of the previous one. Before the sources were
            # stored, nothing is known: the meters are kept, as before.
            previous = stored.get("sources")
            if isinstance(previous, dict):
                self.tank.forget_meters(
                    energy=previous.get(CONF_ENERGY_ENTITY) != self._options.get(CONF_ENERGY_ENTITY),
                    hot_water=previous.get(CONF_HOT_WATER_ENTITY) != self._options.get(CONF_HOT_WATER_ENTITY),
                )
        self._update(dt_util.utcnow())
        self._unsubscribe.append(async_track_state_change_event(self.hass, self._entities(), self._on_state_change))
        self._unsubscribe.append(async_track_time_interval(self.hass, self._on_refresh, REFRESH_INTERVAL))

    async def async_stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        self._cancel_check()
        if self._source_check is not None:
            self._source_check()
            self._source_check = None
        for key in SOURCE_KEYS:  # raised again after 15 min if still true (e.g. after reconfiguring)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
        await self._store.async_save(self._stored())

    # --- For the entities and the services ---

    @property
    def estimate_available(self) -> bool:
        """False while the relay or the power has been unavailable for UNAVAILABLE_AFTER."""
        return not self._reported.intersection(_REQUIRED_KEYS)

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def mark_full(self) -> None:
        now = dt_util.utcnow()
        self.tank.update(now, self._reading())  # the interval up to now is accounted before the reset
        self.tank.mark_full(now)
        self._changed()

    def set_level_l(self, level_l: float) -> None:
        self.tank.update(dt_util.utcnow(), self._reading())
        self.tank.set_level_l(level_l)
        self._changed()

    def planner_attributes(self) -> dict[str, Any]:
        """The tank, its estimate and its heat demand for a planner reading one entity: what it would otherwise
        configure again (volume, capacity, losses, the source entities), the state it plans from and the heat
        demand's thresholds after the next full tank."""
        tank, cfg = self.tank, self.tank.config
        return {
            "volume_l": cfg.volume_l,
            "capacity_kwh": _round(cfg.capacity_kwh, 3),
            "losses_w": round(tank.losses_in_use_w, 1),
            "set_point_c": cfg.set_point_c,
            "cold_water_c": cfg.cold_water_c,
            "energy_to_full_kwh": _round(tank.energy_to_full_kwh, 3),
            "hours_since_full": _round(tank.hours_since_full, 2),
            "heat_demand": tank.heat_demand,
            "heat_demand_measured": tank.heat_demand_measured,
            "loss_threshold_kwh": round(tank.loss_threshold_kwh, 3),
            "draw_threshold_l": round(tank.draw_threshold_l, 1),
            "energy_before_demand_kwh": _round(tank.energy_before_demand_kwh, 3),
            "liters_before_demand_l": _round(tank.liters_before_demand_l, 1),
            **{key: self._options.get(key) for key in SOURCE_KEYS},
        }

    def losses_attributes(self) -> dict[str, Any]:
        """Where the learned losses come from, and whether the estimate counts them."""
        tank = self.tank
        return {
            "configured_losses_w": tank.config.losses_w,
            "losses_in_use_w": round(tank.losses_in_use_w, 1),
            "learning_enabled": tank.config.learn_losses,
            "loss_cycles": tank.loss_cycle_count,
            "last_loss_cycle_w": _round(tank.last_loss_cycle_w, 1),
        }

    def source_states(self) -> dict[str, dict[str, Any] | None]:
        """What each configured source entity shows now, as Home Assistant has it (diagnostics); None = missing."""
        sources: dict[str, dict[str, Any] | None] = {}
        for key in SOURCE_KEYS:
            entity_id = self._options.get(key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            sources[key] = (
                None
                if state is None
                else {
                    "entity_id": entity_id,
                    "state": state.state,
                    "unit": state.attributes.get("unit_of_measurement"),
                    "last_changed": state.last_changed.isoformat(),
                }
            )
        return sources

    # --- Events ---

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        self._update(event.time_fired)

    @callback
    def _on_refresh(self, now: datetime) -> None:
        self._update(now)

    @callback
    def _on_check(self, now: datetime) -> None:
        self._check = None
        self._update(now)

    @callback
    def _on_source_check(self, now: datetime) -> None:
        self._source_check = None
        self._update(now)

    def _update(self, at: datetime) -> None:
        self.tank.update(at, self._reading())
        self._schedule_check()
        self._follow_sources(at)
        self._changed()

    def _follow_sources(self, at: datetime) -> None:
        """Logs a source unavailable for UNAVAILABLE_AFTER with the cause, once, and once when it is back."""
        for key in SOURCE_KEYS:
            entity_id = self._options.get(key)
            if not entity_id:
                continue
            cause = self._unavailable_cause(entity_id, _FACTORS.get(key))
            if cause is None:
                self._unavailable_since.pop(key, None)
                if key in self._reported:
                    self._reported.discard(key)
                    ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
                    _LOGGER.info("%s: the %s %s is available again", self.entry.title, _SOURCE_LABELS[key], entity_id)
                continue
            since = self._unavailable_since.setdefault(key, at)
            if key not in self._reported and at - since >= UNAVAILABLE_AFTER:
                self._reported.add(key)
                _LOGGER.info(
                    "%s: the %s %s is unavailable since %s (%s): %s",
                    self.entry.title,
                    _SOURCE_LABELS[key],
                    entity_id,
                    dt_util.as_local(since).isoformat(timespec="seconds"),
                    cause,
                    _CONSEQUENCES[key],
                )
                if self.hass.states.get(entity_id) is None:
                    self._raise_missing(key, entity_id)
        self._schedule_source_check()

    def _raise_missing(self, key: str, entity_id: str) -> None:
        """An entity that no longer exists (renamed, its device removed) will not come back by itself: a repair."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(key),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR if key in _REQUIRED_KEYS else ir.IssueSeverity.WARNING,
            translation_key=f"missing_{key}",
            translation_placeholders={"name": self.entry.title, "entity": entity_id},
        )

    def _issue_id(self, key: str) -> str:
        return f"missing_{key}_{self.entry.entry_id}"

    def _schedule_source_check(self) -> None:
        """No event may come while a source stays unavailable: an update when the first one is due to be logged."""
        if self._source_check is not None:
            self._source_check()
            self._source_check = None
        pending = [since for key, since in self._unavailable_since.items() if key not in self._reported]
        if pending:
            self._source_check = async_track_point_in_utc_time(
                self.hass, self._on_source_check, min(pending) + UNAVAILABLE_AFTER
            )

    def _schedule_check(self) -> None:
        """No reading comes while the power stays at 0: an update at the end of the idle delay sees the full tank, or
        that the relay on does not heat."""
        self._cancel_check()
        due = [at for at in (self.tank.end_of_heating_due_at, self.tank.heat_demand_due_at) if at is not None]
        if due:
            self._check = async_track_point_in_utc_time(self.hass, self._on_check, min(due))

    def _cancel_check(self) -> None:
        if self._check is not None:
            self._check()
            self._check = None

    def _changed(self) -> None:
        self._store.async_delay_save(self._stored, SAVE_DELAY_S)
        for listener in list(self._listeners):
            listener()

    def _stored(self) -> dict[str, Any]:
        """The model's state, and the source entities it was measured with (a replaced meter is forgotten)."""
        return {**state_to_dict(self.tank.state), "sources": {key: self._options.get(key) for key in SOURCE_KEYS}}

    # --- Reading the entities (their Home Assistant semantics stay here, rule 2) ---

    def _entities(self) -> list[str]:
        return [entity for key in SOURCE_KEYS if (entity := self._options.get(key))]

    def _reading(self) -> Reading:
        relay = self._state(self._options.get(CONF_RELAY_ENTITY))
        return Reading(
            relay_on=None if relay is None else relay.state == STATE_ON,
            power_w=self._number(self._options.get(CONF_POWER_ENTITY), _POWER_TO_W),
            energy_kwh=self._number(self._options.get(CONF_ENERGY_ENTITY), _ENERGY_TO_KWH),
            hot_water_l=self._number(self._options.get(CONF_HOT_WATER_ENTITY), _VOLUME_TO_L),
        )

    def _state(self, entity_id: str | None) -> State | None:
        """The entity's state, None when not configured, unavailable or unknown."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        return state

    def _number(self, entity_id: str | None, factors: dict[str, float]) -> float | None:
        """The numeric state in the model's unit; None when unavailable, not a number or in an unknown unit."""
        return None if not entity_id else _convert(self.hass.states.get(entity_id), factors)[0]

    def _unavailable_cause(self, entity_id: str, factors: dict[str, float] | None) -> str | None:
        """Why the source gives no reading, None when it gives one (factors: None for the relay)."""
        state = self.hass.states.get(entity_id)
        if factors is None:
            if state is None:
                return "the entity does not exist"
            return f"state {state.state}" if state.state in ("unavailable", "unknown") else None
        return _convert(state, factors)[1]


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _convert(state: State | None, factors: dict[str, float]) -> tuple[float | None, str | None]:
    """The numeric state in the model's unit, or None and why."""
    if state is None:
        return None, "the entity does not exist"
    if state.state in ("unavailable", "unknown"):
        return None, f"state {state.state}"
    try:
        value = float(state.state)
    except ValueError:
        return None, f"state {state.state!r} is not a number"
    if not math.isfinite(value):  # "nan" / "inf" parse as floats and would poison the deficit for good
        return None, f"state {state.state!r} is not a number"
    unit = state.attributes.get("unit_of_measurement")
    factor = factors.get(unit) if unit is not None else 1.0
    if factor is None:
        return None, f"unit {unit!r} is not supported"
    return value * factor, None
