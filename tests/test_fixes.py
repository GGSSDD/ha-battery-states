"""The fixes of v1.0.8 (each one fails on v1.0.7)."""
import asyncio
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.battery_states import library as lib
from .helpers import (
    LAST_SEEN, NEW, OLD, advance, low_alerts, make_zigbee_device, setup_bs, stopped_alerts,
)

ORIG_DOWNLOAD = lib.BatteryLibrary._async_download  # patched to a no-op by conftest after import


def fresh(hass, value="50", hours_ago=0):
    hass.states.async_set(OLD, value)
    hass.states.async_set(LAST_SEEN, (dt_util.utcnow() - timedelta(hours=hours_ago)).isoformat())


# ---------------------------------------------------------- entity ID changed in HA
async def test_rename_keeps_override(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None, overrides={OLD: {"name": "Window Contact", "rechargeable": True}})
    er.async_get(hass).async_update_entity(OLD, new_entity_id=NEW)
    hass.states.async_set(NEW, "50")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3)
    d = entry.runtime_data.devices[0]
    assert (d.entity_id, d.name, d.battery_type) == (NEW, "Window Contact", "Rechargeable")
    assert entry.options["overrides"] == {NEW: {"name": "Window Contact", "rechargeable": True}}


async def test_rename_keeps_hand_added_entry_and_order(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    hass.states.async_set("sensor.other_battery", "80")
    entry = await setup_bs(
        hass, include_integrations=[],
        devices=[{"entity_id": OLD, "name": "Window Contact"}, {"entity_id": "sensor.other_battery"}],
    )
    er.async_get(hass).async_update_entity(OLD, new_entity_id=NEW)
    hass.states.async_set(NEW, "50")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3)
    assert entry.options["devices"] == [{"entity_id": NEW, "name": "Window Contact"}, {"entity_id": "sensor.other_battery"}]
    assert [(d.entity_id, d.name) for d in entry.runtime_data.devices][0] == (NEW, "Window Contact")


async def test_rename_of_flagged_device_sends_no_second_alert(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass)
    assert len(stopped_alerts(calls)) == 1
    er.async_get(hass).async_update_entity(OLD, new_entity_id=NEW)
    hass.states.async_set(NEW, "50")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3)
    assert len(stopped_alerts(calls)) == 1
    assert entry.runtime_data._mem[NEW]["not_seen"] is True


async def test_rename_keeps_exclusion(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, exclude=[OLD])
    er.async_get(hass).async_update_entity(OLD, new_entity_id=NEW)
    await advance(hass, freezer, 3)
    assert entry.runtime_data.devices == [] and entry.options["exclude"] == [NEW]


# ---------------------------------------------------------- nothing runs after unload
async def test_no_resolve_after_unload(hass: HomeAssistant, freezer) -> None:
    mqtt, device = make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)
    mon = entry.runtime_data
    er.async_get(hass).async_update_entity(OLD, name="x")
    await hass.async_block_till_done(wait_background_tasks=True)
    mon.async_apply_options()
    assert await hass.config_entries.async_unload(entry.entry_id)
    er.async_get(hass).async_get_or_create(
        "sensor", "mqtt", "0x02_battery", suggested_object_id="zb_new_battery",
        device_id=device.id, original_device_class="battery", config_entry=mqtt,
    )
    await advance(hass, freezer, 3)
    hass.states.async_set("sensor.zb_new_battery", "30")
    hass.states.async_set("sensor.zb_new_battery", "10")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._track_unsub is None and calls == []


class _SlowLibraryServer:
    def __init__(self, data):
        self.gate = asyncio.Event()
        self.data = data

    def session(self, _hass):
        server = self

        class Resp:
            def raise_for_status(self): ...
            async def text(self):
                await server.gate.wait()
                return json.dumps(server.data)

        class Session:
            async def get(self, url):
                return Resp()

        return Session()


async def test_library_download_cancelled_on_unload(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)
    mon = entry.runtime_data
    server = _SlowLibraryServer({"devices": [{"manufacturer": "SONOFF", "model": "SNZB-04", "battery_type": "CR9999"}]})
    with patch.object(lib, "async_get_clientsession", server.session), \
         patch.object(lib.BatteryLibrary, "_async_download", ORIG_DOWNLOAD):
        mon.library._scheduled_download(None)
        await asyncio.sleep(0)
        assert await hass.config_entries.async_unload(entry.entry_id)
        server.gate.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._track_unsub is None
    assert mon.devices[0].battery_type != "CR9999"


async def test_library_download_still_updates_types(hass: HomeAssistant, freezer) -> None:
    """Regression: while loaded, a downloaded library still changes the types."""
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)
    mon = entry.runtime_data
    server = _SlowLibraryServer({"devices": [{"manufacturer": "SONOFF", "model": "SNZB-04", "battery_type": "CR9999"}]})
    server.gate.set()
    with patch.object(lib, "async_get_clientsession", server.session), \
         patch.object(lib.BatteryLibrary, "_async_download", ORIG_DOWNLOAD):
        mon.library._scheduled_download(None)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert mon.devices[0].battery_type == "CR9999"


# ---------------------------------------------------------- one 20 % rule
@pytest.mark.parametrize(
    ("before", "after", "alert"),
    [("21", "20.4", None), ("21", "20", "dropped to 20%"), ("20.5", "19.6", "dropped to 19%"), ("25", "15", "dropped to 15%")],
)
async def test_low_alert_uses_the_reading(hass: HomeAssistant, freezer, before, after, alert) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, before)
    entry = await setup_bs(hass)
    hass.states.async_set(OLD, after)
    await hass.async_block_till_done(wait_background_tasks=True)
    count, _ = entry.runtime_data.snapshot()
    msgs = low_alerts(calls)
    if alert:
        assert len(msgs) == 1 and alert in msgs[0] and count == 1
    else:
        assert msgs == [] and count == 0


# ---------------------------------------------------------- no area
async def test_alerts_without_area(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass, area=False)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "25", hours_ago=13)
    await setup_bs(hass)
    hass.states.async_set(OLD, "15")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(stopped_alerts(calls)) == 1
    assert stopped_alerts(calls)[0].startswith("The device ZB BEDROOM WINDOW, with battery type: AAA, is not responding: no report since ")
    assert stopped_alerts(calls)[0].endswith(", longer than the 12 hours you set. Last battery level: 25%. Check its battery, the device and its connection.")
    assert low_alerts(calls) == [
        "The battery level for the device ZB BEDROOM WINDOW, with battery type: AAA, "
        "has dropped to 15%. Consider replacing soon!"
    ]


# ---------------------------------------------------------- hand-added entity deleted
async def test_deleted_hand_added_hidden_then_returns(hass: HomeAssistant, freezer) -> None:
    mqtt, device = make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "64")
    entry = await setup_bs(hass, include_integrations=[], devices=[{"entity_id": OLD, "name": "Window Contact"}])
    mon = entry.runtime_data
    ent_reg = er.async_get(hass)
    for eid in (OLD, LAST_SEEN):
        ent_reg.async_remove(eid)
        hass.states.async_remove(eid)
    await advance(hass, freezer, 3)
    count, attrs = mon.snapshot()
    assert mon.devices == [] and attrs["devices"] == [] and count == 0
    assert entry.options["devices"] == [{"entity_id": OLD, "name": "Window Contact"}]
    await advance(hass, freezer, 24 * 3600)
    assert calls == []
    # The same entity comes back (HA gives it its old ID): shown again with its name.
    for kind, dc, name in (("battery", "battery", None), ("last_seen", "timestamp", "Last seen")):
        ent_reg.async_get_or_create("sensor", "mqtt", f"0x01_{kind}", device_id=device.id,
                                    original_device_class=dc, original_name=name, config_entry=mqtt)
    hass.states.async_set(OLD, "unknown")
    hass.states.async_set(LAST_SEEN, dt_util.utcnow().isoformat())
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3)
    _, attrs = mon.snapshot()
    assert [(d["entity_id"], d["name"], d["state"]) for d in attrs["devices"]] == [(OLD, "Window Contact", "64")]
    assert calls == []


async def test_hand_added_entity_without_registry_entry(hass: HomeAssistant, freezer) -> None:
    """A YAML sensor without unique_id has no registry entry: shown once its state exists."""
    async_mock_service(hass, "notify", "test")
    entry = await setup_bs(hass, include_integrations=[], devices=[{"entity_id": "sensor.yaml_battery"}])
    mon = entry.runtime_data
    assert mon.devices == []
    hass.states.async_set("sensor.yaml_battery", "77")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [d.entity_id for d in mon.devices] == ["sensor.yaml_battery"]
    _, attrs = mon.snapshot()
    assert attrs["devices"][0]["state"] == "77"


# ---------------------------------------------------------- disabled Last seen
async def test_disabled_last_seen_reported_behaviour_unchanged(hass: HomeAssistant, freezer, hass_ws_client) -> None:
    make_zigbee_device(hass, last_seen_disabled=True)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(OLD, "50")
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    assert mon.devices[0].last_seen_entity is None  # not used, as before
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "battery_states/get"})
    res = await client.receive_json()
    assert res["result"]["batteries"][0]["last_seen_disabled"] is True
    await advance(hass, freezer, 24 * 3600)
    assert mon._mem[OLD]["not_seen"] is False and calls == []


async def test_enabled_last_seen_preferred(hass: HomeAssistant, freezer) -> None:
    mqtt, device = make_zigbee_device(hass, last_seen_disabled=True)
    er.async_get(hass).async_get_or_create(
        "sensor", "mqtt", "0x01_last_seen_2", suggested_object_id="zb_bedroom_window_last_seen_2",
        device_id=device.id, original_device_class="timestamp", original_name="Last seen", config_entry=mqtt,
    )
    async_mock_service(hass, "notify", "test")
    hass.states.async_set(OLD, "50")
    entry = await setup_bs(hass)
    d = entry.runtime_data.devices[0]
    assert d.last_seen_entity == "sensor.zb_bedroom_window_last_seen_2" and d.last_seen_disabled is False


async def test_registry_less_battery_not_hidden_while_starting(hass: HomeAssistant, freezer) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    async_mock_service(hass, "notify", "test")
    hass.set_state(CoreState.starting)
    entry = await setup_bs(hass, include_integrations=[], devices=[{"entity_id": "sensor.yaml_battery"}])
    mon = entry.runtime_data
    assert [d.entity_id for d in mon.devices] == ["sensor.yaml_battery"]  # still loading: kept
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon.devices == []  # started and still absent: hidden
