"""1.1.0 "not responding" detection, end to end."""
from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from .helpers import (
    LAST_SEEN, OLD, advance, advance_beating, beat, make_twin, make_zigbee_device, setup_bs,
    stopped_alerts, teach,
)
from .test_regression import CURTAIN, CURTAIN_R, fresh, make_curtain, make_curtains

H = 3600


def twins_of(hass, n=3, **kw):
    made = [make_twin(hass, i, **kw) for i in range(1, n + 1)]
    return [b for b, _ in made], [ls for _, ls in made]


# ------------------------------------------------------------- what's normal
async def test_judged_by_its_model(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    batteries, beats = twins_of(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, batteries, 2 * H)  # its model: at least every 2 h -> limit max(12 h, 8 h)
    diag = mon.diagnostics(OLD)
    assert (diag["judged"], diag["source"], diag["twins"], diag["network"]) == (True, "model", 3, "ok")
    assert diag["threshold"] == 12 * H and diag["model"] == "SNZB-04"
    await advance_beating(hass, freezer, 12 * H - 60, beats)
    assert mon._mem[OLD].get("not_responding") is None
    await advance_beating(hass, freezer, 120, beats, step=60)
    assert mon._mem[OLD]["not_responding"]["source"] == "model"
    assert len(stopped_alerts(calls)) == 1
    assert "while other SNZB-04 devices report at least every 2 hours" in stopped_alerts(calls)[0]
    assert "Last battery level: 50%" in stopped_alerts(calls)[0]


async def test_its_own_steady_rhythm_wins_over_its_model(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    batteries, beats = twins_of(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, batteries, H)
    teach(mon, [OLD], 6 * H)  # set up to report every 6 h: limit 24 h, not 12 h
    await advance_beating(hass, freezer, 23 * H, beats)
    assert mon._mem[OLD].get("not_responding") is None
    await advance_beating(hass, freezer, 2 * H, beats)
    assert mon._mem[OLD]["not_responding"]["source"] == "own"
    assert "while it usually reports at least every 6 hours" in stopped_alerts(calls)[0]


async def test_unknown_rhythm_is_not_judged(hass: HomeAssistant, freezer) -> None:
    """No setting, no steady history, fewer than 2 steady twins: never flagged."""
    make_zigbee_device(hass)
    batteries, beats = twins_of(hass, n=1)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, batteries, H)  # one twin isn't enough
    await advance_beating(hass, freezer, 3 * 24 * H, beats, step=6 * H)
    diag = mon.diagnostics(OLD)
    assert mon._mem[OLD].get("not_responding") is None and calls == []
    assert (diag["judged"], diag["reason"], diag["twins"]) == (False, "learning", 1)


async def test_alone_on_its_network_is_not_judged(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, [OLD], H)
    await advance(hass, freezer, 5 * 24 * H)
    diag = mon.diagnostics(OLD)
    assert mon._mem[OLD].get("not_responding") is None and calls == []
    assert (diag["judged"], diag["reason"], diag["network"]) == (False, "alone", "alone")


# ------------------------------------------------------------- networks
async def test_network_down_flags_nobody_and_its_outage_does_not_count(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    batteries, beats = twins_of(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, batteries, H)
    await advance(hass, freezer, 14 * H)  # the whole network silent (coordinator down)
    assert mon._mem[OLD].get("not_responding") is None and calls == []
    assert mon.diagnostics(OLD)["network"] == "down"
    beat(hass, beats)  # the network is back; the test device stays silent
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[OLD].get("not_responding") is None  # those 14 h were the network's
    await advance_beating(hass, freezer, 11 * H, beats)
    assert mon._mem[OLD].get("not_responding") is None
    await advance_beating(hass, freezer, 2 * H, beats)
    assert mon._mem[OLD]["not_responding"] and len(stopped_alerts(calls)) == 1


async def test_two_bridges_are_separate_networks(hass: HomeAssistant, freezer) -> None:
    """Twins on bridge A keep working; bridge B is down: its devices aren't blamed."""
    dev_reg = dr.async_get(hass)
    hub = MockConfigEntry(domain="mqtt")
    hub.add_to_hass(hass)
    bridge_a = dev_reg.async_get_or_create(config_entry_id=hub.entry_id, identifiers={("mqtt", "bridge_a")}, name="Bridge A")
    bridge_b = dev_reg.async_get_or_create(config_entry_id=hub.entry_id, identifiers={("mqtt", "bridge_b")}, name="Bridge B")
    a1, a1_ls = make_twin(hass, "a1", via_device_id=bridge_a.id)
    a2, a2_ls = make_twin(hass, "a2", via_device_id=bridge_a.id)
    b1, b1_ls = make_twin(hass, "b1", via_device_id=bridge_b.id)
    b2, b2_ls = make_twin(hass, "b2", via_device_id=bridge_b.id)
    calls = async_mock_service(hass, "notify", "test")
    entry = await setup_bs(hass, silence=None)
    mon = entry.runtime_data
    teach(mon, [a1, a2, b2], H)
    await advance_beating(hass, freezer, 14 * H, [a1_ls, a2_ls])  # bridge B silent
    assert mon._mem[b1].get("not_responding") is None and calls == []
    assert mon.diagnostics(b1)["network"] == "down" and mon.diagnostics(a1)["network"] == "ok"
    await advance_beating(hass, freezer, 13 * H, [a1_ls, a2_ls, b2_ls])  # bridge B back, b1 silent
    assert mon._mem[b1]["not_responding"] and len(stopped_alerts(calls)) == 1


async def test_curtains_both_unavailable_is_the_network(hass: HomeAssistant, freezer) -> None:
    make_curtains(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(CURTAIN, "72")
    entry = await setup_bs(hass, silence=None, include_integrations=["switchbot"])
    mon = entry.runtime_data
    hass.states.async_set(CURTAIN, STATE_UNAVAILABLE)
    hass.states.async_set(CURTAIN_R, STATE_UNAVAILABLE)
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 13 * H)
    assert not mon.not_responding() and calls == []
    hass.states.async_set(CURTAIN_R, "60")  # Bluetooth back: one curtain answers
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not mon.not_responding()
    await advance(hass, freezer, 12 * H + 5)
    assert mon.not_responding() == [CURTAIN] and len(stopped_alerts(calls)) == 1
    assert "unavailable since" in stopped_alerts(calls)[0]
    assert "while other devices on its network work" in stopped_alerts(calls)[0]


# ------------------------------------------------------------- watched by activity
async def test_activity_with_own_setting(hass: HomeAssistant, freezer) -> None:
    """No Last seen sensor, never unavailable (a sleepy sensor): with a setting,
    any update counts as a report - also a value written again unchanged."""
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(CURTAIN, "72")
    entry = await setup_bs(hass, silence=None, include_integrations=["switchbot"],
                           overrides={CURTAIN: {"silence_hours": 6}})
    mon = entry.runtime_data
    assert mon.devices[0].mode == "activity"
    for _ in range(5):
        await advance(hass, freezer, 2 * H)
        hass.states.async_set(CURTAIN, "72")  # the same value again: a report
        await hass.async_block_till_done(wait_background_tasks=True)
    assert not mon.not_responding()
    await advance(hass, freezer, 6 * H + 5)
    assert mon.not_responding() == [CURTAIN]
    assert "longer than the 6 hours you set" in stopped_alerts(calls)[0]
    hass.states.async_set(CURTAIN, "71")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not mon.not_responding()


# ------------------------------------------------------------- alerts
async def test_quick_drop_out_again_is_not_alerted_again(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)  # its own setting: 12 h
    mon = entry.runtime_data
    await advance(hass, freezer, 12 * H + 5)
    assert len(stopped_alerts(calls)) == 1
    fresh(hass)  # a burst of reports
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not mon.not_responding()
    await advance(hass, freezer, 12 * H + 5)  # gone again within a day
    assert mon.not_responding() == [OLD] and len(stopped_alerts(calls)) == 1
    assert (mon.log[0]["kind"], mon.log[0]["status"]) == ("not_responding", "skipped")
    for _ in range(26):  # then it works for over a day
        await advance(hass, freezer, H)
        fresh(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 12 * H + 5)
    assert len(stopped_alerts(calls)) == 2


async def test_setting_raised_ends_not_responding(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass)
    mon = entry.runtime_data
    await advance(hass, freezer, 13 * H)
    assert mon.not_responding() == [OLD]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "overrides": {OLD: {"silence_hours": 48}}})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not mon.not_responding()
    assert (mon.log[0]["kind"], mon.log[0]["note"]) == ("recovered", "no longer beyond its limit")


async def test_low_reading_while_flagged_is_alerted(hass: HomeAssistant, freezer) -> None:
    """The level and the Last seen time arrive as separate updates, in any order."""
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "50")
    entry = await setup_bs(hass)
    await advance(hass, freezer, 13 * H)
    hass.states.async_set(OLD, "12")  # the level first...
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set(LAST_SEEN, dt_util.utcnow().isoformat())  # ...then Last seen
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [c.data["message"] for c in calls if "dropped to 12%" in c.data["message"]]
    count, attrs = entry.runtime_data.snapshot()
    assert count == 1 and attrs["devices"][0]["status"] == "low"


async def test_reminder_counts_both(hass: HomeAssistant, freezer) -> None:
    from datetime import datetime
    make_zigbee_device(hass)
    make_curtains(hass)
    calls = async_mock_service(hass, "notify", "test")
    freezer.move_to(datetime(2026, 10, 5, 19, 0, tzinfo=dt_util.get_default_time_zone()))
    fresh(hass, "50", hours_ago=13)
    hass.states.async_set(CURTAIN, "15")
    await setup_bs(hass, include_integrations=["mqtt", "switchbot"], reminder_days=[0])
    await advance(hass, freezer, H)  # Monday 20:00
    msgs = [c.data["message"] for c in calls if c.data["message"].startswith("You still")]
    assert msgs == ["You still have 1 device with the battery level below 20% and 1 device not responding. Check them soon!"]


async def test_unknown_level_is_not_low(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    hass.states.async_set(OLD, "unknown")
    entry = await setup_bs(hass, silence=None)
    count, attrs = entry.runtime_data.snapshot()
    dev = attrs["devices"][0]
    assert count == 0 and (dev["status"], dev["reading"]) == ("ok", False)


# ------------------------------------------------------------- from 1.0.x
async def test_upgrade_from_1_0_keeps_alerted_silence_quiet(hass: HomeAssistant, freezer, hass_storage) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    now = dt_util.utcnow()
    last = now - timedelta(hours=13)
    hass_storage["battery_states.e3"] = {"version": 1, "key": "battery_states.e3", "data": {
        "observed_at": (now - timedelta(hours=1)).isoformat(),
        "devices": {OLD: {"state": "50", "last_seen": last.isoformat(), "not_seen": True,
                          "credit": 3600.0, "offline_since": None}},
    }}
    hass.states.async_set(OLD, "50")
    hass.states.async_set(LAST_SEEN, last.isoformat())
    entry = MockConfigEntry(domain="battery_states", entry_id="e3", data={}, options={
        "notify_service": ["notify.test"], "devices": [], "include_integrations": ["mqtt"],
        "overrides": {OLD: {"silence_hours": 12}}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    mon = entry.runtime_data
    mem = mon._mem[OLD]
    # 13 h since the report, 1 h Last seen down (credit) and 1 h Home Assistant down: 11 h
    assert "credit" not in mem and mem["offline"] and mon.not_responding() == []
    await advance(hass, freezer, H + 5)
    assert mon.not_responding() == [OLD] and stopped_alerts(calls) == []  # 1.0.x alerted it


# ------------------------------------------------------------- learning from history
async def test_learns_from_the_recorder_history(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    now = dt_util.utcnow()
    history = [State(LAST_SEEN, (now - timedelta(minutes=50 * i)).isoformat()) for i in range(1, 5 * 24 * 60 // 50)]
    hass.config.components.add("recorder")

    class Recorder:
        def async_add_executor_job(self, func):
            return hass.async_add_executor_job(func)

    with (
        patch("homeassistant.components.recorder.get_instance", return_value=Recorder()),
        patch("homeassistant.components.recorder.history.state_changes_during_period",
              return_value={LAST_SEEN: history}),
    ):
        fresh(hass)
        entry = await setup_bs(hass, silence=None)
        await hass.async_block_till_done(wait_background_tasks=True)
    stats = entry.runtime_data._mem[OLD]["stats"]
    assert stats["seeded"] is True and stats["base"] == 50 * 60
    assert entry.runtime_data.diagnostics(OLD)["source"] == "own"


# ------------------------------------------------------------- settings page
async def test_settings_page_setting_and_diagnostics(hass: HomeAssistant, freezer, hass_ws_client) -> None:
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    fresh(hass)
    entry = await setup_bs(hass, silence=None)
    client = await hass_ws_client(hass)

    async def ws(**msg):
        await client.send_json_auto_id(msg)
        res = await client.receive_json()
        await hass.async_block_till_done(wait_background_tasks=True)
        return res

    res = await ws(type="battery_states/get")
    b = res["result"]["batteries"][0]
    assert b["silence_hours"] is None
    assert {"mode", "status", "judged", "reason", "source", "threshold", "network", "last_report"} <= set(b["health"])
    assert (b["health"]["mode"], b["health"]["judged"]) == ("last_seen", False)
    res = await ws(type="battery_states/update_battery", entity_id=OLD, silence_hours=6)
    assert entry.options["overrides"][OLD] == {"silence_hours": 6}
    b = res["result"]["batteries"][0]
    assert (b["silence_hours"], b["health"]["source"], b["health"]["threshold"]) == (6, "setting", 6 * H)
    assert (await ws(type="battery_states/update_battery", entity_id=OLD, silence_hours=0))["success"] is False
    assert (await ws(type="battery_states/update_battery", entity_id=OLD, silence_hours=721))["success"] is False
    await ws(type="battery_states/update_battery", entity_id=OLD)
    assert OLD not in entry.options.get("overrides", {})


async def test_battery_added_later_learns_from_history(hass: HomeAssistant, freezer) -> None:
    """Not only on the first start: a battery that joins the list later is learned from history too."""
    make_zigbee_device(hass)
    async_mock_service(hass, "notify", "test")
    now = dt_util.utcnow()
    history = [State(LAST_SEEN, (now - timedelta(minutes=50 * i)).isoformat()) for i in range(1, 5 * 24 * 60 // 50)]
    hass.config.components.add("recorder")
    calls = []

    class Recorder:
        def async_add_executor_job(self, func):
            calls.append(func)
            return hass.async_add_executor_job(func)

    with (
        patch("homeassistant.components.recorder.get_instance", return_value=Recorder()),
        patch("homeassistant.components.recorder.history.state_changes_during_period",
              return_value={LAST_SEEN: history}),
    ):
        fresh(hass)
        entry = await setup_bs(hass, silence=None, include_integrations=[])  # nothing on the list yet
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entry.runtime_data.devices == [] and calls == []
        hass.config_entries.async_update_entry(entry, options={**entry.options, "include_integrations": ["mqtt"]})
        await hass.async_block_till_done(wait_background_tasks=True)
    stats = entry.runtime_data._mem[OLD]["stats"]
    assert len(calls) == 1 and stats["seeded"] is True and stats["base"] == 50 * 60
