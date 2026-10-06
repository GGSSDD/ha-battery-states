"""Shared test helpers."""
from datetime import timedelta

from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

DOMAIN = "battery_states"
OLD = "sensor.zb_bedroom_window_battery"
NEW = "sensor.zb_bedroom_window_contact_battery"
LAST_SEEN = "sensor.zb_bedroom_window_last_seen"


def make_zigbee_device(hass, *, last_seen_disabled=False, area=True):
    mqtt = MockConfigEntry(domain="mqtt")
    mqtt.add_to_hass(hass)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=mqtt.entry_id,
        identifiers={("mqtt", "zigbee2mqtt_0x01")},
        name="zb bedroom window",
        manufacturer="SONOFF",
        model="SNZB-04",
    )
    if area:
        bedroom = ar.async_get(hass).async_get_or_create("Bedroom")
        dev_reg.async_update_device(device.id, area_id=bedroom.id)
    ent_reg.async_get_or_create(
        "sensor", "mqtt", "0x01_battery", suggested_object_id="zb_bedroom_window_battery",
        device_id=device.id, original_device_class="battery", config_entry=mqtt,
    )
    ent_reg.async_get_or_create(
        "sensor", "mqtt", "0x01_last_seen", suggested_object_id="zb_bedroom_window_last_seen",
        device_id=device.id, original_device_class="timestamp", original_name="Last seen",
        config_entry=mqtt,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION if last_seen_disabled else None,
    )
    return mqtt, device


async def setup_bs(hass, **options):
    opts = {"notify_service": ["notify.test"], "devices": [], "include_integrations": ["mqtt"]}
    opts.update(options)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=opts)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def advance(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)




def stopped_alerts(calls):
    return [c.data["message"] for c in calls if "stopped reporting" in c.data["message"]]


def low_alerts(calls):
    return [c.data["message"] for c in calls if "has dropped to" in c.data["message"]]
