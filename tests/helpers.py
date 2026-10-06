"""Shared test helpers."""
import inspect
from datetime import timedelta

from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
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


async def setup_bs(hass, silence=12, **options):
    """Set up Battery States.

    `silence`: the test device's own "not responding after" setting (hours), the
    simplest way to have a lone device judged (1.1.0); None = automatic rules.
    """
    opts = {"notify_service": ["notify.test"], "devices": [], "include_integrations": ["mqtt"]}
    opts.update(options)
    if silence is not None:
        overrides = {k: dict(v) for k, v in (opts.get("overrides") or {}).items()}
        overrides.setdefault(OLD, {}).setdefault("silence_hours", silence)
        opts["overrides"] = overrides
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
    """The "not responding" alerts (1.0.x: "stopped reporting")."""
    return [c.data["message"] for c in calls if "is not responding" in c.data["message"]]


# ------------------------------------------------------- networks of twins (1.1.0)
def make_twin(hass, n, *, model="SNZB-04", manufacturer="SONOFF", platform="mqtt", via_device_id=None):
    """Another Zigbee2MQTT-style device (battery + Last seen), by default the
    same model as the test device and on the same network."""
    entry = MockConfigEntry(domain=platform)
    entry.add_to_hass(hass)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    via = {}
    if via_device_id is not None:
        # HA 2026.9 takes the hub's id; older versions (2026.4) its identifiers.
        if "via_device_id" in inspect.signature(dev_reg.async_get_or_create).parameters:
            via = {"via_device_id": via_device_id}
        else:
            via = {"via_device": next(iter(dev_reg.async_get(via_device_id).identifiers))}
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(platform, f"twin_{n}")}, name=f"zb twin {n}",
        manufacturer=manufacturer, model=model, **via,
    )
    battery = ent_reg.async_get_or_create(
        "sensor", platform, f"twin_{n}_battery", suggested_object_id=f"zb_twin_{n}_battery",
        device_id=device.id, original_device_class="battery", config_entry=entry,
    ).entity_id
    last_seen = ent_reg.async_get_or_create(
        "sensor", platform, f"twin_{n}_last_seen", suggested_object_id=f"zb_twin_{n}_last_seen",
        device_id=device.id, original_device_class="timestamp", original_name="Last seen", config_entry=entry,
    ).entity_id
    hass.states.async_set(battery, "80")
    hass.states.async_set(last_seen, dt_util.utcnow().isoformat())
    return battery, last_seen


def beat(hass, last_seen_ids):
    """The twins report now."""
    for eid in last_seen_ids:
        hass.states.async_set(eid, dt_util.utcnow().isoformat())


async def advance_beating(hass, freezer, seconds, last_seen_ids, step=1800):
    """Move time on while the twins keep reporting (every `step` seconds)."""
    left = seconds
    while left > 0:
        chunk = min(step, left)
        await advance(hass, freezer, chunk)
        beat(hass, last_seen_ids)
        await hass.async_block_till_done(wait_background_tasks=True)
        left -= chunk


def teach(mon, battery_ids, seconds):
    """Give batteries a learned normal longest gap (the learning itself is
    tested in test_health.py), then judge everything again."""
    for eid in battery_ids:
        mon._mem_for(eid)["stats"]["base"] = float(seconds)
    mon._evaluate_all()


def low_alerts(calls):
    return [c.data["message"] for c in calls if "has dropped to" in c.data["message"]]
