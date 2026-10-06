"""Adding the integration from scratch, and the upgrade path."""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service


async def test_add_integration(hass: HomeAssistant, hass_ws_client) -> None:
    async_mock_service(hass, "notify", "phone")
    result = await hass.config_entries.flow.async_init("battery_states", context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"notify_service": ["notify.phone"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Battery States"
    entry = result["result"]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options == {"notify_service": ["notify.phone"], "devices": []}
    assert hass.states.get("sensor.battery_states_low_batteries").state == "0"
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "battery_states/get"})
    res = (await client.receive_json())["result"]
    assert res["limits"] == {"low_threshold": 20, "not_seen_hours": 12}
    assert res["alerts"]["reminder_days"] == ["1", "3", "5"] and res["alerts"]["quiet_hours"] is False
    assert res["log"] == [] and res["batteries"] == []
    # A second one is refused.
    again = await hass.config_entries.flow.async_init("battery_states", context={"source": config_entries.SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "single_instance_allowed"


async def test_reload_keeps_everything(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "phone")
    entry = MockConfigEntry(domain="battery_states", data={}, options={"notify_service": ["notify.phone"], "devices": [], "low_threshold": 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    entry.runtime_data._alert("reminder", "m", detail="1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.settings.low == 30 and entry.runtime_data.log[0]["kind"] == "reminder" and len(calls) == 1
    assert hass.states.get("sensor.battery_states_low_batteries").attributes["low_threshold"] == 30
