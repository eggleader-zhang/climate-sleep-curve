"""Climate Sleep Curve integration."""

from __future__ import annotations

from typing import TypeAlias

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .manager import ClimateSleepCurveManager
from .models import ValidationError

ClimateSleepCurveConfigEntry: TypeAlias = ConfigEntry[ClimateSleepCurveManager]


def get_manager(hass: HomeAssistant) -> ClimateSleepCurveManager:
    """Return the single configured manager."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries or entries[0].runtime_data is None:
        raise ValidationError("not_found", "Climate Sleep Curve is not configured")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register global APIs and services once."""
    from .websocket_api import async_register_websocket_api

    async_register_websocket_api(hass)

    async def handle_start(call: ServiceCall) -> None:
        await get_manager(hass).async_start_session(
            call.data["controller_id"], call.data.get("profile_id"), call.data.get("replace", False)
        )

    async def handle_stop(call: ServiceCall) -> None:
        await get_manager(hass).async_stop_session(call.data["controller_id"])

    async def handle_apply(call: ServiceCall) -> None:
        await get_manager(hass).async_apply_current_point(call.data["controller_id"])

    async def handle_reload(_call: ServiceCall) -> None:
        await get_manager(hass).async_reload()

    hass.services.async_register(DOMAIN, "start", handle_start, schema=vol.Schema({
        vol.Required("controller_id"): cv.string,
        vol.Optional("profile_id"): cv.string,
        vol.Optional("replace", default=False): cv.boolean,
    }))
    hass.services.async_register(DOMAIN, "stop", handle_stop, schema=vol.Schema({vol.Required("controller_id"): cv.string}))
    hass.services.async_register(DOMAIN, "apply_current_point", handle_apply, schema=vol.Schema({vol.Required("controller_id"): cv.string}))
    hass.services.async_register(DOMAIN, "reload", handle_reload, schema=vol.Schema({}))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ClimateSleepCurveConfigEntry) -> bool:
    manager = ClimateSleepCurveManager(hass)
    await manager.async_setup(entry.options)
    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ClimateSleepCurveConfigEntry) -> None:
    """Apply options through a clean reload."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ClimateSleepCurveConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_unload()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry metadata."""
    if entry.version != 1:
        return False
    if entry.minor_version < 1:
        hass.config_entries.async_update_entry(entry, version=1, minor_version=1)
    return True
