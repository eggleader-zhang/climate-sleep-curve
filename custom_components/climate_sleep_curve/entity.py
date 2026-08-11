"""Shared controller entity support."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATED


class ControllerEntity(Entity):
    """Base entity backed by one curve controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager, controller_id: str) -> None:
        self.manager = manager
        self.controller_id = controller_id

    @property
    def controller(self):
        return self.manager.controllers.get(self.controller_id)

    @property
    def available(self) -> bool:
        return self.controller is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.controller_id)},
            name=self.controller["name"] if self.controller else self.controller_id,
            manufacturer="Climate Sleep Curve",
            model="Sleep Curve Controller",
        )

    async def async_added_to_hass(self) -> None:
        def updated(controller_id):
            if controller_id is None or controller_id == self.controller_id:
                if self.controller is None:
                    self.hass.async_create_task(self.async_remove(force_remove=True))
                else:
                    self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, updated))
