"""Diagnostic privacy regression tests."""

from types import SimpleNamespace

import pytest

from custom_components.climate_sleep_curve.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_turn_off_entity_results_are_redacted():
    manager = SimpleNamespace(
        profiles={"p": {"name": "Sleep"}},
        controllers={"c": {
            "name": "Bedroom",
            "climate_entity_id": "climate.bedroom",
            "climate_entity_ids": ["climate.bedroom"],
        }},
        sessions={"s": {
            "climate_entity_id": "climate.bedroom",
            "climate_entity_ids": ["climate.bedroom"],
            "profile_snapshot": {"name": "Sleep"},
            "turn_off_entity_results": [{
                "entity_id": "climate.bedroom",
                "result": "applied",
            }],
        }},
        _session_cancels={},
        _schedule_cancels={},
    )

    result = await async_get_config_entry_diagnostics(None, SimpleNamespace(runtime_data=manager))

    redacted = result["sessions"][0]["turn_off_entity_results"][0]["entity_id"]
    assert redacted.startswith("redacted-")
    assert "climate.bedroom" not in str(result)
