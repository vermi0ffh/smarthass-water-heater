"""The tank in energy: its deficit to a full tank, raised by draws and losses, lowered by heating, reset at a real
end of heating (see doc/agents/model.md)."""

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from .meter import meter_delta

# Energy to heat one liter of water by one degree (kWh/(L·°C))
WATER_KWH_PER_L_C = 0.001162

# The heater consumes above this power; the thermostat has cut below the idle power
HEATING_THRESHOLD_W = 100.0
IDLE_POWER_W = 5.0
# Idle that long, relay still on, after consuming: the thermostat reached its set point (as the Home Assistant
# automation it replaces, deployed on 2026-10-19: power below 5 W for 60 s)
END_OF_HEATING_S = 60.0

# A meter increase faster than this is a spurious value, not a measure: no domestic outlet draws 60 L/min, no
# single-phase heater exceeds 12 kW. One minute of slack covers readings that arrive late.
MAX_DRAW_L_PER_H = 3600.0
MAX_HEATING_KW = 12.0
METER_SLACK_H = 1 / 60

# Heat demand: after a full tank the thermostat restarts only once the water at its probe has cooled by its
# hysteresis. Without draw, it restarted after about 20 L of losses on a 200 L tank (2026-10-20, ≈ 1.2 kWh at 60 °C):
# the first threshold is the energy of 5 °C over the tank, then learned.
DEFAULT_HYSTERESIS_C = 5.0
# A draw lets cold water in at the bottom, next to the probe: it restarts the thermostat much sooner. Not measured
# yet: the first threshold, the first liter, errs toward allowing a heating (the behavior before the demand), then is
# learned.
DEFAULT_DRAW_THRESHOLD_L = 1.0
# A relay on without heating raises a threshold just above what was reached
LOSS_THRESHOLD_STEP_KWH = 0.05
DRAW_THRESHOLD_STEP_L = 1.0
# Beyond a quarter of the tank, a relay on without heating is no hysteresis but a fault (the thermostat's safety cut,
# a broken heater): the learning stops there
MAX_THRESHOLD_FRACTION = 0.25

# Standing losses: between two ends of heating without any draw, the tank is full at both ends, so what was heated is
# what was lost. From 2026-10-17 to 21, five such cycles of 12.6 to 47.6 h measured 87 to 98 W on a 200 L tank; one of
# 6.3 h measured 115 W, the end of heating's spread weighing on a short cycle: a cycle counts from 12 h.
MIN_LOSS_CYCLE_H = 12.0
# A cycle's losses beyond these multiples of the configured ones are a fault (an energy meter stuck or reset, a meter
# of water not counting), not losses
MIN_LOSS_RATIO, MAX_LOSS_RATIO = 0.25, 4.0
# The learned losses are those of the last cycles, weighted by their duration
LOSS_CYCLES_KEPT = 5


@dataclass(frozen=True)
class TankConfig:
    volume_l: float
    set_point_c: float
    losses_w: float
    cold_water_c: float
    # With an energy meter, the heating is counted by the meter only (an unavailable meter pauses it; its increase
    # counts when it comes back): integrating the power meanwhile would count it twice
    has_energy_meter: bool = False
    # The losses learned on the cycles without draw replace losses_w once there are some (rule 25: off until validated)
    learn_losses: bool = False

    @property
    def capacity_kwh(self) -> float:
        """Energy of a full tank above the cold water (≤ 0 when the set point is not above the cold water)."""
        return self.volume_l * WATER_KWH_PER_L_C * (self.set_point_c - self.cold_water_c)


@dataclass(frozen=True)
class LossCycle:
    """A cycle between two ends of heating without draw: its duration and what was heated (= lost)."""

    hours: float
    heated_kwh: float


@dataclass(frozen=True)
class Reading:
    """The inputs at a time; None = unavailable (an unavailable input pauses what depends on it)."""

    relay_on: bool | None
    power_w: float | None
    energy_kwh: float | None = None  # cumulative heater energy meter, None without one
    hot_water_l: float | None = None  # cumulative hot water meter at the tank's outlet


@dataclass
class TankState:
    """What survives a restart (persisted by the tracker)."""

    deficit_kwh: float | None = None  # None: unknown until the first full tank or a set level
    last_full: datetime | None = None
    updated_at: datetime | None = None
    reading: Reading = field(default_factory=lambda: Reading(None, None))
    # Last valid value of each cumulative meter and its time: the base of its next increase
    energy_meter_kwh: float | None = None
    energy_meter_at: datetime | None = None
    hot_water_meter_l: float | None = None
    hot_water_meter_at: datetime | None = None
    # The heater consumed during the current relay-on session; since when it has been idle, relay on
    heated_this_session: bool = False
    idle_since: datetime | None = None
    # Relay on, no consumption in this session: since when the power has been idle (no heat demand once long enough)
    quiet_since: datetime | None = None
    # The thermostat's heat demand, held from its start to the full tank (its hysteresis): None = unknown; measured
    # (the relay on showed it) or estimated (from the thresholds below)
    heat_demand: bool | None = None
    heat_demand_measured: bool = False
    drawn_since_full_l: float = 0.0
    # Learned thresholds; None = the defaults (DEFAULT_HYSTERESIS_C, DEFAULT_DRAW_THRESHOLD_L)
    loss_threshold_kwh: float | None = None
    draw_threshold_l: float | None = None
    # Heated since the last end of heating, while a cycle of losses is measured; None: none is (since a full tank
    # told or a level set, or a restart without an energy meter: the heating during the downtime is unknown)
    heated_since_full_kwh: float | None = None
    # The last cycles of losses, oldest first
    loss_cycles: list[LossCycle] = field(default_factory=list)
    # The losses of the last cycle measured, even when rejected as a fault (W)
    last_loss_cycle_w: float | None = None


# The fields added since version 1 are read with their default when absent
STATE_VERSION = 1


def state_to_dict(state: TankState) -> dict[str, Any]:
    """The state as JSON-compatible data (persisted by the tracker, format STATE_VERSION)."""
    r = state.reading
    return {
        "deficit_kwh": state.deficit_kwh,
        "last_full": _iso(state.last_full),
        "updated_at": _iso(state.updated_at),
        "reading": {
            "relay_on": r.relay_on,
            "power_w": r.power_w,
            "energy_kwh": r.energy_kwh,
            "hot_water_l": r.hot_water_l,
        },
        "energy_meter_kwh": state.energy_meter_kwh,
        "energy_meter_at": _iso(state.energy_meter_at),
        "hot_water_meter_l": state.hot_water_meter_l,
        "hot_water_meter_at": _iso(state.hot_water_meter_at),
        "heated_this_session": state.heated_this_session,
        "idle_since": _iso(state.idle_since),
        "quiet_since": _iso(state.quiet_since),
        "heat_demand": state.heat_demand,
        "heat_demand_measured": state.heat_demand_measured,
        "drawn_since_full_l": state.drawn_since_full_l,
        "loss_threshold_kwh": state.loss_threshold_kwh,
        "draw_threshold_l": state.draw_threshold_l,
        "heated_since_full_kwh": state.heated_since_full_kwh,
        "loss_cycles": [[c.hours, c.heated_kwh] for c in state.loss_cycles],
        "last_loss_cycle_w": state.last_loss_cycle_w,
    }


def state_from_dict(data: Any) -> TankState:
    """The state persisted by state_to_dict. A missing or unreadable field (a truncated or edited file) takes its
    default: the estimate is then unknown rather than wrong."""
    if not isinstance(data, dict):
        return TankState()
    r = data.get("reading")
    r = r if isinstance(r, dict) else {}
    relay_on = r.get("relay_on")
    deficit = _float(data.get("deficit_kwh"))
    demand = data.get("heat_demand")
    drawn = _float(data.get("drawn_since_full_l"))
    heated = _float(data.get("heated_since_full_kwh"))
    return TankState(
        deficit_kwh=None if deficit is None or deficit < 0 else deficit,
        last_full=_parse(data.get("last_full")),
        updated_at=_parse(data.get("updated_at")),
        reading=Reading(
            relay_on if isinstance(relay_on, bool) else None,
            _float(r.get("power_w")),
            _float(r.get("energy_kwh")),
            _float(r.get("hot_water_l")),
        ),
        energy_meter_kwh=_float(data.get("energy_meter_kwh")),
        energy_meter_at=_parse(data.get("energy_meter_at")),
        hot_water_meter_l=_float(data.get("hot_water_meter_l")),
        hot_water_meter_at=_parse(data.get("hot_water_meter_at")),
        heated_this_session=data.get("heated_this_session") is True,
        idle_since=_parse(data.get("idle_since")),
        quiet_since=_parse(data.get("quiet_since")),
        heat_demand=demand if isinstance(demand, bool) else None,
        heat_demand_measured=isinstance(demand, bool) and data.get("heat_demand_measured") is True,
        drawn_since_full_l=0.0 if drawn is None or drawn < 0 else drawn,
        loss_threshold_kwh=_positive(data.get("loss_threshold_kwh")),
        draw_threshold_l=_positive(data.get("draw_threshold_l")),
        heated_since_full_kwh=None if heated is None or heated < 0 else heated,
        loss_cycles=_loss_cycles_from(data.get("loss_cycles")),
        last_loss_cycle_w=_positive(data.get("last_loss_cycle_w")),
    )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: Any) -> datetime | None:
    """An aware datetime, None when absent or unreadable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _loss_cycles_from(value: Any) -> list[LossCycle]:
    """The stored cycles of losses ([hours, kWh heated]); an unreadable one is dropped."""
    if not isinstance(value, list):
        return []
    cycles = []
    for item in value[-LOSS_CYCLES_KEPT:]:
        if isinstance(item, list) and len(item) == 2:
            hours, kwh = _positive(item[0]), _positive(item[1])
            if hours is not None and kwh is not None:
                cycles.append(LossCycle(hours, kwh))
    return cycles


def _positive(value: Any) -> float | None:
    number = _float(value)
    return number if number is not None and number > 0 else None


def _float(value: Any) -> float | None:
    """A finite number, None otherwise (bool, text, nan, inf)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


class Tank:
    def __init__(self, config: TankConfig, state: TankState | None = None):
        self.config = config
        self.state = state if state is not None else TankState()

    # --- Reading ---

    @property
    def energy_to_full_kwh(self) -> float | None:
        return self.state.deficit_kwh if self.config.capacity_kwh > 0 else None

    @property
    def level_pct(self) -> float | None:
        deficit = self.energy_to_full_kwh
        if deficit is None:
            return None
        return 100.0 * (1.0 - deficit / self.config.capacity_kwh)

    @property
    def level_l(self) -> float | None:
        pct = self.level_pct
        return None if pct is None else self.config.volume_l * pct / 100.0

    @property
    def last_full(self) -> datetime | None:
        return self.state.last_full

    @property
    def hours_since_full(self) -> float | None:
        """At the last update (the tracker updates at least every few minutes)."""
        last, now = self.state.last_full, self.state.updated_at
        return None if last is None or now is None else max(0.0, (now - last).total_seconds() / 3600.0)

    @property
    def end_of_heating_due_at(self) -> datetime | None:
        """When to update again, with the same reading, to see an end of heating: no new reading comes while the
        power stays at 0."""
        idle_since = self.state.idle_since
        return None if idle_since is None else idle_since + timedelta(seconds=END_OF_HEATING_S)

    @property
    def heat_demand_due_at(self) -> datetime | None:
        """When to update again, with the same reading, to see that the relay on does not heat (no heat demand)."""
        quiet_since, now = self.state.quiet_since, self.state.updated_at
        if quiet_since is None:
            return None
        due = quiet_since + timedelta(seconds=END_OF_HEATING_S)
        return due if now is None or now < due else None

    @property
    def heat_demand(self) -> bool | None:
        """Would the heater heat with the relay on: the thermostat's demand, held until the tank is full."""
        return self.state.heat_demand

    @property
    def heat_demand_measured(self) -> bool:
        """The demand was seen with the relay on (or told), not estimated from the thresholds."""
        return self.state.heat_demand_measured

    @property
    def learned_losses_w(self) -> float | None:
        """The losses of the last cycles without draw, weighted by their duration; None before the first one."""
        cycles = self.state.loss_cycles
        if not cycles:
            return None
        return 1000.0 * sum(c.heated_kwh for c in cycles) / sum(c.hours for c in cycles)

    @property
    def loss_cycle_count(self) -> int:
        """How many cycles the learned losses are the mean of (at most LOSS_CYCLES_KEPT)."""
        return len(self.state.loss_cycles)

    @property
    def last_loss_cycle_w(self) -> float | None:
        """The losses of the last cycle measured, kept even when beyond the bounds (rejected as a fault)."""
        return self.state.last_loss_cycle_w

    @property
    def losses_in_use_w(self) -> float:
        """The losses the estimate counts: the learned ones once enabled and measured, else the configured ones."""
        learned = self.learned_losses_w
        return learned if self.config.learn_losses and learned is not None else self.config.losses_w

    @property
    def loss_threshold_kwh(self) -> float:
        """The deficit, without draw since the full tank, from which the thermostat demands."""
        learned = self.state.loss_threshold_kwh
        return learned if learned is not None else self.config.volume_l * WATER_KWH_PER_L_C * DEFAULT_HYSTERESIS_C

    @property
    def draw_threshold_l(self) -> float:
        """The liters drawn since the full tank from which the thermostat demands."""
        learned = self.state.draw_threshold_l
        return learned if learned is not None else DEFAULT_DRAW_THRESHOLD_L

    @property
    def energy_before_demand_kwh(self) -> float | None:
        """Losses still needed before the thermostat demands (0 while it demands, None when unknown)."""
        demand, deficit = self.state.heat_demand, self.energy_to_full_kwh
        if demand is None or deficit is None:
            return None
        return 0.0 if demand else max(0.0, self.loss_threshold_kwh - deficit)

    @property
    def liters_before_demand_l(self) -> float | None:
        """Liters still to draw before the thermostat demands (0 while it demands, None when unknown)."""
        demand = self.state.heat_demand
        if demand is None:
            return None
        return 0.0 if demand else max(0.0, self.draw_threshold_l - self.state.drawn_since_full_l)

    # --- Updating ---

    def update(self, at: datetime, reading: Reading) -> None:
        """Accounts for the interval since the last update with the inputs then in force (held), then takes
        `reading`: its meters' increases and its heating state. A reading older than the last update accounts for no
        interval."""
        state = self.state
        if state.updated_at is not None and at > state.updated_at:
            self._account(state.updated_at, at, state.reading)
        self._take_meters(at, reading)
        observed = self._follow_heating(at, reading)
        if observed is None:
            self._estimate_demand()
        else:
            self._observe_demand(observed)
        state.reading = reading
        state.updated_at = at if state.updated_at is None else max(at, state.updated_at)

    def resume(self) -> None:
        """After a restart: nothing is known of the downtime but its duration. Its losses are counted at the next
        update, and the heating by the energy meter's increase; the power last read is not held over it, and a heating
        session in progress is forgotten (its end, if any, was not seen)."""
        state = self.state
        state.reading = replace(state.reading, power_w=None)
        if not self.config.has_energy_meter:
            state.heated_since_full_kwh = None  # the heating during the downtime is unknown
        state.heated_this_session = False
        state.idle_since = None
        state.quiet_since = None

    def forget_meters(self, energy: bool, hot_water: bool) -> None:
        """Another meter replaces the one counted so far: its value is no increase of the previous one. Its next
        reading is the base it is counted from."""
        state = self.state
        if energy or hot_water:
            # The heating or the draws between the two meters are not seen: no cycle of losses across the change
            state.heated_since_full_kwh = None
        if energy:
            state.energy_meter_kwh, state.energy_meter_at = None, None
            state.reading = replace(state.reading, energy_kwh=None)
        if hot_water:
            state.hot_water_meter_l, state.hot_water_meter_at = None, None
            state.reading = replace(state.reading, hot_water_l=None)

    def mark_full(self, at: datetime) -> None:
        """The user says the tank is full now: no cycle of losses is measured from a full tank told."""
        self._set_full(at)
        self.state.heated_since_full_kwh = None

    def _set_full(self, at: datetime) -> None:
        state = self.state
        state.deficit_kwh = 0.0
        state.last_full = at
        state.drawn_since_full_l = 0.0
        state.heat_demand, state.heat_demand_measured = False, True

    def set_level_l(self, level_l: float) -> None:
        """The user gives the level (liters at the set point), e.g. from the estimate the integration replaces."""
        if not math.isfinite(level_l) or self.config.capacity_kwh <= 0:
            return
        level = min(max(level_l, 0.0), self.config.volume_l)
        self.state.deficit_kwh = self.config.capacity_kwh * (1.0 - level / self.config.volume_l)
        self.state.heated_since_full_kwh = None
        # The demand is estimated again from the given level
        self.state.heat_demand, self.state.heat_demand_measured = None, False
        self._estimate_demand()

    # --- Internals ---

    def _account(self, start: datetime, end: datetime, held: Reading) -> None:
        """Losses over the interval, and the heating by power when there is no energy meter."""
        hours = (end - start).total_seconds() / 3600.0
        delta = self.losses_in_use_w * hours / 1000.0
        if not self.config.has_energy_meter:
            if held.power_w is not None and held.power_w > 0:
                delta -= held.power_w * hours / 1000.0
                self._count_heated(held.power_w * hours / 1000.0)
            elif held.power_w is None and held.relay_on is not False:
                # A heating could go unseen: the cycle would count it as no loss
                self.state.heated_since_full_kwh = None
        self._add(delta)

    def _take_meters(self, at: datetime, reading: Reading) -> None:
        state, cfg = self.state, self.config
        if reading.energy_kwh is not None:
            cap = MAX_HEATING_KW * (_hours(state.energy_meter_at, at) + METER_SLACK_H)
            heated = meter_delta(state.energy_meter_kwh, reading.energy_kwh, cap)
            self._add(-heated)
            self._count_heated(heated)
            state.energy_meter_kwh, state.energy_meter_at = reading.energy_kwh, at
        if reading.hot_water_l is not None:
            cap = MAX_DRAW_L_PER_H * (_hours(state.hot_water_meter_at, at) + METER_SLACK_H)
            liters = meter_delta(state.hot_water_meter_l, reading.hot_water_l, cap)
            state.drawn_since_full_l += liters
            self._add(liters * WATER_KWH_PER_L_C * (cfg.set_point_c - cfg.cold_water_c))
            state.hot_water_meter_l, state.hot_water_meter_at = reading.hot_water_l, at

    def _follow_heating(self, at: datetime, reading: Reading) -> bool | None:
        """Follows the relay-on session; returns the heat demand it shows (True: heating, False: the relay on for
        END_OF_HEATING_S without heating), None when it shows none."""
        state = self.state
        if reading.relay_on is not True:
            # Relay off (or unknown): an interrupted heating is not a full tank
            state.heated_this_session = False
            state.idle_since = None
            state.quiet_since = None
            return None
        power = reading.power_w
        if power is None:
            return None
        if power > HEATING_THRESHOLD_W:
            state.heated_this_session = True
            state.idle_since = None
            state.quiet_since = None
            return True
        if power >= IDLE_POWER_W:
            return None
        if state.heated_this_session:
            if state.idle_since is None:
                state.idle_since = at
            if (at - state.idle_since).total_seconds() >= END_OF_HEATING_S:
                self._measure_losses(at, reading)  # before _set_full: the cycle ends at the previous full tank
                self._set_full(at)
                state.heated_this_session = False
                state.idle_since = None
            return None
        if state.quiet_since is None:
            state.quiet_since = at
        return False if (at - state.quiet_since).total_seconds() >= END_OF_HEATING_S else None

    def _demand_reached(self) -> tuple[bool, bool] | None:
        """Whether the losses and whether the draws since the full tank reached their threshold; None while the
        deficit is unknown."""
        deficit = self.energy_to_full_kwh
        if deficit is None:
            return None
        return deficit >= self.loss_threshold_kwh, self.state.drawn_since_full_l >= self.draw_threshold_l

    def _estimate_demand(self) -> None:
        """Without a relay-on session to show it: the thermostat demands once a threshold is reached, and still
        does until the tank is full."""
        state, reached = self.state, self._demand_reached()
        if state.heat_demand is True or reached is None:
            return
        if any(reached):
            state.heat_demand, state.heat_demand_measured = True, False
        elif state.heat_demand is None:
            state.heat_demand, state.heat_demand_measured = False, False

    def _observe_demand(self, heating: bool) -> None:
        """The relay on shows the demand. When the estimate said otherwise, the threshold it relied on is learned:
        a heating starting before the thresholds lowers the one of the cause (the draws if any, else the losses), a
        relay on without heating raises the thresholds reached just above."""
        state, reached = self.state, self._demand_reached()
        cfg = self.config
        if reached is not None:
            if heating and state.heat_demand is False:
                if state.drawn_since_full_l > 0:
                    state.draw_threshold_l = max(state.drawn_since_full_l, DRAW_THRESHOLD_STEP_L)
                else:
                    state.loss_threshold_kwh = max(self.energy_to_full_kwh or 0.0, LOSS_THRESHOLD_STEP_KWH)
            elif not heating:
                losses_reached, draws_reached = reached
                if losses_reached:
                    state.loss_threshold_kwh = min(
                        (self.energy_to_full_kwh or 0.0) + LOSS_THRESHOLD_STEP_KWH,
                        cfg.capacity_kwh * MAX_THRESHOLD_FRACTION,
                    )
                if draws_reached:
                    state.draw_threshold_l = min(
                        state.drawn_since_full_l + DRAW_THRESHOLD_STEP_L, cfg.volume_l * MAX_THRESHOLD_FRACTION
                    )
        state.heat_demand, state.heat_demand_measured = heating, True

    def _count_heated(self, kwh: float) -> None:
        """Adds to the cycle of losses measured, if one is."""
        if self.state.heated_since_full_kwh is not None:
            self.state.heated_since_full_kwh += kwh

    def _measure_losses(self, at: datetime, reading: Reading) -> None:
        """At a real end of heating: the cycle since the previous one, without any draw and long enough, gives the
        losses (what was heated was lost). Then a new cycle starts — only with a hot water meter read, the draws being
        otherwise unknown."""
        state = self.state
        heated, last = state.heated_since_full_kwh, state.last_full
        if (
            heated is not None
            and last is not None
            and state.drawn_since_full_l == 0
            and reading.hot_water_l is not None
        ):
            hours = (at - last).total_seconds() / 3600.0
            if hours >= MIN_LOSS_CYCLE_H:
                losses_w = 1000.0 * heated / hours
                state.last_loss_cycle_w = losses_w
                configured = self.config.losses_w
                # Configured as 0 (unknown to the user): no reference to call a cycle a fault
                if configured <= 0 or MIN_LOSS_RATIO * configured <= losses_w <= MAX_LOSS_RATIO * configured:
                    state.loss_cycles = [*state.loss_cycles, LossCycle(hours, heated)][-LOSS_CYCLES_KEPT:]
        state.heated_since_full_kwh = 0.0 if reading.hot_water_l is not None else None

    def _add(self, delta_kwh: float) -> None:
        deficit, capacity = self.state.deficit_kwh, self.config.capacity_kwh
        if deficit is None or capacity <= 0:
            return
        self.state.deficit_kwh = min(max(deficit + delta_kwh, 0.0), capacity)


def _hours(since: datetime | None, at: datetime) -> float:
    return 0.0 if since is None else max(0.0, (at - since).total_seconds() / 3600.0)
