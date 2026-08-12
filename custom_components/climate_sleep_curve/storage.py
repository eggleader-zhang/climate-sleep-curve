"""Persistent storage for Climate Sleep Curve."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DEFAULT_SETTINGS, STORE_KEY, STORE_VERSION


class CurveStorage:
    """Versioned Home Assistant Store wrapper."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)

    async def async_load(self) -> dict[str, Any]:
        data = await self._store.async_load()
        if data is None:
            return self.empty()
        if data.get("schema_version") != STORE_VERSION:
            raise ValueError(f"Unsupported store schema {data.get('schema_version')}")
        base = self.empty()
        base.update(data)
        base["settings"] = {**DEFAULT_SETTINGS, **data.get("settings", {})}
        changed = False
        for profile in base.get("profiles", {}).values():
            if "fan_mode_control" not in profile:
                profile["fan_mode_control"] = "none"
                changed = True
        for collection_name in ("controllers", "sessions"):
            for item in base.get(collection_name, {}).values():
                if "climate_entity_ids" not in item and item.get("climate_entity_id"):
                    item["climate_entity_ids"] = [item["climate_entity_id"]]
                    changed = True
                elif item.get("climate_entity_ids") and "climate_entity_id" not in item:
                    item["climate_entity_id"] = item["climate_entity_ids"][0]
                    changed = True
        for session in base.get("sessions", {}).values():
            snapshot = session.get("profile_snapshot")
            if isinstance(snapshot, dict) and "fan_mode_control" not in snapshot:
                snapshot["fan_mode_control"] = "none"
                changed = True
        if changed:
            await self._store.async_save(deepcopy(base))
        return base

    async def async_save(self, data: dict[str, Any]) -> None:
        await self._store.async_save(deepcopy(data))

    @staticmethod
    def empty() -> dict[str, Any]:
        return {
            "schema_version": STORE_VERSION,
            "profiles": {},
            "controllers": {},
            "sessions": {},
            "settings": dict(DEFAULT_SETTINGS),
        }
