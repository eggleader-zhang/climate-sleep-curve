"""Dynamic platform setup helper."""

from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_CONTROLLER_ADDED


def setup_controller_entities(entry, async_add_entities, factory):
    """Add current and future controller entities."""
    manager = entry.runtime_data
    known = set()

    def add(controller_id):
        if controller_id in known:
            return
        known.add(controller_id)
        async_add_entities([factory(manager, controller_id)])

    for controller_id in manager.controllers:
        add(controller_id)
    entry.async_on_unload(async_dispatcher_connect(manager.hass, SIGNAL_CONTROLLER_ADDED, add))

