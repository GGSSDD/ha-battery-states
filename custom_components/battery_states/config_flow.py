"""Config flow for Battery States (settings live on its own page, via Configure)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_DEVICES, CONF_NOTIFY, DOMAIN
from .monitor import notify_choices


class BatteryStatesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Battery States."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose where alerts go; batteries are set on the Configure page."""
        if user_input is not None:
            return self.async_create_entry(
                title="Battery States",
                data={},
                options={CONF_NOTIFY: list(user_input.get(CONF_NOTIFY) or []), CONF_DEVICES: []},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NOTIFY): SelectSelector(
                        SelectSelectorConfig(
                            options=notify_choices(self.hass),
                            custom_value=True,
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
