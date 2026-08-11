"""Profile selector platform."""

from homeassistant.components.select import SelectEntity

from .const import DOMAIN
from .entity import ControllerEntity
from .platform import setup_controller_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_controller_entities(entry, async_add_entities, SleepProfileSelect)


class SleepProfileSelect(ControllerEntity, SelectEntity):
    _attr_translation_key = "sleep_profile"
    _attr_icon = "mdi:chart-timeline-variant-shimmer"

    def __init__(self, manager, controller_id):
        super().__init__(manager, controller_id)
        self._attr_unique_id = f"{DOMAIN}_{controller_id}_profile"

    @property
    def options(self):
        return [self._option_for(profile) for profile in self.manager.profiles.values()]

    @property
    def current_option(self):
        if not self.controller:
            return None
        profile = self.manager.profiles.get(self.controller["profile_id"])
        return self._option_for(profile) if profile else None

    async def async_select_option(self, option: str):
        profile = next((item for item in self.manager.profiles.values() if self._option_for(item) == option), None)
        if profile is None:
            raise ValueError("Profile no longer exists")
        await self.manager.async_save_controller(
            {**self.controller, "profile_id": profile["id"]}, self.controller["revision"]
        )

    def _option_for(self, profile):
        duplicates = sum(item["name"] == profile["name"] for item in self.manager.profiles.values())
        return f'{profile["name"]} · {profile["id"][:6]}' if duplicates > 1 else profile["name"]
