"""Tests for auto-schedule settings serialization and clearing."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))

_ha_root = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
_ha_util = sys.modules.setdefault("homeassistant.util", types.ModuleType("homeassistant.util"))
_ha_dt = sys.modules.setdefault("homeassistant.util.dt", types.ModuleType("homeassistant.util.dt"))
_ha_dt.now = getattr(_ha_dt, "now", lambda *args, **kwargs: None)
_ha_util.dt = _ha_dt
_ha_root.util = _ha_util

_ps = types.ModuleType("power_sync")
_ps.__path__ = [str(ROOT)]
sys.modules["power_sync"] = _ps

_automations = types.ModuleType("power_sync.automations")
_automations.__path__ = [str(ROOT / "automations")]
sys.modules["power_sync.automations"] = _automations

sys.modules.pop("power_sync.const", None)
ev_planner = importlib.import_module("power_sync.automations.ev_charging_planner")


def test_empty_departure_times_does_not_rehydrate_legacy_schedule():
    settings = ev_planner.AutoScheduleSettings.from_dict({
        "departure_time": "07:30",
        "departure_days": [0, 1, 2, 3, 4],
        "departure_times": {},
    })

    assert settings.departure_times == {}
    assert settings.to_dict()["departure_time"] is None
    assert settings.to_dict()["departure_days"] == []


def test_empty_per_day_overrides_clear_legacy_aliases():
    settings = ev_planner.AutoScheduleSettings.from_dict({
        "departure_priorities": {},
        "departure_min_battery_to_start": {},
        "departure_home_battery_min": {"0": 80},
        "departure_limit_grid_import": {},
        "departure_no_grid_import": {"0": True},
        "departure_consume_battery_level": {},
        "departure_stop_at_battery_floor": {},
    })

    assert settings.departure_priorities == {}
    assert settings.departure_min_battery_to_start == {}
    assert settings.departure_limit_grid_import == {}
    assert settings.departure_consume_battery_level == {}
    assert settings.departure_stop_at_battery_floor == {}


def test_generic_status_entity_round_trips_with_auto_schedule_settings():
    settings = ev_planner.AutoScheduleSettings.from_dict({
        "charger_type": "generic",
        "charger_switch_entity": "switch.garage_ev",
        "charger_amps_entity": "number.garage_ev_current",
        "charger_status_entity": "sensor.garage_ev_status",
    })

    assert settings.charger_status_entity == "sensor.garage_ev_status"
    assert settings.to_dict()["charger_status_entity"] == "sensor.garage_ev_status"


def test_vehicle_charger_config_syncs_generic_status_entity():
    settings = ev_planner.AutoScheduleSettings()

    settings.apply_charger_config({
        "charger_type": "generic",
        "min_amps": 6,
        "max_amps": 24,
        "voltage": 240,
        "phases": 3,
        "charger_switch_entity": "switch.garage_ev",
        "charger_amps_entity": "number.garage_ev_current",
        "charger_status_entity": "sensor.garage_ev_status",
    })

    assert settings.charger_type == "generic"
    assert settings.min_charge_amps == 6
    assert settings.max_charge_amps == 24
    assert settings.voltage == 240
    assert settings.phases == 3
    assert settings.charger_status_entity == "sensor.garage_ev_status"


def test_configured_generic_entities_preserve_vehicle_overrides():
    params = {
        "charger_switch_entity": "switch.vehicle_ev",
        "charger_amps_entity": "number.vehicle_ev_current",
        "charger_status_entity": "sensor.vehicle_ev_status",
    }

    result = ev_planner._with_configured_charger_entities(
        params,
        {
            "generic_charger_switch_entity": "switch.global_ev",
            "generic_charger_amps_entity": "number.global_ev_current",
            "generic_charger_status_entity": "sensor.global_ev_status",
        },
        "generic",
    )

    assert result["charger_switch_entity"] == "switch.vehicle_ev"
    assert result["charger_amps_entity"] == "number.vehicle_ev_current"
    assert result["charger_status_entity"] == "sensor.vehicle_ev_status"


def test_power_to_amps_uses_vehicle_charger_phase_settings():
    executor = object.__new__(ev_planner.AutoScheduleExecutor)

    single_phase = ev_planner.AutoScheduleSettings(
        voltage=230,
        phases=1,
        max_charge_amps=32,
    )
    three_phase = ev_planner.AutoScheduleSettings(
        voltage=230,
        phases=3,
        max_charge_amps=32,
    )

    assert executor._power_to_amps_for_settings(6900, single_phase) == 30
    assert executor._power_to_amps_for_settings(6900, three_phase) == 10
