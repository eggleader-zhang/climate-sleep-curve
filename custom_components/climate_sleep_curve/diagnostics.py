"""Privacy-preserving diagnostics."""

from __future__ import annotations

from copy import deepcopy
import hashlib

from homeassistant.const import __version__ as HA_VERSION

from .const import VERSION


def _redact(value: str) -> str:
    return f"redacted-{hashlib.sha256(value.encode()).hexdigest()[:8]}"


async def async_get_config_entry_diagnostics(hass, entry):
    manager = entry.runtime_data
    profiles = deepcopy(list(manager.profiles.values()))
    controllers = deepcopy(list(manager.controllers.values()))
    sessions = deepcopy(list(manager.sessions.values()))
    for profile in profiles:
        profile["name"] = "REDACTED"
    for controller in controllers:
        controller["name"] = "REDACTED"
        controller["climate_entity_id"] = _redact(controller["climate_entity_id"])
        controller["climate_entity_ids"] = [_redact(value) for value in controller["climate_entity_ids"]]
    for session in sessions:
        session["climate_entity_id"] = _redact(session["climate_entity_id"])
        session["climate_entity_ids"] = [_redact(value) for value in session["climate_entity_ids"]]
        for result in session.get("last_entity_results") or []:
            result["entity_id"] = _redact(result["entity_id"])
        for point in session.get("processed_points", []):
            for result in point.get("entity_results") or []:
                result["entity_id"] = _redact(result["entity_id"])
        session["profile_snapshot"]["name"] = "REDACTED"
    return {
        "integration_version": VERSION,
        "home_assistant_version": HA_VERSION,
        "profiles": profiles,
        "controllers": controllers,
        "sessions": sessions,
        "scheduled_session_callback_groups": len(manager._session_cancels),
        "automatic_start_callbacks": len(manager._schedule_cancels),
    }

