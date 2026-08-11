"""Restart session button platform."""

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .entity import ControllerEntity
from .platform import setup_controller_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_controller_entities(entry, async_add_entities, RestartSleepCurveButton)


class RestartSleepCurveButton(ControllerEntity, ButtonEntity):
    _attr_translation_key = "restart"
    _attr_icon = "mdi:restart"

    def __init__(self, manager, controller_id):
        super().__init__(manager, controller_id)
        self._attr_unique_id = f"{DOMAIN}_{controller_id}_restart"

    async def async_press(self):
        await self.manager.async_restart_session(self.controller_id)

