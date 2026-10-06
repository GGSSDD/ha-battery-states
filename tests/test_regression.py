"""Behaviour that must be the same in v1.0.7 and v1.0.8."""
import logging
from datetime import datetime, timedelta

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry, async_mock_service, mock_restore_cache,
)

from .helpers import LAST_SEEN, OLD, advance, low_alerts, make_zigbee_device, setup_bs, stopped_alerts

CURTAIN = "sensor.lr_drape_l_battery"


def fresh(hass, value="50", hours_ago=0):
    hass.states.async_set(OLD, value)
    hass.states.async_set(LAST_SEEN, (dt_util.utcnow() - timedelta(hours=hours_ago)).isoformat())


def make_curtain(hass):
    sb = MockConfigEntry(domain="switchbot")
    sb.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=sb.entry_id, identifiers={("switchbot", "aa")}, name="LR Drape L",
        manufacturer="SwitchBot", model="WoCurtain",
    )
    er.async_get(hass).async_get_or_create(
        "sensor", "switchbot", "aa_battery", suggested_object_id="lr_drape_l_battery",
        device_id=dev.id, original_device_class="battery", config_entry=sb,
    )


async def restart(hass, entry, freezer, down_seconds):
    assert await hass.config_entries.async_unload(entry.entry_id)
    await advance(hass, freezer, down_seconds)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry.runtime_data


# ------------------------------------------------------------- held readings
async def test_missing_reading_keeps_last_value(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass, "47")
    entry = await setup_bs(hass)
    for s in ("unknown", "unavailable"):
        hass.states.async_set(OLD, s)
        await hass.async_block_till_done(wait_background_tasks=True)
        _, attrs = entry.runtime_data.snapshot()
        assert attrs["devices"][0]["state"] == "47" and attrs["devices"][0]["reading"] is True


async def test_reading_survives_restart(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass, "47")
    entry = await setup_bs(hass)
    hass.states.async_set(OLD, "unknown")
    mon = await restart(hass, entry, freezer, 60)
    _, attrs = mon.snapshot()
    assert attrs["devices"][0]["state"] == "47"


# ------------------------------------------------------------- not seen
async def test_not_seen_after_12h_once_and_clears(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)
    mon = entry.runtime_data
    await advance(hass, freezer, 12 * 3600 - 5)
    assert mon._mem[OLD]["not_seen"] is False
    await advance(hass, freezer, 10)
    assert mon._mem[OLD]["not_seen"] is True and len(stopped_alerts(calls)) == 1
    count, attrs = mon.snapshot()
    assert count == 1 and attrs["devices"][0]["state"] == "0" and attrs["devices"][0]["reading"] is False
    await advance(hass, freezer, 24 * 3600)
    assert len(stopped_alerts(calls)) == 1
    hass.states.async_set(LAST_SEEN, dt_util.utcnow().isoformat())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[OLD]["not_seen"] is False
    count, attrs = mon.snapshot()
    assert count == 0 and attrs["devices"][0]["state"] == "50"


async def test_zigbee2mqtt_down_time_is_credited(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    seen = dt_util.utcnow().isoformat()
    fresh(hass)
    entry = await setup_bs(hass)
    mon = entry.runtime_data
    await advance(hass, freezer, 2 * 3600)
    hass.states.async_set(LAST_SEEN, STATE_UNAVAILABLE)
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 20 * 3600)  # Zigbee2MQTT down 20 h
    assert mon._mem[OLD]["not_seen"] is False
    hass.states.async_set(LAST_SEEN, seen)  # back, the device hasn't spoken since
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 10 * 3600 - 5)  # 2 h + 10 h of hearable silence minus 5 s
    assert mon._mem[OLD]["not_seen"] is False
    await advance(hass, freezer, 10)
    assert mon._mem[OLD]["not_seen"] is True and len(stopped_alerts(calls)) == 1


async def test_ha_down_time_is_credited(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, hours_ago=11)
    entry = await setup_bs(hass)
    mon = await restart(hass, entry, freezer, 5 * 3600)
    assert mon._mem[OLD]["not_seen"] is False
    await advance(hass, freezer, 3600 - 5)
    assert mon._mem[OLD]["not_seen"] is False
    await advance(hass, freezer, 10)
    assert mon._mem[OLD]["not_seen"] is True and len(stopped_alerts(calls)) == 1


async def test_curtain_unavailable_for_not_seen_time(hass: HomeAssistant, freezer) -> None:
    """1.0.12: unavailable counts as not seen only after the not-seen time (12 h)."""
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(CURTAIN, "72")
    entry = await setup_bs(hass, include_integrations=["switchbot"])
    mon = entry.runtime_data
    hass.states.async_set(CURTAIN, "unavailable", {"restored": True})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[CURTAIN]["not_seen"] is False  # restored placeholder: ignored
    hass.states.async_set(CURTAIN, "unknown")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[CURTAIN]["not_seen"] is False
    hass.states.async_set(CURTAIN, "unavailable")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[CURTAIN]["not_seen"] is False and stopped_alerts(calls) == []
    await advance(hass, freezer, 12 * 3600 - 5)
    assert mon._mem[CURTAIN]["not_seen"] is False and stopped_alerts(calls) == []
    await advance(hass, freezer, 10)
    assert mon._mem[CURTAIN]["not_seen"] is True and len(stopped_alerts(calls)) == 1
    hass.states.async_set(CURTAIN, "unknown")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[CURTAIN]["not_seen"] is True  # still no reading: kept
    hass.states.async_set(CURTAIN, "71")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[CURTAIN]["not_seen"] is False and len(stopped_alerts(calls)) == 1


# ------------------------------------------------------------- alerts
async def test_low_alert_once_and_texts(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "25")
    await setup_bs(hass, overrides={OLD: {"name": "Window Contact"}})
    for v in ("15", "14", "100", "19"):
        hass.states.async_set(OLD, v)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert low_alerts(calls) == [
        "The battery level for the device WINDOW CONTACT located in the BEDROOM, with battery type: AAA, has dropped to 15%. Consider replacing soon!",
        "The battery level for the device WINDOW CONTACT located in the BEDROOM, with battery type: AAA, has dropped to 19%. Consider replacing soon!",
    ]
    assert all(c.data["title"] == "Batteries" for c in calls)


async def test_not_seen_text_rechargeable(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, hours_ago=13)
    await setup_bs(hass, overrides={OLD: {"name": "TRVZB", "rechargeable": True}})
    assert stopped_alerts(calls) == [
        "The device TRVZB located in the BEDROOM, with battery type: RECHARGEABLE, has stopped reporting. Its battery may be dead. Consider recharging it soon!"
    ]


@pytest.mark.parametrize(("day", "value", "sent"), [(6, "15", True), (5, "15", False), (6, "50", False), (8, "20", True), (10, "5", True)])
async def test_reminder(hass: HomeAssistant, freezer, day, value, sent) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(datetime(2026, 10, day, 19, 59, 59, tzinfo=dt_util.get_default_time_zone()))
    fresh(hass, value)
    await setup_bs(hass)
    await advance(hass, freezer, 1)
    msgs = [c.data["message"] for c in calls if c.data["message"].startswith("You still")]
    assert msgs == (["You still have 1 device with the battery level below 20%. Consider replacing or recharging it soon!"] if sent else [])


async def test_several_notify_services_and_legacy_string(hass: HomeAssistant, freezer, caplog) -> None:
    make_zigbee_device(hass)
    a = async_mock_service(hass, "notify", "a")
    b = async_mock_service(hass, "notify", "b")
    fresh(hass, hours_ago=13)
    await setup_bs(hass, notify_service=["notify.a", "notify.missing", "notify.b"])
    assert len(a) == 1 and len(b) == 1 and "notify.missing not found" in caplog.text


async def test_legacy_single_string(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    a = async_mock_service(hass, "notify", "a")
    fresh(hass, hours_ago=13)
    await setup_bs(hass, notify_service="notify.a")
    assert len(a) == 1


# ------------------------------------------------------------- list and data
async def test_list_order_exclude_and_counts(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    make_curtain(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass, "10")
    hass.states.async_set(CURTAIN, "72")
    hass.states.async_set("sensor.manual_b", "30")
    hass.states.async_set("sensor.manual_a", "90")
    entry = await setup_bs(
        hass, include_integrations=["mqtt", "switchbot"],
        devices=[{"entity_id": "sensor.manual_b", "name": "B"}, {"entity_id": "sensor.manual_a", "name": "A"}],
        overrides={CURTAIN: {"name": "Left Curtain"}, OLD: {"name": "Window Contact"}},
    )
    mon = entry.runtime_data
    assert [d.name for d in mon.devices] == ["B", "A", "Left Curtain", "Window Contact"]
    count, attrs = mon.snapshot()
    assert count == 1
    assert attrs["battery_low_count"] == [{"AAA": 1}, {"Rechargeable": 0}, {"Unknown": 0}]
    assert set(attrs["devices"][0]) == {"entity_id", "name", "battery_type", "area", "state", "value", "reading", "not_seen"}
    hass.config_entries.async_update_entry(entry, options={**entry.options, "exclude": [CURTAIN]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [d.name for d in mon.devices] == ["B", "A", "Window Contact"]


@pytest.mark.parametrize("expected_lingering_timers", [True])  # HA's device registry clean-up (10 s)
async def test_sensor_and_old_selects_removed(hass: HomeAssistant, freezer) -> None:
    """1.0.12: the card keeps sort / group / filter per user; the old selects go."""
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass, "10")
    entry = MockConfigEntry(domain="battery_states", data={}, options={"notify_service": ["notify.test"], "devices": [], "include_integrations": ["mqtt"]})
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    for key in ("sort_order", "group_by", "filter"):
        ent_reg.async_get_or_create("select", "battery_states", f"{entry.entry_id}_{key}", config_entry=entry, suggested_object_id=f"battery_states_{key}")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    st = hass.states.get("sensor.battery_states_low_batteries")
    assert st.state == "1"
    assert not {"sort_entity", "group_entity", "filter_entity"} & set(st.attributes)
    assert [e for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id) if e.domain == "select"] == []
    assert hass.states.async_entity_ids("select") == []


# ------------------------------------------------------------- settings page commands
async def test_settings_commands(hass: HomeAssistant, freezer, hass_ws_client) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    async_mock_service(hass, "notify", "other")
    fresh(hass, "50")
    hass.states.async_set("sensor.manual_battery", "80")
    entry = await setup_bs(hass)
    client = await hass_ws_client(hass)

    async def ws(**msg):
        await client.send_json_auto_id(msg)
        res = await client.receive_json()
        assert res["success"], res
        await hass.async_block_till_done(wait_background_tasks=True)
        return res["result"]

    r = await ws(type="battery_states/update_battery", entity_id=OLD, name="Window", rechargeable=True)
    assert entry.options["overrides"][OLD] == {"name": "Window", "rechargeable": True}
    assert r["batteries"][0]["shown_type"] == "Rechargeable"
    r = await ws(type="battery_states/add_battery", entity_id=OLD)  # filter-found -> hand-added, keeps settings
    assert entry.options["devices"] == [{"entity_id": OLD, "name": "Window", "rechargeable": True}]
    assert OLD not in entry.options["overrides"]
    r = await ws(type="battery_states/remove_battery", entity_id=OLD)  # filter still finds it
    assert entry.options["devices"] == [] and entry.options["overrides"][OLD]["name"] == "Window"
    assert [b["shown_name"] for b in r["batteries"]] == ["Window"]
    r = await ws(type="battery_states/exclude_battery", entity_id=OLD)
    assert r["batteries"] == [] and entry.options["exclude"] == [OLD]
    r = await ws(type="battery_states/add_battery", entity_id="sensor.manual_battery")
    assert [b["entity_id"] for b in r["batteries"]] == ["sensor.manual_battery"]
    r = await ws(type="battery_states/set_filters", include_integrations=[], exclude=[])
    assert r["filters"]["include_integrations"] == [] and r["found_count"] == 0
    r = await ws(type="battery_states/set_notify", notify_services=["notify.test", " notify.other ", "notify.test"])
    assert entry.options["notify_service"] == ["notify.test", "notify.other"]


async def test_remove_entry_deletes_stores(hass: HomeAssistant, freezer, hass_storage) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert f"battery_states.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert f"battery_states.{entry.entry_id}" not in hass_storage
