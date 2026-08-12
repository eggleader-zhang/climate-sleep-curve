"""Concurrency and persistence regression tests for the manager."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

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
