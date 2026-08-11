"""Active session switch platform."""

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import ControllerEntity
from .platform import setup_controller_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_controller_entities(entry, async_add_entities, SleepCurveSwitch)


class SleepCurveSwitch(ControllerEntity, SwitchEntity):
    _attr_translation_key = "sleep_curve"
    _attr_icon = "mdi:sleep"

    def __init__(self, manager, controller_id):
        super().__init__(manager, controller_id)
        self._attr_unique_id = f"{DOMAIN}_{controller_id}_active"

    @property
    def is_on(self):
        return self.manager.active_session(self.controller_id) is not None

    async def async_turn_on(self, **kwargs):
        await self.manager.async_start_session(self.controller_id)

    async def async_turn_off(self, **kwargs):
        await self.manager.async_stop_session(self.controller_id)

