"""Authenticated WebSocket management API."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .models import ValidationError, recommend_profile

_LOGGER = logging.getLogger(__name__)


def _manager(hass: HomeAssistant):
    from . import get_manager
    return get_manager(hass)


def _error(connection, msg, err: Exception) -> None:
    if isinstance(err, ValidationError):
        connection.send_error(msg["id"], err.code, str(err))
        return
    _LOGGER.exception("Unhandled Climate Sleep Curve WebSocket error")
    connection.send_error(msg["id"], "storage_error", "The operation could not be completed")


@websocket_api.websocket_command({vol.Required("type"): "climate_sleep_curve/get_state"})
@callback
def ws_get_state(hass, connection, msg):
    try:
        connection.send_result(msg["id"], _manager(hass).get_state())
    except Exception as err:
        _error(connection, msg, err)


def admin_async(schema):
    """Apply decorators in Home Assistant's expected order."""
    def decorate(func):
        command = websocket_api.websocket_command(schema)(func)
        command = websocket_api.async_response(command)
        return websocket_api.require_admin(command)
    return decorate


@admin_async({vol.Required("type"): "climate_sleep_curve/profile/save", vol.Required("profile"): dict, vol.Optional("expected_revision"): vol.Any(int, None)})
async def ws_profile_save(hass, connection, msg):
    try:
        result = await _manager(hass).async_save_profile(msg["profile"], msg.get("expected_revision"))
        connection.send_result(msg["id"], result)
    except Exception as err:
        _error(connection, msg, err)


@admin_async({vol.Required("type"): "climate_sleep_curve/profile/delete", vol.Required("profile_id"): str, vol.Required("expected_revision"): int})
async def ws_profile_delete(hass, connection, msg):
    try:
        await _manager(hass).async_delete_profile(msg["profile_id"], msg["expected_revision"])
        connection.send_result(msg["id"])
    except Exception as err:
        _error(connection, msg, err)


@admin_async({vol.Required("type"): "climate_sleep_curve/profile/duplicate", vol.Required("profile_id"): str, vol.Required("name"): str})
async def ws_profile_duplicate(hass, connection, msg):
    try:
        connection.send_result(msg["id"], await _manager(hass).async_duplicate_profile(msg["profile_id"], msg["name"]))
    except Exception as err:
        _error(connection, msg, err)


@admin_async({vol.Required("type"): "climate_sleep_curve/controller/save", vol.Required("controller"): dict, vol.Optional("expected_revision"): vol.Any(int, None)})
async def ws_controller_save(hass, connection, msg):
    try:
        result = await _manager(hass).async_save_controller(msg["controller"], msg.get("expected_revision"))
        connection.send_result(msg["id"], result)
    except Exception as err:
        _error(connection, msg, err)


@admin_async({vol.Required("type"): "climate_sleep_curve/controller/delete", vol.Required("controller_id"): str, vol.Required("expected_revision"): int})
async def ws_controller_delete(hass, connection, msg):
    try:
        await _manager(hass).async_delete_controller(msg["controller_id"], msg["expected_revision"])
        connection.send_result(msg["id"])
    except Exception as err:
        _error(connection, msg, err)


@websocket_api.websocket_command({vol.Required("type"): "climate_sleep_curve/session/start", vol.Required("controller_id"): str, vol.Optional("profile_id"): str, vol.Optional("replace", default=False): bool})
@websocket_api.async_response
async def ws_session_start(hass, connection, msg):
    try:
        result = await _manager(hass).async_start_session(msg["controller_id"], msg.get("profile_id"), msg["replace"])
        connection.send_result(msg["id"], result)
    except Exception as err:
        _error(connection, msg, err)


async def _session_action(hass, connection, msg, restart: bool):
    try:
        if restart:
            result = await _manager(hass).async_restart_session(msg["controller_id"])
        else:
            await _manager(hass).async_stop_session(msg["controller_id"])
            result = None
        connection.send_result(msg["id"], result)
    except Exception as err:
        _error(connection, msg, err)


@websocket_api.websocket_command({vol.Required("type"): "climate_sleep_curve/session/stop", vol.Required("controller_id"): str})
@websocket_api.async_response
async def ws_session_stop(hass, connection, msg):
    await _session_action(hass, connection, msg, False)


@websocket_api.websocket_command({vol.Required("type"): "climate_sleep_curve/session/restart", vol.Required("controller_id"): str})
@websocket_api.async_response
async def ws_session_restart(hass, connection, msg):
    await _session_action(hass, connection, msg, True)


@websocket_api.websocket_command({
    vol.Required("type"): "climate_sleep_curve/profile/recommend",
    vol.Required("duration_minutes"): int,
    vol.Required("starting_temperature"): vol.Coerce(float),
    vol.Required("preference"): vol.In(["comfort", "energy_saving", "cooler"]),
})
@callback
def ws_recommend(hass, connection, msg):
    try:
        connection.send_result(msg["id"], recommend_profile(msg["duration_minutes"], msg["starting_temperature"], msg["preference"]))
    except Exception as err:
        _error(connection, msg, err)


@websocket_api.websocket_command({vol.Required("type"): "climate_sleep_curve/subscribe"})
@callback
def ws_subscribe(hass, connection, msg):
    @callback
    def forward(event):
        event_type = event.event_type.removeprefix("climate_sleep_curve_internal_")
        connection.send_event(msg["id"], {"event_type": event_type, **event.data})

    unsubscribers = [
        hass.bus.async_listen(f"climate_sleep_curve_internal_{name}", forward)
        for name in ("profile_created", "profile_updated", "profile_deleted", "controller_updated", "session_started", "point_processed", "session_stopped", "session_completed")
    ]
    @callback
    def unsubscribe() -> None:
        for unsub in unsubscribers:
            unsub()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])


def async_register_websocket_api(hass: HomeAssistant) -> None:
    for command in (
        ws_get_state, ws_profile_save, ws_profile_delete, ws_profile_duplicate, ws_controller_save,
        ws_controller_delete, ws_session_start, ws_session_stop, ws_session_restart, ws_recommend, ws_subscribe,
    ):
        websocket_api.async_register_command(hass, command)
