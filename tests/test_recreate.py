"""Zigbee2MQTT-style rename: HA deletes the entities, then they are created again."""
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from .helpers import LAST_SEEN, OLD, advance, make_zigbee_device, setup_bs


def recreate(hass, mqtt, device, new_object_id):
    ent_reg = er.async_get(hass)
    return [
        ent_reg.async_get_or_create(
            "sensor", "mqtt", f"0x01_{kind}", suggested_object_id=f"{new_object_id}_{kind}",
            device_id=device.id, original_device_class=dc, original_name=name, config_entry=mqtt,
        ).entity_id
        for kind, dc, name in (("battery", "battery", None), ("last_seen", "timestamp", "Last seen"))
    ]


async def test_recreated_entity_gets_old_id_back(hass: HomeAssistant) -> None:
    mqtt, device = make_zigbee_device(hass)
    ent_reg = er.async_get(hass)
    old_reg_id = ent_reg.async_get(OLD).id
    for eid in (OLD, LAST_SEEN):
        ent_reg.async_remove(eid)
    ids = recreate(hass, mqtt, device, "zb_guest_window")
    print("re-created as:", ids, "same registry id:", ent_reg.async_get(ids[0]).id == old_reg_id)
    assert ids == [OLD, LAST_SEEN] and ent_reg.async_get(OLD).id == old_reg_id


@pytest.mark.parametrize("gap", [1.0, 2.5, 30.0])
async def test_flagged_device_through_delete_and_recreate(hass: HomeAssistant, freezer, gap) -> None:
    mqtt, device = make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    seen = (dt_util.utcnow() - timedelta(hours=13)).isoformat()
    hass.states.async_set(OLD, "50")
    hass.states.async_set(LAST_SEEN, seen)
    entry = await setup_bs(hass, overrides={OLD: {"name": "Window Contact"}})
    mon = entry.runtime_data
    assert len(calls) == 1
    ent_reg = er.async_get(hass)
    for eid in (OLD, LAST_SEEN):
        ent_reg.async_remove(eid)
        hass.states.async_remove(eid)
    await advance(hass, freezer, gap)
    on_list_during_gap = [d.entity_id for d in mon.devices]
    recreate(hass, mqtt, device, "zb_guest_window")
    hass.states.async_set(OLD, "50")
    hass.states.async_set(LAST_SEEN, seen)
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3)
    stopped = [c for c in calls if "is not responding" in c.data["message"]]
    print(f"gap {gap}s: on list during gap={on_list_during_gap} after={[(d.entity_id, d.name) for d in mon.devices]} not-seen alerts={len(stopped)} flag={mon._mem[OLD]['not_seen']}")
    assert [(d.entity_id, d.name) for d in mon.devices] == [(OLD, "Window Contact")]
    assert len(stopped) == 1
