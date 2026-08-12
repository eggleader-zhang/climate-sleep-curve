"""Storage migration tests."""

from unittest.mock import AsyncMock

import pytest

from custom_components.climate_sleep_curve.storage import CurveStorage


@pytest.mark.asyncio
async def test_load_migrates_legacy_entity_fields():
    storage = CurveStorage.__new__(CurveStorage)
    storage._store = AsyncMock()
    storage._store.async_load.return_value = {
        "schema_version": 1,
        "profiles": {"p": {"name": "Legacy", "points": []}},
        "controllers": {"c": {"climate_entity_id": "climate.bedroom"}},
        "sessions": {"s": {
            "climate_entity_id": "climate.bedroom",
            "profile_snapshot": {"name": "Legacy snapshot", "points": []},
        }},
        "settings": {},
    }

    result = await storage.async_load()

    assert result["controllers"]["c"]["climate_entity_ids"] == ["climate.bedroom"]
    assert result["sessions"]["s"]["climate_entity_ids"] == ["climate.bedroom"]
    assert result["profiles"]["p"]["fan_mode_control"] == "none"
    assert result["sessions"]["s"]["profile_snapshot"]["fan_mode_control"] == "none"
    storage._store.async_save.assert_awaited_once()
