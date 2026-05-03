"""Regression tests for Solax Modbus entity mapping and Mode1 control."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import types


ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = ROOT / "custom_components" / "power_sync"


def _install_stubs() -> None:
    ha_root = types.ModuleType("homeassistant")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    ha_event = types.ModuleType("homeassistant.helpers.event")
    ha_util = types.ModuleType("homeassistant.util")
    ha_dt = types.ModuleType("homeassistant.util.dt")

    ha_entity_registry.async_get = lambda hass: hass.entity_registry
    ha_entity_registry.async_entries_for_config_entry = (
        lambda registry, entry_id: registry.entries_for(entry_id)
    )
    ha_event.async_call_later = lambda *args, **kwargs: (lambda: None)
    ha_dt.now = lambda *args, **kwargs: datetime(2026, 5, 3, tzinfo=timezone.utc)
    ha_dt.utcnow = lambda *args, **kwargs: datetime(2026, 5, 3, tzinfo=timezone.utc)
    ha_dt.UTC = timezone.utc

    ha_helpers.entity_registry = ha_entity_registry
    ha_helpers.event = ha_event
    ha_util.dt = ha_dt
    ha_root.helpers = ha_helpers
    ha_root.util = ha_util

    sys.modules["homeassistant"] = ha_root
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.entity_registry"] = ha_entity_registry
    sys.modules["homeassistant.helpers.event"] = ha_event
    sys.modules["homeassistant.util"] = ha_util
    sys.modules["homeassistant.util.dt"] = ha_dt

    power_sync = types.ModuleType("power_sync")
    power_sync.__path__ = [str(COMPONENT_ROOT)]
    sys.modules["power_sync"] = power_sync

    inverters = types.ModuleType("power_sync.inverters")
    inverters.__path__ = [str(COMPONENT_ROOT / "inverters")]
    sys.modules["power_sync.inverters"] = inverters


_install_stubs()

from power_sync.inverters.solax_battery import SolaxBatteryController  # noqa: E402


class _FakeState:
    def __init__(self, entity_id: str, state: str = "0", options: list[str] | None = None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = {"options": options or []}


class _FakeStates:
    def __init__(self, states: list[_FakeState]):
        self._states = {state.entity_id: state for state in states}

    def get(self, entity_id: str | None):
        return self._states.get(entity_id or "")

    def async_all(self, domain: str | None = None):
        if domain is None:
            return list(self._states.values())
        prefix = f"{domain}."
        return [state for state in self._states.values() if state.entity_id.startswith(prefix)]


class _FakeServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain: str, service: str, data: dict, blocking: bool = True):
        self.calls.append((domain, service, dict(data)))


class _FakeRegistry:
    def __init__(self, entries: dict[str, list[str]] | None = None):
        self._entries = entries or {}

    def entries_for(self, entry_id: str):
        return [
            SimpleNamespace(entity_id=entity_id)
            for entity_id in self._entries.get(entry_id, [])
        ]


class _FakeHass:
    def __init__(
        self,
        states: list[_FakeState],
        registry_entries: dict[str, list[str]] | None = None,
    ):
        self.states = _FakeStates(states)
        self.services = _FakeServices()
        self.entity_registry = _FakeRegistry(registry_entries)


def _base_states() -> list[_FakeState]:
    return [
        _FakeState("sensor.solax_battery_capacity", "55"),
        _FakeState("sensor.solax_total_battery_power_charge", "120"),
        _FakeState("sensor.solax_measured_power", "0"),
        _FakeState("select.solax_charger_use_mode", "Self Use Mode", ["Self Use Mode"]),
        _FakeState("number.solax_battery_charge_max_current", "25"),
        _FakeState("number.solax_battery_discharge_max_current", "25"),
        _FakeState("number.solax_selfuse_discharge_min_soc", "20"),
    ]


def _mode1_states() -> list[_FakeState]:
    return [
        _FakeState(
            "select.solax_remotecontrol_power_control",
            "Disabled",
            ["Disabled", "Enabled Battery Control", "Enabled Self Use"],
        ),
        _FakeState("number.solax_remotecontrol_active_power", "0"),
        _FakeState("number.solax_inverter_remotecontrol_autorepeat_duration_mode_1_9", "0"),
        _FakeState("button.solax_remotecontrol_trigger", "unknown"),
    ]


def _manual_states() -> list[_FakeState]:
    return [
        _FakeState("select.solax_manual_mode_select", "Stop Charge and Discharge"),
    ]


async def _connect_mode1_controller():
    hass = _FakeHass(_base_states() + _mode1_states())
    controller = SolaxBatteryController(hass, entity_prefix="solax")
    assert await controller.connect()
    return hass, controller


def test_mode1_profile_validates_without_manual_mode_entities():
    hass, controller = asyncio.run(_connect_mode1_controller())

    assert controller._control_profile == "remote_control"
    status = controller.get_status()
    assert status["battery_level"] == 55.0
    assert status["battery_power"] == -0.12
    assert hass.services.calls == []


def test_mode1_profile_preferred_when_manual_mode_also_exists():
    hass = _FakeHass(_base_states() + _mode1_states() + _manual_states())
    controller = SolaxBatteryController(hass, entity_prefix="solax")

    assert asyncio.run(controller.connect())
    assert controller._control_profile == "remote_control"


def test_mode1_force_charge_uses_remotecontrol_entities():
    hass, controller = asyncio.run(_connect_mode1_controller())

    assert asyncio.run(controller.force_charge(duration_minutes=30, power_w=2500))

    assert ("number", "set_value", {
        "entity_id": "number.solax_remotecontrol_active_power",
        "value": 2500,
    }) in hass.services.calls
    assert ("select", "select_option", {
        "entity_id": "select.solax_remotecontrol_power_control",
        "option": "Enabled Battery Control",
    }) in hass.services.calls
    assert ("number", "set_value", {
        "entity_id": "number.solax_inverter_remotecontrol_autorepeat_duration_mode_1_9",
        "value": 1800,
    }) in hass.services.calls
    assert ("button", "press", {
        "entity_id": "button.solax_remotecontrol_trigger",
    }) in hass.services.calls


def test_discovery_prefers_live_state_over_stale_registry_entity():
    hass = _FakeHass(_base_states() + _mode1_states())
    controller = SolaxBatteryController(hass)

    entity_id = controller._resolve_entity_id(
        [
            "sensor.solax_inverter_bms_battery_capacity",
            "sensor.solax_battery_capacity",
        ],
        "sensor",
        ("battery_capacity",),
        legacy_prefix=None,
    )

    assert entity_id == "sensor.solax_battery_capacity"


def test_config_entry_discovery_falls_back_to_live_state_ids():
    hass = _FakeHass(
        _base_states() + _mode1_states(),
        registry_entries={
            "solax-entry": [
                "sensor.solax_inverter_bms_battery_capacity",
                "sensor.solax_inverter_meter_2_measured_power",
            ],
        },
    )
    controller = SolaxBatteryController(hass, solax_entry_id="solax-entry")

    assert asyncio.run(controller.connect())
    assert controller._entity_map["battery_level"] == "sensor.solax_battery_capacity"
    assert controller._entity_map["grid_power"] == "sensor.solax_measured_power"
