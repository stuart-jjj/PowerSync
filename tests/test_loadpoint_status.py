"""Tests for normalized EV loadpoint status helpers."""

from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"
_ps = types.ModuleType("power_sync")
_ps.__path__ = [str(ROOT)]
sys.modules["power_sync"] = _ps

_automations = types.ModuleType("power_sync.automations")
_automations.__path__ = [str(ROOT / "automations")]
sys.modules["power_sync.automations"] = _automations

from power_sync.automations.loadpoint_status import (  # noqa: E402
    build_generic_charger_observation,
    build_loadpoint_status,
)


def test_dynamic_state_reports_commanded_no_power_when_observed_power_is_zero():
    loadpoints = build_loadpoint_status(
        {
            "VIN123": {
                "active": True,
                "vehicle_name": "Blue Car",
                "current_amps": 16,
                "target_amps": 16,
                "charging_started": True,
                "params": {
                    "dynamic_mode": "solar_surplus",
                    "charger_type": "ocpp",
                    "voltage": 230,
                    "phases": 1,
                },
            }
        },
        [
            {
                "vehicle_id": "VIN123",
                "vehicle_name": "Blue Car",
                "ev_power_kw": 0,
                "is_connected": True,
                "is_charging": False,
            }
        ],
    )

    assert loadpoints[0]["owner"] == "powersync"
    assert loadpoints[0]["owner_mode"] == "solar_surplus"
    assert loadpoints[0]["charger_type"] == "ocpp"
    assert loadpoints[0]["current_amps"] == 16
    assert loadpoints[0]["actual_charging"] is False
    assert loadpoints[0]["status"] == "commanded_no_power"
    assert "Commanded 16A" in loadpoints[0]["blocking_reason"]
    assert loadpoints[0]["confidence"] == "observed"


def test_external_observed_charger_is_kept_without_dynamic_state():
    loadpoints = build_loadpoint_status(
        {},
        [
            {
                "charger_id": "garage_ocpp",
                "vehicle_name": "Garage OCPP",
                "charger_type": "ocpp",
                "ev_power_kw": 7.2,
                "is_connected": True,
                "is_charging": True,
            }
        ],
        {"surplus_kw": 1.0},
    )

    assert loadpoints == [
        {
            "loadpoint_id": "garage_ocpp",
            "vehicle_id": None,
            "vehicle_name": "Garage OCPP",
            "charger_type": "ocpp",
            "connected": True,
            "actual_charging": True,
            "status": "charging",
            "owner": "external",
            "owner_mode": None,
            "source": "grid",
            "current_power_kw": 7.2,
            "commanded_power_kw": None,
            "current_amps": 0,
            "target_amps": 0,
            "soc": None,
            "target_soc": None,
            "allocated_surplus_kw": 0.0,
            "blocking_reason": None,
            "session_id": None,
            "last_command": None,
            "confidence": "observed",
        }
    ]


def test_dynamic_state_prefers_business_owner_mode_over_control_mode():
    loadpoints = build_loadpoint_status(
        {
            "VIN123": {
                "active": True,
                "current_amps": 16,
                "target_amps": 16,
                "charging_started": True,
                "params": {
                    "dynamic_mode": "battery_target",
                    "owner_mode": "price_level_recovery",
                    "voltage": 230,
                    "phases": 1,
                },
            }
        }
    )

    assert loadpoints[0]["owner_mode"] == "price_level_recovery"


def test_loadpoint_status_includes_ownership_last_command():
    loadpoints = build_loadpoint_status(
        {
            "VIN123": {
                "active": True,
                "current_amps": 0,
                "target_amps": 0,
                "params": {"dynamic_mode": "solar_surplus"},
            }
        },
        None,
        None,
        {
            "VIN123": {
                "owner": "powersync",
                "owner_mode": "manual",
                "session_id": "sess-1",
                "last_command": {
                    "command": "start",
                    "at": "2026-05-01T00:00:00+00:00",
                    "source": "powersync",
                    "success": True,
                    "reason": "Manual start",
                },
            }
        },
    )

    assert loadpoints[0]["owner"] == "powersync"
    assert loadpoints[0]["owner_mode"] == "manual"
    assert loadpoints[0]["session_id"] == "sess-1"
    assert loadpoints[0]["last_command"]["command"] == "start"


def test_observed_ocpp_loadpoint_uses_ownership_alias():
    loadpoints = build_loadpoint_status(
        {},
        [
            {
                "charger_id": "garage_ocpp",
                "vehicle_name": "Garage OCPP",
                "charger_type": "ocpp",
                "ev_power_kw": 0.0,
                "is_connected": True,
            }
        ],
        None,
        {
            "ocpp_garage_ocpp": {
                "owner": "powersync",
                "owner_mode": "manual",
                "session_id": "sess-ocpp",
                "last_command": {
                    "command": "start_manual",
                    "at": "2026-05-01T00:00:00+00:00",
                    "source": "powersync",
                    "success": True,
                    "reason": "Manual OCPP start",
                },
            }
        },
    )

    assert loadpoints[0]["loadpoint_id"] == "garage_ocpp"
    assert loadpoints[0]["owner"] == "powersync"
    assert loadpoints[0]["owner_mode"] == "manual"
    assert loadpoints[0]["session_id"] == "sess-ocpp"
    assert loadpoints[0]["last_command"]["command"] == "start_manual"


def test_allocated_surplus_marks_powersync_session_as_solar():
    loadpoints = build_loadpoint_status(
        {
            "solar_car": {
                "active": True,
                "current_amps": 10,
                "target_amps": 10,
                "charging_started": True,
                "allocated_surplus_kw": 2.4,
                "params": {"voltage": 240, "phases": 1},
            }
        },
        None,
        {"surplus_kw": 0.0},
    )

    assert loadpoints[0]["current_power_kw"] == 2.4
    assert loadpoints[0]["status"] == "charging"
    assert loadpoints[0]["source"] == "solar"
    assert loadpoints[0]["confidence"] == "commanded"


def test_dynamic_state_uses_observed_power_for_matched_vehicle():
    loadpoints = build_loadpoint_status(
        {
            "LRW3F7FS1NC484342": {
                "active": True,
                "vehicle_name": "N3bula",
                "current_amps": 10,
                "target_amps": 10,
                "charging_started": True,
                "params": {
                    "dynamic_mode": "solar_surplus",
                    "voltage": 240,
                    "phases": 3,
                },
            }
        },
        [
            {
                "vehicle_id": "LRW3F7FS1NC484342",
                "vehicle_name": "N3bula",
                "charger_type": "tesla",
                "ev_power_kw": 2.4,
                "ev_soc": 78,
                "is_connected": True,
                "is_charging": True,
            }
        ],
    )

    assert loadpoints[0]["current_power_kw"] == 2.4
    assert loadpoints[0]["commanded_power_kw"] == 7.2
    assert loadpoints[0]["status"] == "charging"
    assert loadpoints[0]["confidence"] == "observed"


def test_default_session_merges_with_single_observed_charging_vehicle():
    generic_observation = build_generic_charger_observation(
        vehicle_name="EV",
        switch_state="on",
        amps_value=None,
        status_state="connected",
        soc_value="53",
    )

    loadpoints = build_loadpoint_status(
        {
            "_default": {
                "active": True,
                "vehicle_name": "EV",
                "current_amps": 0,
                "target_amps": 0,
                "params": {"dynamic_mode": "solar_surplus"},
            }
        },
        [
            {
                "vehicle_id": "VIN_TESS",
                "vehicle_name": "Tess",
                "charger_type": "tesla",
                "ev_power_kw": 3.4,
                "ev_soc": 55,
                "is_connected": True,
                "is_charging": True,
            },
            generic_observation,
            {
                "vehicle_id": "VIN_THEO",
                "vehicle_name": "Theo",
                "charger_type": "tesla",
                "ev_power_kw": 0.0,
                "ev_soc": 21,
                "is_connected": False,
                "is_charging": False,
            },
        ],
    )

    assert [loadpoint["vehicle_name"] for loadpoint in loadpoints] == ["Tess", "Theo"]
    assert loadpoints[0]["loadpoint_id"] == "VIN_TESS"
    assert loadpoints[0]["vehicle_id"] == "VIN_TESS"
    assert loadpoints[0]["current_power_kw"] == 3.4
    assert loadpoints[0]["owner"] == "powersync"
    assert loadpoints[0]["owner_mode"] == "solar_surplus"
    assert loadpoints[0]["confidence"] == "observed"


def test_generic_charger_observation_reports_commanded_without_power():
    observation = build_generic_charger_observation(
        vehicle_name="Generic EV",
        switch_state="on",
        amps_value="16",
        status_state="connected",
        soc_value="62",
    )

    loadpoints = build_loadpoint_status({}, [observation])

    assert loadpoints[0]["vehicle_name"] == "Generic EV"
    assert loadpoints[0]["charger_type"] == "generic"
    assert loadpoints[0]["connected"] is True
    assert loadpoints[0]["actual_charging"] is False
    assert loadpoints[0]["status"] == "commanded_no_power"
    assert loadpoints[0]["current_amps"] == 16
    assert loadpoints[0]["soc"] == 62


def test_generic_charger_observation_keeps_configured_idle_loadpoint():
    observation = build_generic_charger_observation(
        switch_state="off",
        amps_value=None,
        status_state="disconnected",
        soc_value=None,
    )

    loadpoints = build_loadpoint_status({}, [observation])

    assert len(loadpoints) == 1
    assert loadpoints[0]["loadpoint_id"] == "generic_ev"
    assert loadpoints[0]["status"] == "idle"
    assert loadpoints[0]["owner"] == "external"
