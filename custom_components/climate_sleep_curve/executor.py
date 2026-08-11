"""Safe temperature point execution."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Callable

from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.util.unit_conversion import TemperatureConverter

CLIMATE_DOMAIN = "climate"
SERVICE_SET_TEMPERATURE = "set_temperature"
ATTR_TEMPERATURE = "temperature"
ATTR_MIN_TEMP = "min_temp"
ATTR_MAX_TEMP = "max_temp"
ATTR_TEMPERATURE_STEP = "target_temp_step"


def _state_result(state: State | None) -> str | None:
    if state is None or state.state == "unknown":
        return "skipped_unknown"
    if state.state == "unavailable":
        return "skipped_unavailable"
    if state.state == "off":
        return "skipped_off"
    return None


def normalize_temperature(state: State, temperature_c: float) -> tuple[float, float]:
    """Convert Celsius, clamp to entity range, and snap to entity step."""
    unit = state.attributes.get("temperature_unit", UnitOfTemperature.CELSIUS)
    target = temperature_c
    if unit == UnitOfTemperature.FAHRENHEIT:
        target = TemperatureConverter.convert(temperature_c, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT)
    try:
        minimum = float(state.attributes.get(ATTR_MIN_TEMP, target))
        maximum = float(state.attributes.get(ATTR_MAX_TEMP, target))
        step = float(state.attributes.get(ATTR_TEMPERATURE_STEP, 0.5 if unit == UnitOfTemperature.CELSIUS else 1.0))
    except (TypeError, ValueError):
        minimum = maximum = target
        step = 0.5 if unit == UnitOfTemperature.CELSIUS else 1.0
    if not all(map(math.isfinite, (minimum, maximum, step))) or step <= 0 or minimum > maximum:
        minimum = maximum = target
        step = 0.5 if unit == UnitOfTemperature.CELSIUS else 1.0
    clamped = min(maximum, max(minimum, target))
    snapped = minimum + round((clamped - minimum) / step) * step
    return round(min(maximum, max(minimum, snapped)), 3), step


async def async_execute_temperature(
    hass: HomeAssistant,
    entity_id: str,
    temperature_c: float,
    retry_count: int,
    retry_delay: int,
    is_active: Callable[[], bool],
) -> dict[str, Any]:
    """Set temperature only while an existing climate entity is running."""
    attempts = 0
    first_failure = False
    while attempts <= retry_count:
        if not is_active():
            return {"result": "failed", "attempts": attempts, "error": "Session is no longer active"}
        state = hass.states.get(entity_id)
        skipped = _state_result(state)
        if skipped:
            if first_failure and skipped == "skipped_off":
                skipped = "skipped_off_after_failure"
            return {"result": skipped, "attempts": attempts, "error": None}
        assert state is not None
        target, step = normalize_temperature(state, temperature_c)
        current = state.attributes.get(ATTR_TEMPERATURE)
        try:
            unchanged = current is not None and abs(float(current) - target) <= max(0.01, step / 2)
        except (TypeError, ValueError):
            unchanged = False
        if unchanged:
            return {"result": "no_change", "attempts": attempts, "error": None, "applied_temperature": target}
        attempts += 1
        try:
            # Safety invariant: never include hvac_mode and never call a power service.
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: target},
                blocking=True,
            )
            return {"result": "applied", "attempts": attempts, "error": None, "applied_temperature": target}
        except Exception as err:  # Home Assistant service integrations may raise arbitrary errors.
            first_failure = True
            if attempts > retry_count:
                return {"result": "failed", "attempts": attempts, "error": str(err)[:256]}
            await asyncio.sleep(retry_delay)
    raise AssertionError("unreachable")
