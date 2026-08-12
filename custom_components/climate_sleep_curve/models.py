"""Serializable models and validation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from typing import Any
from uuid import uuid4

from .const import (
    FAN_MODE_CONTROL_CURVE,
    FAN_MODE_CONTROL_NONE,
    FAN_MODE_CONTROLS,
    MAX_DURATION_MINUTES,
    MAX_FAN_MODE_LENGTH,
    MAX_POINTS,
    MAX_TEMPERATURE_C,
    MIN_DURATION_MINUTES,
    MIN_TEMPERATURE_C,
)


class ValidationError(ValueError):
    """Raised when API data is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RevisionConflict(ValidationError):
    """Raised for an optimistic locking conflict."""

    def __init__(self) -> None:
        super().__init__("revision_conflict", "The object was modified by another client")


def new_id() -> str:
    """Return an opaque sortable-enough identifier without dependencies."""
    return uuid4().hex


def utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse a stored timestamp as aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValidationError("invalid_time", "Timestamp must contain a timezone")
    return parsed.astimezone(timezone.utc)


def validate_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a profile payload."""
    raw_name = data.get("name", "")
    if not isinstance(raw_name, str):
        raise ValidationError("invalid_profile", "Profile name must be a string")
    name = raw_name.strip()
    if not 1 <= len(name) <= 64:
        raise ValidationError("invalid_profile", "Profile name must contain 1 to 64 characters")
    try:
        raw_duration = data["duration_minutes"]
        if isinstance(raw_duration, bool) or not isinstance(raw_duration, int):
            raise TypeError
        duration = raw_duration
    except (KeyError, TypeError, ValueError) as err:
        raise ValidationError("invalid_profile", "Duration must be an integer") from err
    if duration < MIN_DURATION_MINUTES or duration > MAX_DURATION_MINUTES:
        raise ValidationError("invalid_profile", "Duration must be between 240 and 720 minutes")
    interpolation = data.get("interpolation", "step")
    if interpolation != "step":
        raise ValidationError("invalid_profile", "Only step interpolation is supported")
    fan_mode_control = data.get("fan_mode_control", FAN_MODE_CONTROL_NONE)
    if not isinstance(fan_mode_control, str) or fan_mode_control not in FAN_MODE_CONTROLS:
        raise ValidationError("invalid_fan_mode", "Fan mode control must be none, auto, or curve")
    points = data.get("points")
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise ValidationError("invalid_profile", "A profile must contain 2 to 25 points")

    normalized: list[dict[str, float | int | str]] = []
    previous = -1
    for raw in points:
        if not isinstance(raw, dict):
            raise ValidationError("invalid_profile", "Every point must be an object")
        try:
            raw_offset = raw["offset_minutes"]
            raw_temperature = raw["temperature"]
            if (
                isinstance(raw_offset, bool)
                or not isinstance(raw_offset, int)
                or isinstance(raw_temperature, bool)
                or not isinstance(raw_temperature, (int, float))
            ):
                raise TypeError
            offset = raw_offset
            temperature = float(raw_temperature)
        except (KeyError, TypeError, ValueError) as err:
            raise ValidationError("invalid_profile", "Invalid point value") from err
        if offset <= previous:
            raise ValidationError("invalid_point_order", "Point offsets must be strictly increasing")
        if offset < 0 or offset > duration:
            raise ValidationError("invalid_point_order", "Point offset is outside the profile duration")
        if not math.isfinite(temperature) or not MIN_TEMPERATURE_C <= temperature <= MAX_TEMPERATURE_C:
            raise ValidationError("invalid_temperature", "Temperature must be between 5 and 40 °C")
        point: dict[str, float | int | str] = {
            "offset_minutes": offset,
            "temperature": temperature,
        }
        if fan_mode_control == FAN_MODE_CONTROL_CURVE:
            raw_fan_mode = raw.get("fan_mode")
            if not isinstance(raw_fan_mode, str):
                raise ValidationError("invalid_fan_mode", "Every fan curve point must contain a fan mode")
            fan_mode = raw_fan_mode.strip()
            if not 1 <= len(fan_mode) <= MAX_FAN_MODE_LENGTH or not fan_mode.isprintable():
                raise ValidationError("invalid_fan_mode", "Fan modes must contain 1 to 64 printable characters")
            point["fan_mode"] = fan_mode
        normalized.append(point)
        previous = offset
    if normalized[0]["offset_minutes"] != 0:
        raise ValidationError("invalid_point_order", "The first point must start at offset zero")
    return {
        "name": name,
        "duration_minutes": duration,
        "interpolation": interpolation,
        "fan_mode_control": fan_mode_control,
        "points": normalized,
    }


def validate_controller(data: dict[str, Any], profile_ids: set[str]) -> dict[str, Any]:
    """Validate and normalize a controller payload."""
    raw_name = data.get("name", "")
    if not isinstance(raw_name, str):
        raise ValidationError("invalid_controller", "Controller name must be a string")
    name = raw_name.strip()
    if not 1 <= len(name) <= 64:
        raise ValidationError("invalid_controller", "Controller name must contain 1 to 64 characters")
    raw_entity_ids = data.get("climate_entity_ids")
    if raw_entity_ids is None:
        raw_entity_ids = [data.get("climate_entity_id", "")]
    if not isinstance(raw_entity_ids, list) or not 1 <= len(raw_entity_ids) <= 32:
        raise ValidationError("invalid_entity", "One to 32 climate entities are required")
    entity_ids: list[str] = []
    for raw_entity_id in raw_entity_ids:
        if not isinstance(raw_entity_id, str):
            raise ValidationError("invalid_entity", "Every climate entity must be a string")
        entity_id = raw_entity_id.strip()
        if re.fullmatch(r"climate\.[a-z0-9_]+", entity_id) is None:
            raise ValidationError("invalid_entity", "Every entity must use a valid climate entity id")
        if entity_id not in entity_ids:
            entity_ids.append(entity_id)
    profile_id = data.get("profile_id", "")
    if not isinstance(profile_id, str):
        raise ValidationError("not_found", "Profile does not exist")
    if profile_id not in profile_ids:
        raise ValidationError("not_found", "Profile does not exist")
    auto = data.get("automatic_start") or {}
    raw_time = str(auto.get("time", "23:00:00"))
    try:
        datetime.strptime(raw_time, "%H:%M:%S")
    except ValueError as err:
        raise ValidationError("invalid_time", "Automatic start time must use HH:MM:SS") from err
    weekdays = auto.get("weekdays", list(range(7)))
    if not isinstance(weekdays, list) or any(
        isinstance(day, bool) or not isinstance(day, int) or day not in range(7) for day in weekdays
    ):
        raise ValidationError("invalid_weekdays", "Weekdays must contain numbers 0 through 6")
    try:
        catch_up = data.get("catch_up_window_minutes", 0)
        retry_count = data.get("retry_count", 1)
        retry_delay = data.get("retry_delay_seconds", 10)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (catch_up, retry_count, retry_delay)):
            raise TypeError
    except (TypeError, ValueError) as err:
        raise ValidationError("invalid_controller", "Retry and catch-up options must be integers") from err
    if not 0 <= catch_up <= 15 or not 0 <= retry_count <= 1 or not 1 <= retry_delay <= 300:
        raise ValidationError("invalid_controller", "Retry or catch-up option is outside its allowed range")
    enabled = data.get("enabled", True)
    automatic_enabled = auto.get("enabled", False)
    turn_off_after_completion = data.get("turn_off_after_completion", False)
    if (
        not isinstance(enabled, bool)
        or not isinstance(automatic_enabled, bool)
        or not isinstance(turn_off_after_completion, bool)
    ):
        raise ValidationError("invalid_controller", "Enabled options must be boolean values")
    return {
        "name": name,
        "climate_entity_ids": entity_ids,
        # Kept as a compatibility alias for older cards, automations, and diagnostics.
        "climate_entity_id": entity_ids[0],
        "profile_id": profile_id,
        "enabled": enabled,
        "turn_off_after_completion": turn_off_after_completion,
        "automatic_start": {
            "enabled": automatic_enabled,
            "time": raw_time,
            "weekdays": sorted(set(weekdays)),
        },
        "catch_up_window_minutes": catch_up,
        "retry_count": retry_count,
        "retry_delay_seconds": retry_delay,
    }


def make_session(controller: dict[str, Any], profile: dict[str, Any], source: str, started_at: datetime) -> dict[str, Any]:
    """Create a session containing an immutable profile snapshot."""
    started_at = started_at.astimezone(timezone.utc)
    ends_at = started_at.timestamp() + profile["duration_minutes"] * 60
    entity_ids = list(controller.get("climate_entity_ids") or [controller["climate_entity_id"]])
    return {
        "id": new_id(),
        "controller_id": controller["id"],
        "climate_entity_ids": entity_ids,
        "climate_entity_id": entity_ids[0],
        "turn_off_after_completion": bool(controller.get("turn_off_after_completion", False)),
        "turn_off_result": None,
        "turn_off_error": None,
        "turn_off_entity_results": [],
        "profile_snapshot": deepcopy(profile),
        "source": source,
        "status": "running",
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "ends_at": datetime.fromtimestamp(ends_at, timezone.utc).isoformat().replace("+00:00", "Z"),
        "processed_points": [],
        "next_offset_minutes": 0,
        "last_result": None,
        "last_error": None,
        "updated_at": utcnow_iso(),
    }


def recommend_profile(duration_minutes: int, starting_temperature: float, preference: str) -> dict[str, Any]:
    """Build a deterministic, unsaved hourly profile draft."""
    if isinstance(duration_minutes, bool) or duration_minutes < MIN_DURATION_MINUTES or duration_minutes > MAX_DURATION_MINUTES or duration_minutes % 60:
        raise ValidationError("invalid_profile", "Recommended duration must be 4 to 12 whole hours")
    if not math.isfinite(starting_temperature):
        raise ValidationError("invalid_temperature", "Starting temperature must be finite")
    templates = {
        "comfort": [0, 0, .5, 1, 1.5, 1.5, 1, .5],
        "energy_saving": [0, .5, 1, 1.5, 2, 2, 1.5, 1],
        "cooler": [0, 0, .5, .5, 1, 1, .5, 0],
    }
    if preference not in templates:
        raise ValidationError("invalid_profile", "Unknown recommendation preference")
    count = duration_minutes // 60
    template = templates[preference]
    points = []
    for index in range(count):
        source_index = round(index * (len(template) - 1) / max(1, count - 1))
        temperature = min(MAX_TEMPERATURE_C, max(MIN_TEMPERATURE_C, starting_temperature + template[source_index]))
        points.append({"offset_minutes": index * 60, "temperature": round(temperature * 2) / 2})
    return {
        "id": None,
        "name": "Recommended sleep curve",
        "duration_minutes": duration_minutes,
        "interpolation": "step",
        "fan_mode_control": FAN_MODE_CONTROL_NONE,
        "points": points,
        "revision": 0,
    }
