"""The Battery States integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import CARD_URL, DOMAIN, PANEL_URL, PANEL_URL_PATH, VERSION
from .library import LIBRARY_STORE_KEY
from .monitor import STORAGE_VERSION, BatteryMonitor

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type BatteryStatesConfigEntry = ConfigEntry[BatteryMonitor]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card (loaded on every dashboard) and the settings page."""
    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(CARD_URL, str(frontend_dir / "battery-states-card.js"), cache_headers=False),
            StaticPathConfig(PANEL_URL, str(frontend_dir / "battery-states-panel.js"), cache_headers=False),
        ]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    # The integration's Configure button opens this page; it has no sidebar entry.
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="battery-states-panel",
        module_url=f"{PANEL_URL}?v={VERSION}",
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BatteryStatesConfigEntry) -> bool:
    """Set up Battery States from a config entry."""
    # Before 1.0.12 the card's sort / group / filter were three select entities,
    # shared by everyone; the card now keeps them per user. Remove the old ones.
    ent_reg = er.async_get(hass)
    for old in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if old.domain == "select":
            ent_reg.async_remove(old.entity_id)
    monitor = BatteryMonitor(hass, entry)
    entry.runtime_data = monitor
    await monitor.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BatteryStatesConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: BatteryStatesConfigEntry) -> None:
    """The integration was deleted: remove its saved readings and library copy."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    # HA still lists the entry being removed at this point, so look for others.
    if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        await Store(hass, 1, LIBRARY_STORE_KEY).async_remove()


async def _async_options_updated(hass: HomeAssistant, entry: BatteryStatesConfigEntry) -> None:
    """Options changed: apply in place (no reload, the card doesn't flicker)."""
    entry.runtime_data.async_apply_options()
