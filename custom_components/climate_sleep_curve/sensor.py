"""Status sensor platform."""

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN
from .entity import ControllerEntity
from .platform import setup_controller_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_controller_entities(entry, async_add_entities, SleepCurveStatusSensor)


class SleepCurveStatusSensor(ControllerEntity, SensorEntity):
    _attr_translation_key = "status"
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(self, manager, controller_id):
        super().__init__(manager, controller_id)
        self._attr_unique_id = f"{DOMAIN}_{controller_id}_status"

    @property
    def native_value(self):
        return self.manager.controller_status(self.controller_id)["state"]

    @property
    def extra_state_attributes(self):
        status = self.manager.controller_status(self.controller_id)
        session = status.pop("session", None)
        if not session:
            return status
        return {
            **status,
            "session_id": session["id"],
            "profile_name": session["profile_snapshot"]["name"],
            "started_at": session["started_at"],
            "ends_at": session["ends_at"],
            "last_result": session.get("last_result"),
            "last_error": session.get("last_error"),
        }

