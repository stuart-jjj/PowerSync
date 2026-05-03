"""Shared helpers for normalizing OCPP charger status."""

from __future__ import annotations

from typing import Optional


OCPP_CHARGING_STATES = {"charging"}
OCPP_VEHICLE_PRESENT_STATES = {
    "preparing",
    "charging",
    "suspendedev",
    "suspendedevse",
    "finishing",
}
OCPP_HARDWARE_ONLINE_STATES = OCPP_VEHICLE_PRESENT_STATES | {"available", "reserved"}
OCPP_SESSION_ACTIVE_STATES = {
    "preparing",
    "charging",
    "suspendedev",
    "suspendedevse",
}
OCPP_IDLE_STATES = {"available", "unavailable", "unknown", "faulted", "offline", ""}


def normalize_ocpp_status(status: Optional[str]) -> str:
    """Normalize common OCPP/HACS status spelling variants."""
    if status is None:
        return ""
    return str(status).strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def extract_hacs_ocpp_prefix(entity_id: str) -> Optional[str]:
    """Extract the charger prefix from a HACS OCPP entity id."""
    if "." not in entity_id:
        return None
    object_id = entity_id.lower().split(".", 1)[1]
    for suffix in (
        "_status_connector",
        "_status",
        "_availability",
        "_current_power",
        "_energy_meter",
        "_charge_control",
    ):
        if object_id.endswith(suffix):
            return object_id[: -len(suffix)]
    return None


def is_ocpp_charging(status: Optional[str], power_w: float = 0.0) -> bool:
    """Return True when the connector is actively charging."""
    return normalize_ocpp_status(status) in OCPP_CHARGING_STATES or power_w > 50


def is_ocpp_vehicle_present(status: Optional[str], power_w: float = 0.0) -> bool:
    """Return True when a vehicle appears connected to the connector."""
    normalized = normalize_ocpp_status(status)
    return normalized in OCPP_VEHICLE_PRESENT_STATES or power_w > 50


def is_ocpp_hardware_online(status: Optional[str]) -> bool:
    """Return True when charger hardware is reachable, even without a vehicle."""
    return normalize_ocpp_status(status) in OCPP_HARDWARE_ONLINE_STATES


def should_end_ocpp_session(status: Optional[str], power_w: float, has_session: bool) -> bool:
    """Return True when an active OCPP session should be closed."""
    if not has_session:
        return False

    normalized = normalize_ocpp_status(status)
    if power_w > 50:
        return False
    if normalized == "finishing":
        return True
    if normalized not in OCPP_SESSION_ACTIVE_STATES:
        return True
    return False
