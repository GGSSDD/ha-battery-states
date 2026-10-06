"""Limits and alert settings, quiet hours, recent alerts, test message."""
import asyncio
from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from .helpers import (
    LAST_SEEN, OLD, advance, advance_beating, low_alerts, make_twin, make_zigbee_device, setup_bs, stopped_alerts, teach,
)

def at(day, hour, minute=0, second=0):
    """Local time in HA's time zone (read when called: the test HA sets it)."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())  # 5 Oct = Monday


def fresh(hass, value="50", hours_ago=0):
    hass.states.async_set(OLD, value)
    hass.states.async_set(LAST_SEEN, (dt_util.utcnow() - timedelta(hours=hours_ago)).isoformat())


async def ws_client(hass, hass_ws_client):
    client = await hass_ws_client(hass)

    async def ws(**msg):
        await client.send_json_auto_id(msg)
        res = await client.receive_json()
        await hass.async_block_till_done(wait_background_tasks=True)
        return res

    return client, ws


QUIET = {"quiet_hours": True, "quiet_from": "22:00:00", "quiet_to": "07:00:00"}


# ------------------------------------------------------------- limits
async def test_low_limit_setting(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "35")
    entry = await setup_bs(hass, silence=None, low_threshold=30)
    hass.states.async_set(OLD, "28")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert low_alerts(calls) and "dropped to 28%" in low_alerts(calls)[0]
    st = hass.states.get("sensor.battery_states_low_batteries")
    assert st.state == "1" and st.attributes["low_threshold"] == 30
    freezer.move_to(at(6, 19, 59, 59))
    await advance(hass, freezer, 1)
    assert "below 30%" in calls[-1].data["message"]


async def test_minimum_silence_setting_and_live_change(hass: HomeAssistant, freezer) -> None:
    """1.1.0: "Not seen after" is the minimum silence before "not responding"."""
    make_zigbee_device(hass)
    twins = [make_twin(hass, n) for n in (1, 2)]
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, hours_ago=8)
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, [b for b, _ in twins], 3600)  # its model reports at least hourly
    assert mon._mem[OLD].get("not_responding") is None  # 8 h < 12 h minimum
    hass.config_entries.async_update_entry(entry, options={**entry.options, "not_seen_hours": 6})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[OLD]["not_responding"]["source"] == "model" and len(stopped_alerts(calls)) == 1
    assert "while other SNZB-04 devices report at least every 1 hour" in stopped_alerts(calls)[0]


async def test_bad_saved_values_fall_back_to_defaults(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass, "15")
    entry = await setup_bs(hass, low_threshold="x", not_seen_hours=0, reminder_time="25:99", reminder_days="Tue")
    s = entry.runtime_data.settings
    assert (s.low, s.not_seen, f"{s.reminder_time:%H:%M}", s.reminder_days) == (20, timedelta(hours=12), "20:00", (1, 3, 5))


# ------------------------------------------------------------- switches
async def test_alert_switches(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "25", hours_ago=13)
    freezer.move_to(at(6, 19, 0))
    fresh(hass, "25", hours_ago=13)
    entry = await setup_bs(hass, alert_low=False, alert_not_seen=False, reminder=False)
    hass.states.async_set(OLD, "15")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3600)  # past Tuesday 20:00
    mon = entry.runtime_data
    assert calls == [] and mon.log == []
    count, attrs = mon.snapshot()  # the card and the counts still show it
    assert mon._mem[OLD]["not_seen"] is True and attrs["not_responding_count"] == 1 and count == 0


@pytest.mark.parametrize(("days", "start", "fires"), [([0], (5, 8, 29, 59), True), ([0], (6, 8, 29, 59), False), ([], (5, 8, 29, 59), False)])
async def test_reminder_days_and_time(hass: HomeAssistant, freezer, days, start, fires) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(*start))
    fresh(hass, "15")
    await setup_bs(hass, reminder_days=days, reminder_time="08:30:00")
    await advance(hass, freezer, 1)
    reminders = [c for c in calls if c.data["message"].startswith("You still")]
    assert bool(reminders) is fires


# ------------------------------------------------------------- quiet hours
async def test_quiet_hours_held_alert_sent_at_end(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(5, 14, 0))
    fresh(hass)  # last heard 14:00 -> not seen at 02:00
    entry = await setup_bs(hass, **QUIET)
    mon = entry.runtime_data
    await advance(hass, freezer, 12 * 3600 + 5)  # 02:00:05
    assert mon._mem[OLD]["not_seen"] is True and calls == []
    assert mon.log[0]["status"] == "held" and mon.log[0]["note"] == "quiet hours until 07:00"
    freezer.move_to(at(6, 6, 59, 59))
    await advance(hass, freezer, 1)  # 07:00:00
    assert len(stopped_alerts(calls)) == 1
    assert mon.log[0]["status"] == "sent" and mon.log[0]["note"] == "held for quiet hours, sent at 07:00"
    assert mon.log[0]["targets"] == [{"service": "notify.test", "ok": True}]


async def test_quiet_hours_skip_when_no_longer_true(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(5, 14, 0))
    fresh(hass, "25")
    entry = await setup_bs(hass, **QUIET)
    mon = entry.runtime_data
    await advance(hass, freezer, 12 * 3600 + 5)  # 02:00 not seen -> held
    hass.states.async_set(OLD, "15")  # low at 02:00 -> held
    hass.states.async_set(LAST_SEEN, dt_util.utcnow().isoformat())  # it reported
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set(OLD, "100")  # battery replaced at night
    await hass.async_block_till_done(wait_background_tasks=True)
    freezer.move_to(at(6, 6, 59, 59))
    await advance(hass, freezer, 1)
    assert calls == []
    notes = {e["kind"]: (e["status"], e["note"]) for e in mon.log}
    assert notes["not_responding"] == ("skipped", "it is responding again")
    assert notes["low"] == ("skipped", "the battery is above 20 % again")


async def test_quiet_reminder_sent_at_end_with_current_count(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(6, 19, 59, 59))  # Tuesday
    fresh(hass, "15")
    entry = await setup_bs(hass, quiet_hours=True, quiet_from="19:00:00", quiet_to="21:00:00")
    await advance(hass, freezer, 1)  # 20:00 reminder -> held
    assert calls == [] and entry.runtime_data.log[0]["kind"] == "reminder"
    freezer.move_to(at(6, 20, 59, 59))
    await advance(hass, freezer, 1)
    assert [c.data["message"] for c in calls] == [
        "You still have 1 device with the battery level below 20%. Consider replacing or recharging it soon!"
    ]


async def test_turning_quiet_hours_off_sends_held(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(5, 23, 0))
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, **QUIET)
    assert calls == [] and entry.runtime_data.log[0]["status"] == "held"
    hass.config_entries.async_update_entry(entry, options={**entry.options, "quiet_hours": False})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(stopped_alerts(calls)) == 1 and entry.runtime_data.log[0]["status"] == "sent"


async def test_held_survives_restart_and_is_sent_after(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(5, 23, 0))
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, **QUIET)
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(at(6, 9, 0))  # HA was off at 07:00
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(stopped_alerts(calls)) == 1
    assert entry.runtime_data.log[0]["status"] == "sent"


@pytest.mark.parametrize(
    ("start", "end", "now", "inside"),
    [("22:00:00", "07:00:00", (5, 23, 30), True), ("22:00:00", "07:00:00", (6, 6, 59), True),
     ("22:00:00", "07:00:00", (6, 7, 0), False), ("22:00:00", "07:00:00", (5, 21, 59), False),
     ("13:00:00", "15:00:00", (5, 14, 0), True), ("13:00:00", "15:00:00", (5, 15, 0), False)],
)
async def test_quiet_window(hass: HomeAssistant, freezer, start, end, now, inside) -> None:
    async_mock_service(hass, "notify", "test")
    entry = await setup_bs(hass, include_integrations=[], quiet_hours=True, quiet_from=start, quiet_to=end)
    assert entry.runtime_data.in_quiet_hours(at(*now)) is inside


# ------------------------------------------------------------- history and delivery
async def test_history_kept_across_restart_and_capped(hass: HomeAssistant, freezer) -> None:
    async_mock_service(hass, "notify", "test")
    entry = await setup_bs(hass, include_integrations=[])
    for i in range(55):
        entry.runtime_data._alert("reminder", f"message {i}", detail="1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(entry.runtime_data.log) == 50
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    log = entry.runtime_data.log
    assert len(log) == 50 and log[0]["message"] == "message 54" and log[-1]["message"] == "message 5"
    assert all(e["status"] == "sent" for e in log)


async def test_delivery_results(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    ok = async_mock_service(hass, "notify", "ok")

    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("push service rejected the message")

    hass.services.async_register("notify", "broken", broken)
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, notify_service=["notify.ok", "notify.missing", "notify.broken"])
    e = entry.runtime_data.log[0]
    assert len(ok) == 1 and e["status"] == "partly"
    assert e["targets"] == [
        {"service": "notify.ok", "ok": True},
        {"service": "notify.missing", "ok": False, "error": "doesn't exist"},
        {"service": "notify.broken", "ok": False, "error": "push service rejected the message"},
    ]


async def test_no_notify_service(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, notify_service=[])
    e = entry.runtime_data.log[0]
    assert (e["status"], e["note"]) == ("not_sent", "no notify service is set")


async def test_unload_waits_for_sending(hass: HomeAssistant, freezer, hass_storage) -> None:
    make_zigbee_device(hass)
    gate = asyncio.Event()

    async def slow(call: ServiceCall) -> None:
        await gate.wait()

    hass.services.async_register("notify", "slow", slow)
    fresh(hass, "25")
    entry = await setup_bs(hass, notify_service=["notify.slow"])
    hass.states.async_set(OLD, "15")  # low alert now; its message hangs in the service
    for _ in range(3):
        await asyncio.sleep(0)
    assert entry.runtime_data.log[0]["status"] == "sending"
    async def release() -> None:  # the frozen clock stops timers: open after a few loop turns
        for _ in range(5):
            await asyncio.sleep(0)
        gate.set()

    hass.async_create_task(release())
    assert await hass.config_entries.async_unload(entry.entry_id)
    saved = hass_storage[f"battery_states.{entry.entry_id}"]["data"]["log"][0]
    assert saved["status"] == "sent"


async def test_sending_at_shutdown_becomes_unknown(hass: HomeAssistant, freezer, hass_storage) -> None:
    async_mock_service(hass, "notify", "test")
    entry_id = "e1"
    hass_storage[f"battery_states.{entry_id}"] = {
        "version": 1, "key": f"battery_states.{entry_id}",
        "data": {"devices": {}, "log": [{"id": "a", "time": "2026-10-05T00:00:00+00:00", "kind": "test", "status": "sending", "message": "m", "note": "", "targets": []}], "held": []},
    }
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain="battery_states", entry_id=entry_id, data={}, options={"notify_service": [], "devices": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    e = entry.runtime_data.log[0]
    assert (e["status"], e["note"]) == ("unknown", "Home Assistant stopped before the result came back")


# ------------------------------------------------------------- settings page commands
async def test_set_limits_and_alerts_validation(hass: HomeAssistant, freezer, hass_ws_client) -> None:
    async_mock_service(hass, "notify", "test")
    entry = await setup_bs(hass, include_integrations=[])
    _, ws = await ws_client(hass, hass_ws_client)
    assert not (await ws(type="battery_states/set_limits", low_threshold=0, not_seen_hours=12))["success"]
    assert not (await ws(type="battery_states/set_limits", low_threshold=20.5, not_seen_hours=12))["success"]
    assert not (await ws(type="battery_states/set_limits", low_threshold=20, not_seen_hours=169))["success"]
    r = await ws(type="battery_states/set_limits", low_threshold=25.0, not_seen_hours=6)
    assert r["success"] and r["result"]["limits"] == {"low_threshold": 25, "not_seen_hours": 6}
    base = dict(type="battery_states/set_alerts", notify_services=["notify.test"], alert_low=True, alert_not_seen=False,
                reminder=True, reminder_days=["0", "4", "4"], reminder_time="7:05", quiet_hours=True,
                quiet_from="22:00", quiet_to="22:00:00")
    r = await ws(**base)
    assert not r["success"] and r["error"]["message"] == "Quiet hours need different start and end times"
    r = await ws(**{**base, "quiet_to": "06:30"})
    assert r["success"]
    assert r["result"]["alerts"] == {"alert_low": True, "alert_not_seen": False, "reminder": True, "reminder_days": ["0", "4"],
                                     "reminder_time": "07:05:00", "quiet_hours": True, "quiet_from": "22:00:00", "quiet_to": "06:30:00"}
    assert entry.options["reminder_days"] == [0, 4] and entry.runtime_data.settings.alert_not_seen is False


async def test_subscribe_log_sends_updates(hass: HomeAssistant, freezer, hass_ws_client) -> None:
    async_mock_service(hass, "notify", "test")
    await setup_bs(hass, include_integrations=[])
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "battery_states/subscribe_log"})
    assert (await client.receive_json())["success"]
    entry = hass.config_entries.async_entries("battery_states")[0]
    entry.runtime_data._alert("reminder", "m", detail="1")
    statuses = []
    while "sent" not in statuses and len(statuses) < 4:  # "sending", then "sent"
        msg = await client.receive_json()
        assert msg["type"] == "event"
        statuses.append(msg["event"]["log"][0]["status"])
    assert statuses[-1] == "sent"


async def test_alert_while_starting_waits_for_start(hass: HomeAssistant, freezer) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.set_state(CoreState.starting)
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass)
    # 1.1.0: nothing is judged while Home Assistant starts (networks aren't up yet).
    assert calls == [] and entry.runtime_data.log == []
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    e = entry.runtime_data.log[0]
    assert len(stopped_alerts(calls)) == 1 and (e["kind"], e["status"]) == ("not_responding", "sent")


async def test_alert_while_starting_in_quiet_hours_waits_for_quiet_end(hass: HomeAssistant, freezer) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(at(5, 23, 0))
    hass.set_state(CoreState.starting)
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, **QUIET)
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    e = entry.runtime_data.log[0]
    assert calls == [] and (e["status"], e["note"]) == ("held", "quiet hours until 07:00")
    freezer.move_to(at(6, 6, 59, 59))
    await advance(hass, freezer, 1)
    assert len(stopped_alerts(calls)) == 1 and (e["status"], e["note"]) == ("sent", "held for quiet hours, sent at 07:00")


async def test_quiet_end_inside_dst_gap(hass: HomeAssistant, freezer) -> None:
    """Europe/Bucharest skips 03:00-04:00 on 28 Mar 2027: quiet hours ending 03:30 must still end."""
    await hass.config.async_set_time_zone("Europe/Bucharest")
    tz = dt_util.get_default_time_zone()
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(datetime(2027, 3, 27, 13, 0, tzinfo=tz))
    fresh(hass)  # silent from 13:00 -> not seen at 01:00 on the 28th
    entry = await setup_bs(hass, quiet_hours=True, quiet_from="22:00:00", quiet_to="03:30:00")
    await advance(hass, freezer, 12 * 3600 + 5)  # 01:00:05, held
    assert calls == [] and entry.runtime_data.log[0]["status"] == "held"
    for _ in range(4 * 60):  # minute by minute until 05:00 local (clocks jump 03:00 -> 04:00)
        await advance(hass, freezer, 60)
    print("local now", dt_util.now(), "status", entry.runtime_data.log[0]["status"])
    assert len(stopped_alerts(calls)) == 1


async def test_quiet_end_on_autumn_clock_change(hass: HomeAssistant, freezer) -> None:
    """25 Oct 2026, Europe/Bucharest: 03:00-04:00 happens twice; held alerts go out once, at the first 03:30."""
    await hass.config.async_set_time_zone("Europe/Bucharest")
    tz = dt_util.get_default_time_zone()
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(datetime(2026, 10, 24, 13, 0, tzinfo=tz))
    fresh(hass)
    entry = await setup_bs(hass, quiet_hours=True, quiet_from="22:00:00", quiet_to="03:30:00")
    await advance(hass, freezer, 12 * 3600 + 5)  # 01:00:05, held
    sent_at = None
    for _ in range(5 * 60):
        await advance(hass, freezer, 60)
        if calls and sent_at is None:
            sent_at = dt_util.now()
    print("sent at", sent_at)
    assert len(stopped_alerts(calls)) == 1
    assert (sent_at.hour, sent_at.minute, sent_at.utcoffset()) == (3, 30, timedelta(hours=3))



async def test_test_command_is_gone(hass: HomeAssistant, hass_ws_client) -> None:
    async_mock_service(hass, "notify", "test")
    await setup_bs(hass, include_integrations=[])
    _, ws = await ws_client(hass, hass_ws_client)
    res = await ws(type="battery_states/test_notify", notify_services=["notify.test"])
    assert not res["success"] and res["error"]["code"] == "unknown_command"
