"""Config flow for Climate Sleep Curve."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import DEFAULT_SETTINGS, DOMAIN


class ClimateSleepCurveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the integration's single container entry."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Climate Sleep Curve", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ClimateSleepCurveOptionsFlow()


class ClimateSleepCurveOptionsFlow(config_entries.OptionsFlow):
    """Manage small global settings; curves live in the card."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**DEFAULT_SETTINGS, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("history_retention_days", default=current["history_retention_days"]): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
            vol.Required("default_retry_count", default=current["default_retry_count"]): vol.In([0, 1]),
            vol.Required("default_retry_delay_seconds", default=current["default_retry_delay_seconds"]): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
        }))

