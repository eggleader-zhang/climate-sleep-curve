"""Hard safety regression tests for device service calls."""

from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.const import UnitOfTemperature
from homeassistant.core import State

from custom_components.climate_sleep_curve.executor import async_execute_temperature


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
