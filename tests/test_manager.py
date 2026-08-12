"""Concurrency and persistence regression tests for the manager."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

import custom_components.climate_sleep_curve.manager as manager_module
from custom_components.climate_sleep_curve.manager import ClimateSleepCurveManager
from custom_components.climate_sleep_curve.models import ValidationError
from custom_components.climate_sleep_curve.storage import CurveStorage


def build_manager():
    hass = Mock()
    hass.data = {}
    hass.config.config_dir = "."
    hass.states.get.return_value = Mock(state="cool", attributes={"supported_features": 1, "temperature": 25})
    hass.bus.async_fire = Mock()
    manager = ClimateSleepCurveManager(hass)
    manager.storage = Mock()
    manager.storage.async_save = AsyncMock()
    manager.data = CurveStorage.empty()
    profile = {
        "id": "profile", "name": "Test", "duration_minutes": 240, "interpolation": "step",
        "points": [
            {"offset_minutes": 0, "temperature": 26},
            {"offset_minutes": 60, "temperature": 26.5},
        ],
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "revision": 1,
    }
    controller = {
        "id": "controller", "name": "Bedroom", "climate_entity_id": "climate.bedroom",
        "climate_entity_ids": ["climate.bedroom"],
        "profile_id": "profile", "enabled": True,
        "turn_off_after_completion": False,
        "automatic_start": {"enabled": False, "time": "23:00:00", "weekdays": list(range(7))},
        "catch_up_window_minutes": 0, "retry_count": 1, "retry_delay_seconds": 10,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "revision": 1,
    }
    manager.profiles[profile["id"]] = profile
    manager.controllers[controller["id"]] = controller
    manager._schedule_session = Mock()
    manager._notify = Mock()
    manager._broadcast = Mock()
    manager.async_process_point = AsyncMock()
    return manager


@pytest.mark.asyncio
async def test_concurrent_start_creates_only_one_active_session():
    manager = build_manager()
    results = await asyncio.gather(
        manager.async_start_session("controller"),
        manager.async_start_session("controller"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    assert len([session for session in manager.sessions.values() if session["status"] == "running"]) == 1


@pytest.mark.asyncio
async def test_failed_profile_save_rolls_back_memory():
    manager = build_manager()
    before = deepcopy(manager.data)
    manager.storage.async_save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await manager.async_save_profile({
            "name": "New", "duration_minutes": 240, "interpolation": "step",
            "points": [{"offset_minutes": 0, "temperature": 26}, {"offset_minutes": 60, "temperature": 27}],
        }, None)
    assert manager.data == before


@pytest.mark.asyncio
async def test_controller_saves_multiple_supported_entities():
    manager = build_manager()
    manager.hass.states.get.side_effect = lambda entity_id: Mock(
        state="cool", attributes={"supported_features": 1, "temperature": 25}
    ) if entity_id in {"climate.bedroom", "climate.study"} else None

    result = await manager.async_save_controller({
        "name": "Both rooms",
        "climate_entity_ids": ["climate.bedroom", "climate.study"],
        "profile_id": "profile",
        "automatic_start": {"enabled": False, "time": "23:00:00", "weekdays": list(range(7))},
    }, None)

    assert result["climate_entity_ids"] == ["climate.bedroom", "climate.study"]
    assert result["climate_entity_id"] == "climate.bedroom"


@pytest.mark.asyncio
async def test_controller_requires_turn_off_support_when_enabled():
    manager = build_manager()
    manager.hass.states.get.return_value = Mock(
        state="cool",
        attributes={
            "supported_features": int(manager_module.ClimateEntityFeature.TARGET_TEMPERATURE),
            "temperature": 25,
        },
    )

    with pytest.raises(ValidationError) as error:
        await manager.async_save_controller({
            **manager.controllers["controller"],
            "turn_off_after_completion": True,
        }, manager.controllers["controller"]["revision"])

    assert error.value.code == "unsupported_turn_off"


@pytest.mark.asyncio
async def test_controller_accepts_turn_off_support_when_enabled():
    manager = build_manager()
    manager.hass.states.get.return_value = Mock(
        state="cool",
        attributes={
            "supported_features": int(
                manager_module.ClimateEntityFeature.TARGET_TEMPERATURE
                | manager_module.ClimateEntityFeature.TURN_OFF
            ),
            "temperature": 25,
        },
    )

    result = await manager.async_save_controller({
        **manager.controllers["controller"],
        "turn_off_after_completion": True,
    }, manager.controllers["controller"]["revision"])

    assert result["turn_off_after_completion"] is True


@pytest.mark.asyncio
async def test_session_snapshots_controller_entity_list():
    manager = build_manager()
    manager.controllers["controller"]["climate_entity_ids"] = ["climate.bedroom", "climate.study"]

    session = await manager.async_start_session("controller")
    manager.controllers["controller"]["climate_entity_ids"] = ["climate.bedroom"]

    assert session["climate_entity_ids"] == ["climate.bedroom", "climate.study"]


@pytest.mark.asyncio
async def test_legacy_card_can_update_singular_entity_alias():
    manager = build_manager()
    manager.hass.states.get.side_effect = lambda entity_id: Mock(
        state="cool", attributes={"supported_features": 1, "temperature": 25}
    ) if entity_id == "climate.study" else None

    result = await manager.async_save_controller({
        **manager.controllers["controller"],
        "climate_entity_id": "climate.study",
    }, manager.controllers["controller"]["revision"])

    assert result["climate_entity_ids"] == ["climate.study"]


@pytest.mark.asyncio
async def test_new_card_plural_entity_update_is_not_overridden_by_stale_alias():
    manager = build_manager()
    manager.hass.states.get.side_effect = lambda entity_id: Mock(
        state="cool", attributes={"supported_features": 1, "temperature": 25}
    ) if entity_id in {"climate.study", "climate.office"} else None

    result = await manager.async_save_controller({
        **manager.controllers["controller"],
        "climate_entity_ids": ["climate.study", "climate.office"],
    }, manager.controllers["controller"]["revision"])

    assert result["climate_entity_ids"] == ["climate.study", "climate.office"]
    assert result["climate_entity_id"] == "climate.study"


@pytest.mark.asyncio
async def test_plural_only_controller_update_does_not_require_compatibility_alias():
    manager = build_manager()
    payload = deepcopy(manager.controllers["controller"])
    del payload["climate_entity_id"]

    result = await manager.async_save_controller(
        payload, manager.controllers["controller"]["revision"]
    )

    assert result["climate_entity_ids"] == ["climate.bedroom"]
    assert result["climate_entity_id"] == "climate.bedroom"


@pytest.mark.asyncio
async def test_point_execution_uses_snapshot_fan_curve_and_records_results(monkeypatch):
    manager = build_manager()
    profile = deepcopy(manager.profiles["profile"])
    profile["fan_mode_control"] = "curve"
    profile["points"][0]["fan_mode"] = "low"
    session = manager_module.make_session(
        manager.controllers["controller"], profile, "manual", manager_module.dt_util.utcnow()
    )
    manager.sessions[session["id"]] = session
    execute = AsyncMock(return_value={
        "result": "applied",
        "attempts": 1,
        "error": None,
        "entity_results": [{
            "entity_id": "climate.bedroom",
            "result": "applied",
            "temperature_result": "applied",
            "fan_result": "applied",
        }],
    })
    monkeypatch.setattr(manager_module, "async_execute_climate_targets", execute)

    await manager._execute(session, session["profile_snapshot"]["points"][0], record=True)

    assert execute.await_args.args[3] == "low"
    assert session["processed_points"][0]["target_fan_mode"] == "low"
    assert session["last_entity_results"][0]["fan_result"] == "applied"


def test_auto_fan_target_is_resolved_for_every_point():
    assert manager_module._target_fan_mode(
        {"fan_mode_control": "auto"}, {"offset_minutes": 120, "temperature": 27}
    ) == "auto"


@pytest.mark.asyncio
async def test_natural_completion_uses_session_power_off_snapshot(monkeypatch):
    manager = build_manager()
    manager.controllers["controller"]["turn_off_after_completion"] = True
    await manager.async_start_session("controller")
    session = manager.active_session("controller")
    manager.controllers["controller"]["turn_off_after_completion"] = False
    turn_off = AsyncMock(return_value={
        "result": "applied",
        "attempts": 1,
        "error": None,
        "entity_results": [{"entity_id": "climate.bedroom", "result": "applied", "attempts": 1, "error": None}],
    })
    monkeypatch.setattr(manager_module, "async_turn_off_climates", turn_off)

    await manager._async_finish(session, "completed")

    turn_off.assert_awaited_once()
    assert turn_off.await_args.args[1] == ["climate.bedroom"]
    assert session["status"] == "completed"
    assert session["turn_off_result"] == "applied"


@pytest.mark.asyncio
async def test_natural_completion_does_not_turn_off_when_disabled(monkeypatch):
    manager = build_manager()
    await manager.async_start_session("controller")
    session = manager.active_session("controller")
    turn_off = AsyncMock()
    monkeypatch.setattr(manager_module, "async_turn_off_climates", turn_off)

    await manager._async_finish(session, "completed")

    turn_off.assert_not_awaited()
    assert session["status"] == "completed"


@pytest.mark.asyncio
async def test_manual_stop_never_turns_off_even_when_enabled(monkeypatch):
    manager = build_manager()
    manager.controllers["controller"]["turn_off_after_completion"] = True
    await manager.async_start_session("controller")
    turn_off = AsyncMock()
    monkeypatch.setattr(manager_module, "async_turn_off_climates", turn_off)

    await manager.async_stop_session("controller")

    turn_off.assert_not_awaited()
    assert manager.active_session("controller") is None


@pytest.mark.asyncio
async def test_replace_delete_and_unload_never_turn_off(monkeypatch):
    turn_off = AsyncMock()
    monkeypatch.setattr(manager_module, "async_turn_off_climates", turn_off)

    replaced = build_manager()
    replaced.controllers["controller"]["turn_off_after_completion"] = True
    await replaced.async_start_session("controller")
    await replaced.async_start_session("controller", replace=True)

    deleted = build_manager()
    deleted.controllers["controller"]["turn_off_after_completion"] = True
    await deleted.async_start_session("controller")
    await deleted.async_delete_controller("controller", 1)

    unloaded = build_manager()
    unloaded.controllers["controller"]["turn_off_after_completion"] = True
    await unloaded.async_start_session("controller")
    await unloaded.async_unload()

    turn_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_expiry_never_replays_power_off(monkeypatch):
    manager = build_manager()
    manager.controllers["controller"]["turn_off_after_completion"] = True
    await manager.async_start_session("controller")
    session = manager.active_session("controller")
    session["ends_at"] = "2020-01-01T00:00:00Z"
    turn_off = AsyncMock()
    monkeypatch.setattr(manager_module, "async_turn_off_climates", turn_off)

    await manager._async_restore_sessions()

    turn_off.assert_not_awaited()
    assert session["status"] == "completed_after_restart"
