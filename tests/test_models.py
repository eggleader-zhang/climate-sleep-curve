"""Tests for pure data-model behavior."""

from datetime import datetime, timezone

import pytest

from custom_components.climate_sleep_curve.models import (
    ValidationError,
    make_session,
    recommend_profile,
    validate_controller,
    validate_profile,
)


def profile(hours: int = 4):
    return {
        "name": "Test",
        "duration_minutes": hours * 60,
        "points": [{"offset_minutes": i * 60, "temperature": 26 + i / 2} for i in range(hours)],
    }


def test_profile_validation():
    result = validate_profile(profile())
    assert result["points"][0]["offset_minutes"] == 0
    assert result["interpolation"] == "step"
    assert result["fan_mode_control"] == "none"


def test_profile_validates_fan_curve_points():
    value = profile()
    value["fan_mode_control"] = "curve"
    for index, point in enumerate(value["points"]):
        point["fan_mode"] = "auto" if index == 0 else "low"

    result = validate_profile(value)

    assert [point["fan_mode"] for point in result["points"]] == ["auto", "low", "low", "low"]


@pytest.mark.parametrize("control", ["invalid", 1, None, []])
def test_profile_rejects_invalid_fan_control(control):
    value = profile()
    value["fan_mode_control"] = control
    with pytest.raises(ValidationError) as error:
        validate_profile(value)
    assert error.value.code == "invalid_fan_mode"


def test_fan_curve_requires_every_point_mode():
    value = profile()
    value["fan_mode_control"] = "curve"
    with pytest.raises(ValidationError) as error:
        validate_profile(value)
    assert error.value.code == "invalid_fan_mode"


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(name=""),
    lambda value: value.update(duration_minutes=239),
    lambda value: value["points"][0].update(offset_minutes=1),
    lambda value: value["points"][1].update(offset_minutes=0),
    lambda value: value["points"][0].update(temperature=100),
    lambda value: value["points"][0].update(temperature=float("nan")),
    lambda value: value["points"][0].update(offset_minutes=True),
    lambda value: value.update(duration_minutes=240.5),
])
def test_invalid_profiles(mutation):
    value = profile()
    mutation(value)
    with pytest.raises(ValidationError):
        validate_profile(value)


def test_controller_validation():
    result = validate_controller({
        "name": "Bedroom", "climate_entity_ids": ["climate.bedroom", "climate.study", "climate.bedroom"], "profile_id": "p",
        "automatic_start": {"enabled": True, "time": "23:00:00", "weekdays": [6, 0, 0]},
    }, {"p"})
    assert result["automatic_start"]["weekdays"] == [0, 6]
    assert result["climate_entity_ids"] == ["climate.bedroom", "climate.study"]
    assert result["climate_entity_id"] == "climate.bedroom"


def test_controller_accepts_legacy_single_entity():
    result = validate_controller({
        "name": "Bedroom", "climate_entity_id": "climate.bedroom", "profile_id": "p",
    }, {"p"})
    assert result["climate_entity_ids"] == ["climate.bedroom"]


def test_plural_entity_field_takes_precedence_over_compatibility_alias():
    result = validate_controller({
        "name": "Bedroom",
        "climate_entity_id": "climate.new",
        "climate_entity_ids": ["climate.old", "climate.study"],
        "profile_id": "p",
    }, {"p"})
    assert result["climate_entity_ids"] == ["climate.old", "climate.study"]


@pytest.mark.parametrize("entity_ids", [[], ["sensor.wrong"], ["climate.good", 1]])
def test_controller_rejects_invalid_entity_lists(entity_ids):
    with pytest.raises(ValidationError):
        validate_controller({
            "name": "Bedroom", "climate_entity_ids": entity_ids, "profile_id": "p",
        }, {"p"})


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_controller_rejects_non_boolean_enabled(value):
    with pytest.raises(ValidationError):
        validate_controller({
            "name": "Bedroom", "climate_entity_id": "climate.bedroom", "profile_id": "p",
            "enabled": value,
        }, {"p"})


def test_session_uses_snapshot():
    value = profile()
    value["fan_mode_control"] = "curve"
    for point in value["points"]:
        point["fan_mode"] = "low"
    curve = {"id": "p", **validate_profile(value)}
    session = make_session({"id": "c", "climate_entity_ids": ["climate.bedroom", "climate.study"]}, curve, "manual", datetime.now(timezone.utc))
    curve["points"][0]["temperature"] = 30
    curve["points"][0]["fan_mode"] = "high"
    assert session["profile_snapshot"]["points"][0]["temperature"] == 26
    assert session["profile_snapshot"]["points"][0]["fan_mode"] == "low"
    assert session["climate_entity_ids"] == ["climate.bedroom", "climate.study"]


def test_recommendation_is_deterministic():
    result = recommend_profile(480, 26.5, "comfort")
    assert [point["temperature"] for point in result["points"]] == [26.5, 26.5, 27, 27.5, 28, 28, 27.5, 27]
