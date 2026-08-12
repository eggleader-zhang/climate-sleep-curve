"""Safe temperature and fan-mode point execution."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Callable

from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.util.unit_conversion import TemperatureConverter

CLIMATE_DOMAIN = "climate"
SERVICE_SET_TEMPERATURE = "set_temperature"
SERVICE_SET_FAN_MODE = "set_fan_mode"
ATTR_TEMPERATURE = "temperature"
ATTR_FAN_MODE = "fan_mode"
ATTR_FAN_MODES = "fan_modes"
ATTR_MIN_TEMP = "min_temp"
ATTR_MAX_TEMP = "max_temp"
ATTR_TEMPERATURE_STEP = "target_temp_step"
MAX_PARALLEL_TARGETS = 4


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
    default_step = 0.5 if unit == UnitOfTemperature.CELSIUS else 1.0
    try:
        minimum = float(state.attributes.get(ATTR_MIN_TEMP, target))
        maximum = float(state.attributes.get(ATTR_MAX_TEMP, target))
    except (TypeError, ValueError):
        minimum = maximum = target
    if not all(map(math.isfinite, (minimum, maximum))) or minimum > maximum:
        minimum = maximum = target
    try:
        step = float(state.attributes.get(ATTR_TEMPERATURE_STEP, default_step))
    except (TypeError, ValueError):
        step = default_step
    if not math.isfinite(step) or step <= 0:
        step = default_step
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
    *,
    service_limiter: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Set temperature only while an existing climate entity is running."""
    attempts = 0
    first_failure = False
    while attempts <= retry_count:
        acquired = False
        failure: Exception | None = None
        if service_limiter is not None:
            await service_limiter.acquire()
            acquired = True
        try:
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
                failure = err
        finally:
            if acquired:
                service_limiter.release()
        if failure is not None:
            first_failure = True
            if attempts > retry_count:
                return {"result": "failed", "attempts": attempts, "error": str(failure)[:256]}
            await asyncio.sleep(retry_delay)
    raise AssertionError("unreachable")


async def async_execute_fan_mode(
    hass: HomeAssistant,
    entity_id: str,
    fan_mode: str,
    retry_count: int,
    retry_delay: int,
    is_active: Callable[[], bool],
    *,
    service_limiter: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Set a supported fan mode only while an existing climate entity is running."""
    attempts = 0
    first_failure = False
    while attempts <= retry_count:
        acquired = False
        failure: Exception | None = None
        if service_limiter is not None:
            await service_limiter.acquire()
            acquired = True
        try:
            if not is_active():
                return {"result": "failed", "attempts": attempts, "error": "Session is no longer active"}
            state = hass.states.get(entity_id)
            skipped = _state_result(state)
            if skipped:
                if first_failure and skipped == "skipped_off":
                    skipped = "skipped_off_after_failure"
                return {"result": skipped, "attempts": attempts, "error": None}
            assert state is not None
            fan_modes = state.attributes.get(ATTR_FAN_MODES)
            if not isinstance(fan_modes, (list, tuple)) or fan_mode not in fan_modes:
                return {"result": "skipped_unsupported", "attempts": attempts, "error": None}
            if state.attributes.get(ATTR_FAN_MODE) == fan_mode:
                return {
                    "result": "no_change",
                    "attempts": attempts,
                    "error": None,
                    "applied_fan_mode": fan_mode,
                }
            attempts += 1
            try:
                # Safety invariant: only entity_id and fan_mode are allowed here.
                await hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_FAN_MODE,
                    {ATTR_ENTITY_ID: entity_id, ATTR_FAN_MODE: fan_mode},
                    blocking=True,
                )
                return {
                    "result": "applied",
                    "attempts": attempts,
                    "error": None,
                    "applied_fan_mode": fan_mode,
                }
            except Exception as err:  # Home Assistant service integrations may raise arbitrary errors.
                failure = err
        finally:
            if acquired:
                service_limiter.release()
        if failure is not None:
            first_failure = True
            if attempts > retry_count:
                return {"result": "failed", "attempts": attempts, "error": str(failure)[:256]}
            await asyncio.sleep(retry_delay)
    raise AssertionError("unreachable")


def _aggregate_results(entity_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-entity execution results without hiding partial failures."""
    outcomes = [result["result"] for result in entity_results]
    unique_outcomes = set(outcomes)
    if len(unique_outcomes) == 1:
        outcome = outcomes[0]
    elif unique_outcomes & {"failed", "partial_failure"}:
        outcome = "partial_failure"
    elif "applied" in unique_outcomes:
        outcome = "applied"
    elif "no_change" in unique_outcomes:
        outcome = "no_change"
    else:
        outcome = "skipped_mixed"
    errors = [str(result["error"]) for result in entity_results if result.get("error")]
    return {
        "result": outcome,
        "attempts": max((int(result.get("attempts", 0)) for result in entity_results), default=0),
        "error": "; ".join(errors)[:256] or None,
        "entity_results": entity_results,
    }


async def async_execute_temperatures(
    hass: HomeAssistant,
    entity_ids: list[str],
    temperature_c: float,
    retry_count: int,
    retry_delay: int,
    is_active: Callable[[], bool],
) -> dict[str, Any]:
    """Safely apply one point to several independent climate entities."""
    semaphore = asyncio.Semaphore(MAX_PARALLEL_TARGETS)

    raw_results = await asyncio.gather(*(
        async_execute_temperature(
            hass,
            entity_id,
            temperature_c,
            retry_count,
            retry_delay,
            is_active,
            service_limiter=semaphore,
        )
        for entity_id in entity_ids
    ))
    entity_results = [
        {"entity_id": entity_id, **result}
        for entity_id, result in zip(entity_ids, raw_results, strict=True)
    ]
    return _aggregate_results(entity_results)


async def async_execute_climate_targets(
    hass: HomeAssistant,
    entity_ids: list[str],
    temperature_c: float,
    fan_mode: str | None,
    retry_count: int,
    retry_delay: int,
    is_active: Callable[[], bool],
) -> dict[str, Any]:
    """Apply a temperature and optional fan mode to several climate entities."""
    semaphore = asyncio.Semaphore(MAX_PARALLEL_TARGETS)

    async def execute_entity(entity_id: str) -> dict[str, Any]:
        temperature_result = await async_execute_temperature(
            hass,
            entity_id,
            temperature_c,
            retry_count,
            retry_delay,
            is_active,
            service_limiter=semaphore,
        )
        if fan_mode is None:
            return {
                "entity_id": entity_id,
                **temperature_result,
                "temperature_result": temperature_result["result"],
                "fan_result": "not_requested",
            }
        fan_result = await async_execute_fan_mode(
            hass,
            entity_id,
            fan_mode,
            retry_count,
            retry_delay,
            is_active,
            service_limiter=semaphore,
        )
        action_outcomes = {temperature_result["result"], fan_result["result"]}
        failed_outcomes = action_outcomes & {"failed", "partial_failure"}
        if failed_outcomes:
            combined = "failed" if action_outcomes <= failed_outcomes else "partial_failure"
        elif "applied" in action_outcomes:
            combined = "applied"
        elif action_outcomes == {"no_change"}:
            combined = "no_change"
        elif len(action_outcomes) == 1:
            combined = next(iter(action_outcomes))
        else:
            combined = "skipped_mixed"
        errors = [result["error"] for result in (temperature_result, fan_result) if result.get("error")]
        return {
            "entity_id": entity_id,
            "result": combined,
            "attempts": max(temperature_result["attempts"], fan_result["attempts"]),
            "error": "; ".join(errors)[:256] or None,
            "temperature_result": temperature_result["result"],
            "fan_result": fan_result["result"],
            **({"applied_temperature": temperature_result["applied_temperature"]}
               if "applied_temperature" in temperature_result else {}),
            **({"applied_fan_mode": fan_result["applied_fan_mode"]}
               if "applied_fan_mode" in fan_result else {}),
        }

    entity_results = await asyncio.gather(*(execute_entity(entity_id) for entity_id in entity_ids))
    return _aggregate_results(list(entity_results))
