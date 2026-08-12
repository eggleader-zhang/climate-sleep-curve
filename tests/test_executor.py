"""Hard safety regression tests for device service calls."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.const import UnitOfTemperature
from homeassistant.core import State

from custom_components.climate_sleep_curve.executor import (
    MAX_PARALLEL_TARGETS,
    async_execute_climate_targets,
    async_execute_fan_mode,
    async_execute_temperature,
    async_execute_temperatures,
)


def hass_with_state(state):
    hass = Mock()
    hass.states.get.return_value = state
    hass.services.async_call = AsyncMock()
    return hass


@pytest.mark.asyncio
@pytest.mark.parametrize("state_value,expected", [
    ("off", "skipped_off"),
    ("unavailable", "skipped_unavailable"),
    ("unknown", "skipped_unknown"),
])
async def test_non_running_entity_never_receives_a_service_call(state_value, expected):
    hass = hass_with_state(State("climate.bedroom", state_value, {"temperature_unit": UnitOfTemperature.CELSIUS}))
    result = await async_execute_temperature(hass, "climate.bedroom", 27, 1, 0, lambda: True)
    assert result["result"] == expected
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_only_safe_set_temperature_payload_is_sent():
    hass = hass_with_state(State("climate.bedroom", "cool", {
        "temperature": 25, "min_temp": 16, "max_temp": 30, "target_temp_step": .5,
        "temperature_unit": UnitOfTemperature.CELSIUS,
    }))
    result = await async_execute_temperature(hass, "climate.bedroom", 27.1, 0, 10, lambda: True)
    assert result["result"] == "applied"
    hass.services.async_call.assert_awaited_once()
    domain, service, data = hass.services.async_call.await_args.args
    assert domain == "climate"
    assert service == "set_temperature"
    assert data == {"entity_id": "climate.bedroom", "temperature": 27.0}
    assert "hvac_mode" not in data


@pytest.mark.asyncio
async def test_same_target_does_not_send_duplicate_command():
    hass = hass_with_state(State("climate.bedroom", "heat", {
        "temperature": 27, "min_temp": 16, "max_temp": 30, "target_temp_step": .5,
        "temperature_unit": UnitOfTemperature.CELSIUS,
    }))
    result = await async_execute_temperature(hass, "climate.bedroom", 27, 1, 0, lambda: True)
    assert result["result"] == "no_change"
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_temperature_step_falls_back_safely():
    hass = hass_with_state(State("climate.bedroom", "cool", {
        "temperature": 25, "min_temp": 16, "max_temp": 30, "target_temp_step": 0,
        "temperature_unit": UnitOfTemperature.CELSIUS,
    }))
    result = await async_execute_temperature(hass, "climate.bedroom", 27.1, 0, 10, lambda: True)
    assert result["result"] == "applied"
    assert hass.services.async_call.await_args.args[2]["temperature"] == 27.0


@pytest.mark.asyncio
async def test_only_safe_set_fan_mode_payload_is_sent():
    hass = hass_with_state(State("climate.bedroom", "cool", {
        "temperature": 25,
        "fan_mode": "high",
        "fan_modes": ["auto", "low", "high"],
    }))

    result = await async_execute_fan_mode(hass, "climate.bedroom", "auto", 0, 10, lambda: True)

    assert result["result"] == "applied"
    hass.services.async_call.assert_awaited_once_with(
        "climate", "set_fan_mode", {"entity_id": "climate.bedroom", "fan_mode": "auto"}, blocking=True
    )
    assert "hvac_mode" not in hass.services.async_call.await_args.args[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("state_value,expected", [
    ("off", "skipped_off"),
    ("unavailable", "skipped_unavailable"),
    ("unknown", "skipped_unknown"),
])
async def test_non_running_entity_never_receives_fan_call(state_value, expected):
    hass = hass_with_state(State("climate.bedroom", state_value, {"fan_modes": ["auto"]}))

    result = await async_execute_fan_mode(hass, "climate.bedroom", "auto", 1, 0, lambda: True)

    assert result["result"] == expected
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_fan_mode_is_skipped_without_service_call():
    hass = hass_with_state(State("climate.bedroom", "cool", {
        "fan_mode": "low", "fan_modes": ["low", "high"],
    }))

    result = await async_execute_fan_mode(hass, "climate.bedroom", "auto", 1, 0, lambda: True)

    assert result["result"] == "skipped_unsupported"
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_fan_retry_rechecks_state_and_stops_when_device_is_off():
    running = State("climate.bedroom", "cool", {"fan_mode": "high", "fan_modes": ["auto", "high"]})
    stopped = State("climate.bedroom", "off", {"fan_mode": "high", "fan_modes": ["auto", "high"]})
    hass = hass_with_state(running)
    hass.states.get.side_effect = [running, stopped]
    hass.services.async_call.side_effect = RuntimeError("temporary failure")

    result = await async_execute_fan_mode(hass, "climate.bedroom", "auto", 1, 0, lambda: True)

    assert result["result"] == "skipped_off_after_failure"
    assert hass.services.async_call.await_count == 1


@pytest.mark.asyncio
async def test_temperature_and_fan_results_are_reported_independently():
    hass = hass_with_state(State("climate.bedroom", "cool", {
        "temperature": 25,
        "min_temp": 16,
        "max_temp": 30,
        "target_temp_step": 0.5,
        "fan_mode": "low",
        "fan_modes": ["auto", "low"],
    }))

    result = await async_execute_climate_targets(
        hass, ["climate.bedroom"], 27, "auto", 0, 10, lambda: True
    )

    assert result["result"] == "applied"
    assert result["entity_results"][0]["temperature_result"] == "applied"
    assert result["entity_results"][0]["fan_result"] == "applied"
    assert [call.args[1] for call in hass.services.async_call.await_args_list] == [
        "set_temperature", "set_fan_mode"
    ]
    assert all(set(call.args[2]) <= {"entity_id", "temperature", "fan_mode"}
               for call in hass.services.async_call.await_args_list)


@pytest.mark.asyncio
async def test_cancellation_prevents_retry():
    hass = hass_with_state(State("climate.bedroom", "cool", {
        "temperature": 25, "min_temp": 16, "max_temp": 30, "target_temp_step": .5,
        "temperature_unit": UnitOfTemperature.CELSIUS,
    }))
    active = True

    async def fail_once(*_args, **_kwargs):
        nonlocal active
        active = False
        raise RuntimeError("temporary failure")

    hass.services.async_call.side_effect = fail_once
    result = await async_execute_temperature(hass, "climate.bedroom", 27, 1, 0, lambda: active)
    assert result["attempts"] == 1
    assert hass.services.async_call.await_count == 1


@pytest.mark.asyncio
async def test_multiple_entities_are_checked_and_executed_independently():
    hass = Mock()
    states = {
        "climate.bedroom": Mock(state="off", attributes={"temperature": 25}),
        "climate.study": Mock(
            state="cool",
            attributes={"temperature": 25, "min_temp": 16, "max_temp": 30, "target_temp_step": 0.5},
        ),
    }
    hass.states.get.side_effect = states.get
    hass.services.async_call = AsyncMock()

    result = await async_execute_temperatures(
        hass, ["climate.bedroom", "climate.study"], 27, 0, 10, lambda: True
    )

    hass.services.async_call.assert_awaited_once_with(
        "climate", "set_temperature", {"entity_id": "climate.study", "temperature": 27.0}, blocking=True
    )
    assert result["result"] == "applied"
    assert result["attempts"] == 1
    assert [item["result"] for item in result["entity_results"]] == ["skipped_off", "applied"]
    assert all("hvac_mode" not in call.args[2] for call in hass.services.async_call.await_args_list)


@pytest.mark.asyncio
async def test_multiple_entity_service_calls_have_a_concurrency_limit():
    hass = Mock()
    hass.states.get.return_value = Mock(
        state="cool",
        attributes={"temperature": 25, "min_temp": 16, "max_temp": 30, "target_temp_step": 0.5},
    )
    active_calls = 0
    peak_calls = 0
    release = asyncio.Event()

    async def block_call(*_args, **_kwargs):
        nonlocal active_calls, peak_calls
        active_calls += 1
        peak_calls = max(peak_calls, active_calls)
        if peak_calls == MAX_PARALLEL_TARGETS:
            release.set()
        await release.wait()
        await asyncio.sleep(0)
        active_calls -= 1

    hass.services.async_call = AsyncMock(side_effect=block_call)
    entity_ids = [f"climate.room_{index}" for index in range(MAX_PARALLEL_TARGETS + 3)]

    result = await async_execute_temperatures(hass, entity_ids, 27, 0, 10, lambda: True)

    assert peak_calls == MAX_PARALLEL_TARGETS
    assert hass.services.async_call.await_count == len(entity_ids)
    assert all(item["result"] == "applied" for item in result["entity_results"])


@pytest.mark.asyncio
async def test_entity_waiting_for_service_slot_rechecks_session_before_calling():
    hass = Mock()
    hass.states.get.return_value = Mock(
        state="cool",
        attributes={"temperature": 25, "min_temp": 16, "max_temp": 30, "target_temp_step": 0.5},
    )
    saturated = asyncio.Event()
    release = asyncio.Event()
    active_calls = 0

    async def block_call(*_args, **_kwargs):
        nonlocal active_calls
        active_calls += 1
        if active_calls == MAX_PARALLEL_TARGETS:
            saturated.set()
        await release.wait()
        active_calls -= 1

    hass.services.async_call = AsyncMock(side_effect=block_call)
    session_active = True
    entity_ids = [f"climate.room_{index}" for index in range(MAX_PARALLEL_TARGETS + 1)]
    task = asyncio.create_task(
        async_execute_temperatures(hass, entity_ids, 27, 0, 10, lambda: session_active)
    )

    await saturated.wait()
    session_active = False
    release.set()
    result = await task

    assert hass.services.async_call.await_count == MAX_PARALLEL_TARGETS
    assert result["entity_results"][-1]["result"] == "failed"
    assert result["entity_results"][-1]["error"] == "Session is no longer active"
