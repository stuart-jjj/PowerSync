"""Tests for shared EV solar-surplus configuration helpers."""

from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"
_ps = types.ModuleType("power_sync")
_ps.__path__ = [str(ROOT)]
sys.modules["power_sync"] = _ps

from power_sync.solar_surplus_config import (  # noqa: E402
    DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC,
    get_solar_surplus_min_battery_soc,
    normalize_solar_surplus_config,
)


def test_default_min_battery_soc_is_80_percent():
    assert get_solar_surplus_min_battery_soc({}) == DEFAULT_SOLAR_SURPLUS_MIN_BATTERY_SOC


def test_home_battery_minimum_sets_runtime_threshold():
    config = normalize_solar_surplus_config({"home_battery_minimum": 40})

    assert config["home_battery_minimum"] == 40
    assert config["min_battery_soc"] == 40
    assert get_solar_surplus_min_battery_soc(config) == 40


def test_min_battery_soc_alias_is_accepted_for_new_clients():
    config = normalize_solar_surplus_config({"min_battery_soc": "35"})

    assert config["home_battery_minimum"] == 35
    assert config["min_battery_soc"] == 35


def test_min_battery_soc_is_clamped_to_percentage_range():
    assert get_solar_surplus_min_battery_soc({"home_battery_minimum": -5}) == 0
    assert get_solar_surplus_min_battery_soc({"home_battery_minimum": 140}) == 100


def test_invalid_primary_threshold_falls_back_to_alias():
    assert get_solar_surplus_min_battery_soc({
        "home_battery_minimum": "nope",
        "min_battery_soc": 45,
    }) == 45
