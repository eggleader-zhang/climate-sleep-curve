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
        "name": "Bedroom", "climate_entity_id": "climate.bedroom", "profile_id": "p",
        "automatic_start": {"enabled": True, "time": "23:00:00", "weekdays": [6, 0, 0]},
    }, {"p"})
    assert result["automatic_start"]["weekdays"] == [0, 6]


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_controller_rejects_non_boolean_enabled(value):
    with pytest.raises(ValidationError):
        validate_controller({
            "name": "Bedroom", "climate_entity_id": "climate.bedroom", "profile_id": "p",
            "enabled": value,
        }, {"p"})


def test_session_uses_snapshot():
    curve = {"id": "p", **validate_profile(profile())}
    session = make_session({"id": "c", "climate_entity_id": "climate.bedroom"}, curve, "manual", datetime.now(timezone.utc))
    curve["points"][0]["temperature"] = 30
    assert session["profile_snapshot"]["points"][0]["temperature"] == 26


def test_recommendation_is_deterministic():
    result = recommend_profile(480, 26.5, "comfort")
    assert [point["temperature"] for point in result["points"]] == [26.5, 26.5, 27, 27.5, 28, 28, 27.5, 27]
