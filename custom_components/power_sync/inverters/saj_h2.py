"""SAJ H2 / HS2 battery bridge via the upstream saj_h2_modbus integration.

PowerSync does not open a second Modbus connection here. Instead it discovers
the entities created by `stanus74/home-assistant-saj-h2-modbus` and controls
them through Home Assistant services.

Control model (verified against stanus74 discussion #105 and live testing):

  Modes (`number.saj_app_mode_input`):
    0 = Self-Use (normal)
    1 = TOU (Time-of-Use schedule)
    2 = Backup
    3 = Passive

  force_charge — uses Passive mode (AppMode=3):
    - Passive mode is "fixed power, PV subtracted from target". A 5 kW
      charge target with 2 kW PV draws 3 kW from grid. This is the right
      behaviour for charging during cheap-price windows because the LP
      picks the rate to fit the energy budget.
    - Sequence:
        write number.saj_passive_bat_charge_power_input  = pct  (0-1000)
        write number.saj_passive_bat_discharge_power_input = 0
        turn ON switch.saj_passive_charge_control
          (stanus74 atomically captures AppMode → AppMode=3 → passive_enable=2)
    - pct encoding (per stanus74/home-assistant-saj-h2-modbus#105):
        value = (requested_w / inverter_rated_w) * 1000, capped at 1000.
        1000 = 100% of rated, 1100 is a "default" sentinel and is AVOIDED
        because empirically it triggers fall-back load-following on this
        firmware instead of true rated discharge.

  force_discharge — uses TOU mode (AppMode=1) with discharge slot 7:
    - Passive discharge is load-following with a small grid-push margin
      (~500-1100 W above home load). It cannot push the battery to grid at
      a fixed high rate. TOU discharge is "fixed % of rated, PV added on
      top" — exactly what the LP wants for AEMO spike export.
    - PowerSync owns slot 7 (00:00-23:59, days=127, power=100). One-time
      bootstrap on every force_discharge call (idempotent). Toggle bit 6
      of `discharge_time_enable_input` to start/stop.
    - Sequence:
        text.saj_discharge7_start_time_time = "00:00"
        text.saj_discharge7_end_time_time   = "23:59"
        number.saj_discharge7_day_mask_input = 127
        number.saj_discharge7_power_percent_input = 100
        cache current charge_time_enable bitmask, then clear it (so a
          user-configured charge slot in AppMode=1 doesn't fight us)
        number.saj_discharge_time_enable_input = current_bitmask | (1<<6)
        number.saj_app_mode_input = 1
    - Restore:
        number.saj_discharge_time_enable_input = current_bitmask & ~(1<<6)
        number.saj_charge_time_enable_input = cached_charge_enable
        number.saj_app_mode_input = 0

  Passive grid_charge_power / grid_discharge_power are NOT written. Per
  discussion #105 stanus74 author confirmed grid_charge_power has no
  effect; jsjhb confirmed grid_discharge_power can have effect on parallel
  inverter setups but recommended leaving both at default. We honour that.

  charging_control / discharging_control switches are not used for
  force_charge or force_discharge. They are turned OFF in restore_normal
  as a defensive measure in case a previous version of this controller
  (or a manual toggle in the SAJ Modbus UI) left them on.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)


# Maps internal slot → tuple of unique_id suffixes to try (first match wins).
# Fast-poll sensors are preferred over slow-poll for fresher readings.
# stanus74 integration uses camelCase keys: unique_id = f"{hub_name}_{key}" or f"{hub_name}_fast_{key}"
_SENSOR_KEYS: dict[str, tuple[str, ...]] = {
    "battery_level":               ("Bat1SOC", "batEnergyPercent"),
    "battery_power":               ("batteryPower",),
    # CT_GridPowerWatt is the whole-house meter and reflects AC-coupled inverter
    # contribution; gridPower is only the SAJ's own grid leg. Prefer the CT.
    "grid_power":                  ("CT_GridPowerWatt", "gridPower", "totalgridPower"),
    "solar_power":                 ("CT_PVPowerWatt", "pvPower"),
    "load_power":                  ("TotalLoadPower", "gridPower"),
    "battery_temperature":         ("BatTemp", "Bat1Temperature"),
    "battery_soh":                 ("Bat1SOH",),
    "app_mode":                    ("AppMode",),
    "battery_max_charge_power_w":  ("BatChargePower", "GridChargePower", "BatChaCurrLimit"),
    "battery_max_discharge_power_w": ("BatDischargePower", "GridDischargePower", "BatDisCurrLimit"),
    # Direction sensors — 1=discharging/export, -1=charging/import, 0=idle
    "direction_battery":           ("directionBattery",),
    "direction_grid":              ("directionGrid",),
    # Engagement signals — distinguish "battery converter active" (mode 2, R-phase ~240V)
    # from "low-SOC lockout" (mode 4, R-phase 0V). Without these the controller silently
    # writes Modbus commands that go nowhere because the inverter's converter is offline.
    "inverter_working_mode":       ("mpvmode",),
    "inverter_voltage_r":          ("RInvVolt",),
    # Bitmask sensors — read at runtime to OR/AND our slot bit cleanly without
    # clobbering user-configured slots (the *_input number entities can be stale
    # versus the actual register, so we trust the sensor for current state).
    "discharge_time_enable_bitmask": ("discharge_time_enable",),
    "charge_time_enable_bitmask":    ("charge_time_enable",),
}

# Maps internal slot → unique_id suffix for writable number entities.
# stanus74 constructs unique_id as f"{hub_name}_{key}_input" for all number entities.
# NOTE: passive_charge_enable is intentionally absent — passive mode entry is
# managed via the switch entities below, which handle AppMode internally.
# passive_grid_charge_power and passive_grid_discharge_power are NOT written
# (per discussion #105: grid_charge_power has no effect; grid_discharge_power
# behaviour varies per setup, recommended to leave at default).
# app_mode_writable is used by restore_normal() to force AppMode=0 (Self-Use)
# after a force charge/discharge, and by force_discharge to enter AppMode=1 (TOU).
# discharge7_*_input and charge_time_enable / discharge_time_enable drive the
# TOU-mode force_discharge path. PowerSync owns slot 7.
_NUMBER_KEYS: dict[str, str] = {
    "charge_power":            "passive_bat_charge_power_input",
    "discharge_power":         "passive_bat_discharge_power_input",
    "app_mode_writable":       "app_mode_input",
    "discharge7_day_mask":     "discharge7_day_mask_input",
    "discharge7_power_percent": "discharge7_power_percent_input",
    "discharge_time_enable":   "discharge_time_enable_input",
    "charge_time_enable":      "charge_time_enable_input",
}

# Maps internal slot → unique_id suffix for writable text entities.
# stanus74 exposes slot start/end times under the `text.` domain (not number)
# with unique_id = f"{hub_name}_discharge{N}_start_time" / "_end_time".
# We only need slot 7 since PowerSync owns it for force_discharge.
_TEXT_KEYS: dict[str, str] = {
    "discharge7_start_time": "discharge7_start_time",
    "discharge7_end_time":   "discharge7_end_time",
}

# Read-only sensor for the current discharge_time_enable bitmask. We read this
# so we can OR/AND our slot-7 bit cleanly without clobbering user-configured
# slots 1-6.
_DISCHARGE_ENABLE_SENSOR_SUFFIX = "discharge_time_enable"
_CHARGE_ENABLE_SENSOR_SUFFIX = "charge_time_enable"

# Slot 7 lives at bit 6 of the enable bitmask (slot N → bit N-1).
_POWERSYNC_DISCHARGE_SLOT = 7
_POWERSYNC_DISCHARGE_BIT = 1 << (_POWERSYNC_DISCHARGE_SLOT - 1)  # 0b1000000 = 64

# AppMode values
_APP_MODE_SELF_USE = 0
_APP_MODE_TOU = 1
_APP_MODE_PASSIVE = 3

# Maps internal slot → unique_id suffix for writable switch entities.
# stanus74 constructs unique_id as f"{hub_name}_{switch_type}{unique_id_suffix}".
# passive_charge_control ON  → hub.set_passive_mode(2) → AppMode=3 + passive_enable=2
# passive_charge_control OFF → hub.set_passive_mode(0) → passive_enable=0 + AppMode restored
# passive_discharge_control ON  → hub.set_passive_mode(1) → AppMode=3 + passive_enable=1
# passive_discharge_control OFF → hub.set_passive_mode(0) → passive_enable=0 + AppMode restored
_SWITCH_KEYS: dict[str, str] = {
    "charging_control":         "charging_control",
    "discharging_control":      "discharging_control",
    "passive_charge_control":   "passive_charge_control",
    "passive_discharge_control": "passive_discharge_control",
}


class SajH2BatteryController:
    """Bridge controller for SAJ H2 entities exposed by saj_h2_modbus."""

    # SOC band guarding force_discharge. The SAJ inverter's discharge_depth register
    # cannot be written reliably from the stanus74 integration (writes silently
    # ignored on tested firmware), so the user-facing min_soc must be enforced in
    # software here. The +1% buffer keeps us off the inverter's own low-SOC lockout
    # which trips at the register floor (typically 5%) and requires a power-cycle.
    _MIN_SOC_BUFFER_PCT = 1.0

    # Minimum R-phase inverter voltage that indicates the battery DC-DC converter
    # is actually engaged. When the converter is offline the register reads 0V even
    # though the on-grid pass-through is at 235V — so we test the inverter (battery)
    # leg specifically, not the grid leg.
    _MIN_ENGAGED_INV_VOLTAGE = 50.0

    def __init__(
        self,
        hass: Any,
        saj_entry_id: str,
        battery_capacity_kwh: float = 10.0,
        min_soc_pct: float = 5.0,
        inverter_rated_kw: float = 10.0,
    ) -> None:
        self.hass = hass
        self._saj_entry_id = saj_entry_id
        self._battery_capacity_kwh = float(battery_capacity_kwh)
        self._min_soc_pct = float(min_soc_pct)
        self._inverter_rated_w = float(inverter_rated_kw) * 1000.0
        self._entity_map: dict[str, str] = {}
        # Cache of the user's charge_time_enable bitmask captured on
        # force_discharge entry, so restore_normal can put it back.
        # None when not currently in force_discharge.
        self._cached_charge_enable: int | None = None

    def set_min_soc_pct(self, min_soc_pct: float) -> None:
        """Update the software-enforced discharge floor (called when user changes it)."""
        self._min_soc_pct = float(min_soc_pct)

    async def connect(self) -> bool:
        """Validate that the required SAJ entities exist."""
        self._discover_entities()
        required = (
            "battery_level",
            "battery_power",
            "grid_power",
            "solar_power",
            "load_power",
        )
        missing = [key for key in required if key not in self._entity_map]
        if missing:
            raise ValueError(f"saj_missing_entities:{','.join(missing)}")
        _LOGGER.info(
            "SAJ H2 entities validated via config entry %s — mapped: %s",
            self._saj_entry_id,
            {k: v for k, v in self._entity_map.items()},
        )
        passive_missing = [
            k for k in ("charge_power", "discharge_power", "passive_charge_control")
            if k not in self._entity_map
        ]
        if passive_missing:
            _LOGGER.warning(
                "SAJ H2: passive-mode entities not found (%s) — force_charge will not work. "
                "Check that stanus74 exposes switch and number entities for passive mode.",
                passive_missing,
            )
        tou_missing = [
            k for k in (
                "discharge7_day_mask", "discharge7_power_percent",
                "discharge_time_enable", "app_mode_writable",
                "discharge7_start_time", "discharge7_end_time",
            )
            if k not in self._entity_map
        ]
        if tou_missing:
            _LOGGER.warning(
                "SAJ H2: TOU-mode entities not found (%s) — force_discharge will not work. "
                "Requires saj_h2_modbus version exposing discharge7_* number entities and "
                "discharge slot start/end time text entities.",
                tou_missing,
            )
        return True

    def _discover_entities(self) -> None:
        """Discover entity IDs from the upstream config entry."""
        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(registry, self._saj_entry_id)

        by_uid: dict[str, str] = {
            reg_entry.unique_id: reg_entry.entity_id
            for reg_entry in entries
            if reg_entry.unique_id and reg_entry.entity_id
        }

        _LOGGER.debug("SAJ H2 entity registry: %d entities found for entry %s", len(by_uid), self._saj_entry_id)

        for target, keys in _SENSOR_KEYS.items():
            if target in self._entity_map:
                continue
            for key in keys:
                # Prefer fast-poll over slow-poll for fresher readings
                fast = self._find_uid_suffix(by_uid, f"_fast_{key}")
                regular = self._find_uid_suffix(by_uid, f"_{key}", exclude=f"_fast_{key}")
                chosen = fast or regular
                if chosen:
                    self._entity_map[target] = chosen
                    _LOGGER.debug("SAJ H2: mapped %s → %s", target, chosen)
                    break
            else:
                _LOGGER.debug("SAJ H2: no entity found for sensor slot '%s' (tried: %s)", target, keys)

        for target, key in _NUMBER_KEYS.items():
            if target not in self._entity_map:
                entity_id = self._find_uid_suffix(by_uid, f"_{key}")
                if entity_id:
                    self._entity_map[target] = entity_id
                    _LOGGER.debug("SAJ H2: mapped %s → %s", target, entity_id)
                else:
                    _LOGGER.debug("SAJ H2: no number entity found for '%s' (suffix: _%s)", target, key)

        for target, suffix in _SWITCH_KEYS.items():
            if target not in self._entity_map:
                entity_id = self._find_uid_suffix(by_uid, suffix)
                if entity_id:
                    self._entity_map[target] = entity_id
                    _LOGGER.debug("SAJ H2: mapped %s → %s", target, entity_id)
                else:
                    _LOGGER.debug("SAJ H2: no switch entity found for '%s' (suffix: %s)", target, suffix)

        for target, suffix in _TEXT_KEYS.items():
            if target not in self._entity_map:
                entity_id = self._find_uid_suffix(by_uid, suffix)
                if entity_id:
                    self._entity_map[target] = entity_id
                    _LOGGER.debug("SAJ H2: mapped %s → %s", target, entity_id)
                else:
                    _LOGGER.debug("SAJ H2: no text entity found for '%s' (suffix: %s)", target, suffix)

    @staticmethod
    def _find_uid_suffix(
        uid_map: dict[str, str],
        suffix: str,
        exclude: str | None = None,
    ) -> str | None:
        for unique_id, entity_id in uid_map.items():
            if exclude and unique_id.endswith(exclude):
                continue
            if unique_id.endswith(suffix):
                return entity_id
        return None

    def _read_direction(self, key: str) -> str | None:
        """Return 'active_b' (discharge/export), 'active_a' (charge/import), 'idle', or None."""
        entity_id = self._entity_map.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unavailable", "unknown", ""):
            return None
        val = state.state.lower().strip()
        # Numeric convention (SAJ): 1=discharging/export, -1=charging/import, 0=idle
        try:
            n = int(float(val))
            if n == 1:
                return "active_b"   # discharging / grid export
            if n == -1:
                return "active_a"   # charging / grid import
            return "idle"
        except (ValueError, TypeError):
            pass
        # Text convention
        if "discharg" in val or "export" in val or "output" in val:
            return "active_b"
        if "charg" in val or "import" in val or "input" in val:
            return "active_a"
        return "idle"

    def get_status(self) -> dict[str, Any]:
        """Read current SAJ state and return PowerSync-canonical fields."""
        # Battery power: SAJ typically reports absolute value + direction sensor
        battery_w_raw = self._read_float("battery_power") or 0.0
        dir_bat = self._read_direction("direction_battery")
        if dir_bat == "active_b":       # discharging → positive (convention: + = export from battery)
            battery_kw = abs(battery_w_raw) / 1000.0
        elif dir_bat == "active_a":     # charging → negative (convention: - = import to battery)
            battery_kw = -abs(battery_w_raw) / 1000.0
        else:
            # No direction info — trust the raw value's sign (some versions report signed)
            battery_kw = battery_w_raw / 1000.0

        # Grid power: positive = import, negative = export
        grid_w_raw = self._read_float("grid_power") or 0.0
        dir_grid = self._read_direction("direction_grid")
        if dir_grid == "active_b":      # exporting
            grid_kw = -abs(grid_w_raw) / 1000.0
        elif dir_grid == "active_a":    # importing
            grid_kw = abs(grid_w_raw) / 1000.0
        else:
            grid_kw = grid_w_raw / 1000.0

        # battery_max_*_power_w: derived from inverter rated × user-configured
        # power_limit percentage. The stanus74 BatChargePower / BatDischargePower
        # sensors return a percentage (0-100), not watts — using them directly
        # gave the LP wrong values and broke discharge/charge rate math.
        max_charge_pct = self._read_float("battery_max_charge_power_w")
        max_discharge_pct = self._read_float("battery_max_discharge_power_w")
        max_charge_w = (
            self._inverter_rated_w * (max_charge_pct / 100.0)
            if max_charge_pct is not None and 0 < max_charge_pct <= 110
            else self._inverter_rated_w
        )
        max_discharge_w = (
            self._inverter_rated_w * (max_discharge_pct / 100.0)
            if max_discharge_pct is not None and 0 < max_discharge_pct <= 110
            else self._inverter_rated_w
        )

        return {
            "battery_level":              self._read_float("battery_level") or 0.0,
            "battery_power":              battery_kw,
            "grid_power":                 grid_kw,
            "solar_power":                max(0.0, (self._read_float("solar_power") or 0.0) / 1000.0),
            "load_power":                 max(0.0, (self._read_float("load_power") or 0.0) / 1000.0),
            "battery_temperature":        self._read_float("battery_temperature"),
            "battery_soh":                self._read_float("battery_soh"),
            "app_mode":                   self._read_float("app_mode"),
            "battery_capacity_kwh":       self._battery_capacity_kwh,
            "battery_max_charge_power_w": max_charge_w,
            "battery_max_discharge_power_w": max_discharge_w,
            "inverter_rated_w":           self._inverter_rated_w,
        }

    def _check_passive_control_entities(self, operation: str) -> bool:
        """Return False and log an error if passive-mode entities are not mapped.

        Used by force_charge and set_idle. force_discharge has its own TOU check.
        """
        missing = [
            k for k in ("charge_power", "discharge_power", "passive_charge_control")
            if not self._entity_map.get(k)
        ]
        if missing:
            _LOGGER.error(
                "SAJ H2: %s aborted — passive-mode entities not mapped: %s. "
                "Check that stanus74 exposes switch and number entities for passive mode.",
                operation, missing,
            )
            return False
        return True

    def _check_tou_control_entities(self, operation: str) -> bool:
        """Return False and log an error if TOU-mode entities are not mapped."""
        missing = [
            k for k in (
                "discharge7_day_mask", "discharge7_power_percent",
                "discharge_time_enable", "app_mode_writable",
                "discharge7_start_time", "discharge7_end_time",
            )
            if not self._entity_map.get(k)
        ]
        if missing:
            _LOGGER.error(
                "SAJ H2: %s aborted — TOU-mode entities not mapped: %s. "
                "Requires saj_h2_modbus version exposing discharge7_* number/text entities.",
                operation, missing,
            )
            return False
        return True

    def _check_engaged(self, operation: str) -> bool:
        """Refuse Modbus commands when the inverter's battery converter is offline.

        Real low-SOC lockout: working_mode goes to 4 AND R-phase inverter voltage
        drops to 0V together. The DC-DC converter is offline and the only fix is a
        physical power-cycle.

        Either signal alone is unreliable: working_mode oscillates 2↔4 every minute
        or two on healthy systems, and stanus74's RInvVolt register sometimes reads
        0V even while the inverter is exporting normally on certain firmwares.
        Refuse only when BOTH signals say lockout.
        """
        wm = self._read_float("inverter_working_mode")
        rv = self._read_float("inverter_voltage_r")
        mode_ok = wm is None or int(wm) == 2
        voltage_ok = rv is None or rv >= self._MIN_ENGAGED_INV_VOLTAGE
        if mode_ok or voltage_ok:
            return True
        _LOGGER.error(
            "SAJ H2: %s refused — inverter working_mode=%s and R-phase voltage %.1fV "
            "(need ≥%.0fV). Battery converter offline (low-SOC lockout). Power-cycle required.",
            operation, int(wm), rv, self._MIN_ENGAGED_INV_VOLTAGE,
        )
        return False

    async def force_charge(self, duration_minutes: int, power_w: int) -> bool:
        """Force battery to charge from grid.

        Passive charge mode (AppMode=3): sets charge power target then turns on
        the passive_charge_control switch, which triggers stanus74's
        _activate_passive_mode() — capturing the current AppMode and setting
        AppMode=3 atomically.

        PV power is SUBTRACTED from the fixed target (per stanus74 discussions/105):
        a 5 kW charge target with 2 kW PV draws 3 kW from grid. Grid_charge_power
        is not written — confirmed by stanus74 author to have no effect.
        """
        if not self._check_passive_control_entities("force_charge"):
            return False
        if not self._check_engaged("force_charge"):
            return False
        pct = self._power_to_scaled_percent(power_w)
        try:
            await self._set_number("discharge_power", 0)
            await self._set_number("charge_power", pct)
            await self._turn_on("passive_charge_control")
        except Exception:
            _LOGGER.exception("SAJ H2: force_charge failed mid-sequence — attempting restore_normal")
            await self.restore_normal()
            return False
        _LOGGER.info(
            "SAJ H2 force_charge: passive mode at %d/1000 (%.0f W of %.0f W rated)",
            pct, power_w if power_w else 0, self._inverter_rated_w,
        )
        return True

    async def force_discharge(self, duration_minutes: int, power_w: int) -> bool:
        """Push battery to grid via TOU mode (AppMode=1) with discharge slot 7.

        Passive discharge is load-following with a small grid-push margin and
        cannot dump battery to grid at a fixed high rate. TOU mode adds PV on
        top of the configured target — exactly what's needed for AEMO spike
        export. PowerSync owns slot 7 here:

            slot 7: 00:00–23:59, days=127, power=100  (always-ready)

        We toggle bit 6 of `discharge_time_enable_input` to start/stop the slot,
        and switch AppMode to 1 (TOU). Charge slots are temporarily disabled
        for the duration so a user-configured charge slot in AppMode=1 doesn't
        fight us — the original bitmask is captured and restored on stop.

        `power_w` is currently ignored — slot 7 always runs at 100% so the
        inverter pushes the battery's rated discharge to grid plus any PV on
        top. AEMO spikes and peak-export windows always want max rate. If a
        future use case needs throttling, we'd write
        number.saj_discharge7_power_percent_input here.
        """
        if not self._check_tou_control_entities("force_discharge"):
            return False
        if not self._check_engaged("force_discharge"):
            return False
        soc = self._read_float("battery_level")
        floor = self._min_soc_pct + self._MIN_SOC_BUFFER_PCT
        if soc is not None and soc <= floor:
            _LOGGER.warning(
                "SAJ H2: force_discharge refused — SOC %.1f%% at/below software floor %.1f%% "
                "(min_soc=%.1f%% + %.1f%% buffer). Holding battery to avoid low-SOC lockout.",
                soc, floor, self._min_soc_pct, self._MIN_SOC_BUFFER_PCT,
            )
            return False
        try:
            # Bootstrap (idempotent): make sure slot 7 spans the whole day at 100%.
            await self._set_text("discharge7_start_time", "00:00")
            await self._set_text("discharge7_end_time", "23:59")
            await self._set_number("discharge7_day_mask", 127)
            await self._set_number("discharge7_power_percent", 100)

            # Capture & clear charge_time_enable so user charge slots don't
            # contend with us in AppMode=1. Skip if we already cached it
            # (force_discharge called twice without restore in between).
            if self._cached_charge_enable is None:
                cached = self._read_int_sensor("charge_time_enable_bitmask")
                self._cached_charge_enable = cached if cached is not None else 0
                if "charge_time_enable" in self._entity_map:
                    await self._set_number("charge_time_enable", 0)

            # Set slot 7 bit on the discharge_time_enable bitmask.
            current = self._read_int_sensor("discharge_time_enable_bitmask") or 0
            await self._set_number(
                "discharge_time_enable",
                current | _POWERSYNC_DISCHARGE_BIT,
            )

            # Enter TOU mode.
            await self._set_number("app_mode_writable", _APP_MODE_TOU)
        except Exception:
            _LOGGER.exception("SAJ H2: force_discharge failed mid-sequence — attempting restore_normal")
            await self.restore_normal()
            return False
        _LOGGER.info(
            "SAJ H2 force_discharge: TOU mode, slot 7 at 100%% of %.0f W rated",
            self._inverter_rated_w,
        )
        return True

    async def set_idle(self) -> bool:
        """Hold battery at current SOC — no charge or discharge, grid serves home load.

        Enters passive charge mode with charge power zeroed. AppMode=3 (set by
        turning on passive_charge_control) prevents the TOU schedule from driving
        discharge. Because passive mode subtracts PV from the fixed target, a
        zero charge target also prevents PV from charging the battery — surplus
        PV exports to grid instead. This is intentional: idle means hold SOC.
        """
        if not self._check_passive_control_entities("set_idle"):
            return False
        if not self._check_engaged("set_idle"):
            return False
        try:
            await self._set_number("discharge_power", 0)
            await self._set_number("charge_power", 0)
            await self._turn_on("passive_charge_control")
        except Exception:
            _LOGGER.exception("SAJ H2: set_idle failed mid-sequence — attempting restore_normal")
            await self.restore_normal()
            return False
        _LOGGER.info("SAJ H2 idle: passive charge mode with zero power — battery held")
        return True

    async def restore_normal(self) -> bool:
        """Return to Self-Use mode regardless of which path got us here.

        Handles both:
          - Passive entry (force_charge / set_idle): zeros passive numbers and
            turns off the passive switches so stanus74's _deactivate_passive_mode
            restores the pre-passive AppMode capture.
          - TOU entry (force_discharge): clears slot 7's enable bit and restores
            the user's cached charge_time_enable bitmask.

        Then explicitly writes AppMode=0 (Self-Use) so the user always lands in
        Self-Use after a force operation, regardless of stanus74's AppMode capture.
        """
        # Passive-mode unwind
        await self._set_number("charge_power", 0)
        await self._set_number("discharge_power", 0)
        await self._turn_off("passive_charge_control")
        await self._turn_off("passive_discharge_control")
        await self._turn_off("charging_control")
        await self._turn_off("discharging_control")

        # TOU-mode unwind: clear slot 7 bit, restore user's charge slots
        if "discharge_time_enable" in self._entity_map:
            current = self._read_int_sensor("discharge_time_enable_bitmask") or 0
            await self._set_number(
                "discharge_time_enable",
                current & ~_POWERSYNC_DISCHARGE_BIT,
            )
        if self._cached_charge_enable is not None:
            if "charge_time_enable" in self._entity_map:
                await self._set_number("charge_time_enable", self._cached_charge_enable)
            self._cached_charge_enable = None

        # Final mode flip
        await self._set_number("app_mode_writable", _APP_MODE_SELF_USE)
        _LOGGER.info("SAJ H2 restored to Self-Use mode (AppMode=0)")
        return True

    async def disconnect(self) -> None:
        """No persistent connection to close."""
        return None

    def _power_to_scaled_percent(self, requested_w: int | float) -> int:
        """Convert watts to SAJ's % × 10 of rated power encoding.

        Per stanus74 discussion #105: 0–1000 = 0–100% of inverter rated AC
        capacity. Example: 1500 W on a 10K inverter → 150 (15% × 10).

        We deliberately AVOID writing 1100 (the so-called "default" sentinel) —
        empirically it triggers fall-back load-following on this firmware
        instead of true rated discharge. The cap is 1000 = 100% of rated.

        Returns 1000 (max) if requested_w is missing or non-positive — i.e.
        the LP says "max" or didn't provide a target.
        """
        rated_w = self._inverter_rated_w
        if not rated_w or rated_w <= 0:
            return 1000
        if not requested_w or requested_w <= 0:
            return 1000
        return max(0, min(1000, int(round((requested_w / rated_w) * 1000))))

    def _read_float(self, key: str) -> float | None:
        entity_id = self._entity_map.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unavailable", "unknown", ""):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _read_int_sensor(self, key: str) -> int | None:
        """Read a sensor entity value as an int (e.g. enable bitmask)."""
        val = self._read_float(key)
        if val is None:
            return None
        return int(val)

    async def _set_number(self, key: str, value: float) -> None:
        entity_id = self._entity_map.get(key)
        if not entity_id:
            _LOGGER.warning("SAJ H2: cannot set %s — number entity not mapped", key)
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": value},
            blocking=True,
        )

    async def _set_text(self, key: str, value: str) -> None:
        entity_id = self._entity_map.get(key)
        if not entity_id:
            _LOGGER.warning("SAJ H2: cannot set %s — text entity not mapped", key)
            return
        await self.hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": entity_id, "value": value},
            blocking=True,
        )

    async def _turn_on(self, key: str) -> None:
        entity_id = self._entity_map.get(key)
        if not entity_id:
            _LOGGER.debug("SAJ H2: cannot turn_on %s — switch entity not mapped", key)
            return
        await self.hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
        )

    async def _turn_off(self, key: str) -> None:
        entity_id = self._entity_map.get(key)
        if not entity_id:
            _LOGGER.debug("SAJ H2: cannot turn_off %s — switch entity not mapped", key)
            return
        await self.hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True,
        )
