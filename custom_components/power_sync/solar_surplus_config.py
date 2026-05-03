"""Shared helpers for EV solar-surplus charging configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC = 80

DEFAULT_SOLAR_SURPLUS_CONFIG: dict[str, Any] = {
    "enabled": False,
    "household_buffer_kw": 0.5,
    "surplus_calculation": "grid_based",
    "sustained_surplus_minutes": 2,
    "stop_delay_minutes": 5,
    "dual_vehicle_strategy": "priority_first",
    "home_battery_minimum": DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC,
    "min_battery_soc": DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC,
    "allow_parallel_charging": False,
    "max_battery_charge_rate_kw": 5.0,
}


def _coerce_percentage(value: Any) -> int | None:
    """Coerce a value to a clamped percentage, returning None if invalid."""
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return None


def get_solar_surplus_min_battery_soc(
    config: Mapping[str, Any] | None,
    default: int = DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC,
) -> int:
    """Return the configured home-battery SOC threshold for solar EV charging."""
    if config:
        for key in ("home_battery_minimum", "min_battery_soc"):
            if key in config:
                value = _coerce_percentage(config[key])
                if value is not None:
                    return value

    value = _coerce_percentage(default)
    return value if value is not None else DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC


def normalize_solar_surplus_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Merge solar-surplus config with defaults and keep threshold aliases aligned."""
    min_battery_soc = get_solar_surplus_min_battery_soc(config)
    normalized = dict(DEFAULT_SOLAR_SURPLUS_CONFIG)
    if config:
        normalized.update(dict(config))

    normalized["home_battery_minimum"] = min_battery_soc
    normalized["min_battery_soc"] = min_battery_soc
    return normalized
