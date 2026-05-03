"""Tests for shared OCPP status normalization helpers."""

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

from power_sync.automations.ocpp_status import (  # noqa: E402
    extract_hacs_ocpp_prefix,
    is_ocpp_charging,
    is_ocpp_vehicle_present,
    normalize_ocpp_status,
    should_end_ocpp_session,
)


def test_extracts_status_connector_prefix_before_status_suffix():
    assert extract_hacs_ocpp_prefix("sensor.evse_1_status_connector") == "evse_1"
    assert extract_hacs_ocpp_prefix("sensor.evse_1_status") == "evse_1"
    assert extract_hacs_ocpp_prefix("switch.evse_1_charge_control") == "evse_1"


def test_status_normalization_accepts_hacs_variants():
    assert normalize_ocpp_status("Suspended_EVSE") == "suspendedevse"
    assert normalize_ocpp_status("suspended-ev") == "suspendedev"


def test_vehicle_present_and_charging_use_status_and_power():
    assert is_ocpp_vehicle_present("Preparing") is True
    assert is_ocpp_vehicle_present("available") is False
    assert is_ocpp_vehicle_present("available", power_w=200) is True
    assert is_ocpp_charging("available", power_w=200) is True


def test_finishing_without_power_ends_session():
    assert should_end_ocpp_session("finishing", 0, has_session=True) is True
    assert should_end_ocpp_session("finishing", 120, has_session=True) is False
    assert should_end_ocpp_session("charging", 0, has_session=True) is False
    assert should_end_ocpp_session("available", 0, has_session=True) is True
    assert should_end_ocpp_session("available", 0, has_session=False) is False
