"""Tests for EV live-status unit normalization."""

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

from power_sync.automations.live_status import coordinator_data_to_ev_live_status  # noqa: E402


def test_coordinator_data_to_ev_live_status_converts_kw_to_watts():
    live_status = coordinator_data_to_ev_live_status({
        "battery_level": 72,
        "grid_power": 1.25,
        "solar_power": 5.5,
        "battery_power": -2.0,
        "load_power": 3.75,
    })

    assert live_status == {
        "battery_soc": 72,
        "grid_power": 1250.0,
        "solar_power": 5500.0,
        "battery_power": -2000.0,
        "load_power": 3750.0,
    }


def test_coordinator_data_to_ev_live_status_handles_missing_values():
    live_status = coordinator_data_to_ev_live_status({"battery_level": None})

    assert live_status["battery_soc"] is None
    assert live_status["grid_power"] == 0.0
    assert live_status["solar_power"] == 0.0
    assert live_status["battery_power"] == 0.0
    assert live_status["load_power"] == 0.0
