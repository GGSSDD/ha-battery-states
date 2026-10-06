"""1.0.12: notify entities, unavailable for the not-seen time, wording."""
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from .helpers import OLD, advance, make_zigbee_device, setup_bs, stopped_alerts
from .test_regression import CURTAIN, fresh, make_curtain

NOT_SEEN = 12 * 3600


# ------------------------------------------------------------- notify entities
async def test_notify_entity_sent_with_send_message(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.phone", "unknown", {"friendly_name": "Phone"})
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, notify_service=["notify.phone"])
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.phone"
    assert calls[0].data["title"] == "Batteries"
    assert "has stopped reporting" in calls[0].data["message"]
    log = entry.runtime_data.log[0]
    assert log["status"] == "sent" and log["targets"] == [{"service": "notify.phone", "ok": True}]


async def test_notify_service_and_entity_together(hass: HomeAssistant, freezer) -> None:
    make_zigbee_device(hass)
    svc = async_mock_service(hass, "notify", "mobile_app_phone")
    ent = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.tablet", "unknown")
    fresh(hass, hours_ago=13)
    await setup_bs(hass, notify_service=["notify.mobile_app_phone", "notify.tablet"])
    assert len(svc) == 1 and "entity_id" not in svc[0].data
    assert len(ent) == 1 and ent[0].data["entity_id"] == "notify.tablet"


async def test_notify_entity_unavailable_or_missing(hass: HomeAssistant, freezer, caplog) -> None:
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.phone", "unavailable")
    fresh(hass, hours_ago=13)
    entry = await setup_bs(hass, notify_service=["notify.phone", "notify.gone", "notify.send_message"])
    assert calls == []
    log = entry.runtime_data.log[0]
    assert log["status"] == "not_sent"
    assert log["targets"] == [
        {"service": "notify.phone", "ok": False, "error": "is unavailable"},
        {"service": "notify.gone", "ok": False, "error": "doesn't exist"},
        {"service": "notify.send_message", "ok": False, "error": "needs a notify entity: choose one instead"},
    ]
    assert "notify.gone not found" in caplog.text


async def test_notify_choices(hass: HomeAssistant) -> None:
    from custom_components.battery_states.monitor import notify_choices

    async_mock_service(hass, "notify", "send_message")
    async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "notify", "persistent_notification")
    hass.states.async_set("notify.tablet", "unknown", {"friendly_name": "Kitchen tablet"})
    hass.states.async_set("notify.bot", "unknown", {"friendly_name": "Alerts bot"})
    assert notify_choices(hass) == [
        {"value": "notify.mobile_app_phone", "label": "notify.mobile_app_phone"},
        {"value": "notify.persistent_notification", "label": "notify.persistent_notification"},
        {"value": "notify.bot", "label": "Alerts bot (notify.bot)"},
        {"value": "notify.tablet", "label": "Kitchen tablet (notify.tablet)"},
    ]


# ------------------------------------------------- unavailable for the not-seen time
async def test_short_outage_no_alert_and_timer_restarts(hass: HomeAssistant, freezer) -> None:
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(CURTAIN, "72")
    entry = await setup_bs(hass, include_integrations=["switchbot"])
    mon = entry.runtime_data
    hass.states.async_set(CURTAIN, "unavailable")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3600)
    hass.states.async_set(CURTAIN, "70")  # back after an hour
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mon._mem[CURTAIN]["unavailable_since"] is None
    hass.states.async_set(CURTAIN, "unavailable")  # a new outage starts from zero
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, NOT_SEEN - 3600)
    assert mon._mem[CURTAIN]["not_seen"] is False and stopped_alerts(calls) == []
    await advance(hass, freezer, 3600 + 5)
    assert mon._mem[CURTAIN]["not_seen"] is True and len(stopped_alerts(calls)) == 1


async def test_unknown_ends_the_outage(hass: HomeAssistant, freezer) -> None:
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(CURTAIN, "72")
    entry = await setup_bs(hass, include_integrations=["switchbot"])
    hass.states.async_set(CURTAIN, "unavailable")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 3600)
    hass.states.async_set(CURTAIN, "unknown")  # back, no reading yet
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, NOT_SEEN)
    assert entry.runtime_data._mem[CURTAIN]["not_seen"] is False and stopped_alerts(calls) == []


async def test_not_seen_limit_applies(hass: HomeAssistant, freezer) -> None:
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    hass.states.async_set(CURTAIN, "72")
    await setup_bs(hass, include_integrations=["switchbot"], not_seen_hours=2)
    hass.states.async_set(CURTAIN, "unavailable")
    await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 2 * 3600 - 5)
    assert stopped_alerts(calls) == []
    await advance(hass, freezer, 10)
    assert len(stopped_alerts(calls)) == 1


async def test_flag_from_older_version_kept(hass: HomeAssistant, freezer, hass_storage) -> None:
    """Flagged by 1.0.11 (unavailable = not seen at once): stays flagged, no new alert."""
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    entry = MockConfigEntry(domain="battery_states", entry_id="e1", data={},
                            options={"notify_service": ["notify.test"], "devices": [], "include_integrations": ["switchbot"]})
    hass_storage["battery_states.e1"] = {"version": 1, "key": "battery_states.e1", "data": {
        "observed_at": dt_util.utcnow().isoformat(),
        "devices": {CURTAIN: {"state": "72", "last_seen": None, "not_seen": True}},
    }}
    hass.states.async_set(CURTAIN, "unavailable")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data._mem[CURTAIN]["not_seen"] is True
    await advance(hass, freezer, NOT_SEEN + 5)
    assert stopped_alerts(calls) == []


async def test_downtime_credited(hass: HomeAssistant, freezer, hass_storage) -> None:
    """Unavailable for 11 h, then Home Assistant was off for 5 h: those 5 h don't count."""
    make_curtain(hass)
    calls = async_mock_service(hass, "notify", "test")
    now = dt_util.utcnow()
    entry = MockConfigEntry(domain="battery_states", entry_id="e2", data={},
                            options={"notify_service": ["notify.test"], "devices": [], "include_integrations": ["switchbot"]})
    hass_storage["battery_states.e2"] = {"version": 1, "key": "battery_states.e2", "data": {
        "observed_at": (now - timedelta(hours=5)).isoformat(),
        "devices": {CURTAIN: {"state": "72", "last_seen": None, "not_seen": False,
                              "unavailable_since": (now - timedelta(hours=16)).isoformat()}},
    }}
    hass.states.async_set(CURTAIN, "unavailable")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    mon = entry.runtime_data
    assert mon._mem[CURTAIN]["not_seen"] is False  # 11 h counted, not 16 h
    await advance(hass, freezer, 3600 - 5)
    assert stopped_alerts(calls) == []
    await advance(hass, freezer, 10)
    assert mon._mem[CURTAIN]["not_seen"] is True and len(stopped_alerts(calls)) == 1


async def test_zigbee_with_last_seen_unchanged(hass: HomeAssistant, freezer) -> None:
    """A device with a Last seen sensor still follows it, not its unavailable state."""
    make_zigbee_device(hass)
    calls = async_mock_service(hass, "notify", "test")
    fresh(hass, "50")
    entry = await setup_bs(hass)
    assert entry.runtime_data._mem[OLD].get("unavailable_since") is None
    assert stopped_alerts(calls) == []


# ------------------------------------------------------------------ wording
async def test_reminder_wording_several(hass: HomeAssistant) -> None:
    from custom_components.battery_states.monitor import BatteryMonitor, alert_settings
    import types

    fake = types.SimpleNamespace(settings=alert_settings({"low_threshold": 20}))
    assert BatteryMonitor._reminder_text(fake, 2) == (
        "You still have 2 devices with the battery level below 20%. Consider replacing or recharging them soon!"
    )
    assert BatteryMonitor._reminder_text(fake, 1) == (
        "You still have 1 device with the battery level below 20%. Consider replacing or recharging it soon!"
    )
