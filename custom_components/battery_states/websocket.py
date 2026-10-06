"""WebSocket commands used by the Battery States settings page."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.loader import async_get_integrations

from .const import (
    ATTR_BATTERY_TYPE,
    ATTR_ENTITY_ID,
    ATTR_NAME,
    ATTR_RECHARGEABLE,
    CONF_ALERT_LOW,
    CONF_ALERT_NOT_SEEN,
    CONF_DEVICES,
    CONF_EXCLUDE,
    CONF_INCLUDE_AREAS,
    CONF_INCLUDE_DEVICES,
    CONF_INCLUDE_INTEGRATIONS,
    CONF_INCLUDE_LABELS,
    CONF_LOW_THRESHOLD,
    CONF_NOT_SEEN_HOURS,
    CONF_NOTIFY,
    CONF_OVERRIDES,
    CONF_QUIET,
    CONF_QUIET_FROM,
    CONF_QUIET_TO,
    CONF_REMINDER,
    CONF_REMINDER_DAYS,
    CONF_REMINDER_TIME,
    DOMAIN,
    LOW_THRESHOLD_RANGE,
    NOT_SEEN_HOURS_RANGE,
    SIGNAL_LOG,
)
from .monitor import alert_settings, notify_choices, notify_services

FILTER_KEYS = (
    CONF_INCLUDE_INTEGRATIONS,
    CONF_INCLUDE_AREAS,
    CONF_INCLUDE_LABELS,
    CONF_INCLUDE_DEVICES,
    CONF_EXCLUDE,
)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the commands."""
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_update_battery)
    websocket_api.async_register_command(hass, ws_add_battery)
    websocket_api.async_register_command(hass, ws_remove_battery)
    websocket_api.async_register_command(hass, ws_exclude_battery)
    websocket_api.async_register_command(hass, ws_set_filters)
    websocket_api.async_register_command(hass, ws_set_notify)
    websocket_api.async_register_command(hass, ws_set_limits)
    websocket_api.async_register_command(hass, ws_set_alerts)
    websocket_api.async_register_command(hass, ws_subscribe)


def _entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _clean(settings: dict[str, Any]) -> dict[str, Any]:
    """Keep only settings that are set."""
    out: dict[str, Any] = {}
    if (name := (settings.get(ATTR_NAME) or "").strip()):
        out[ATTR_NAME] = name
    if (battery_type := (settings.get(ATTR_BATTERY_TYPE) or "").strip()):
        out[ATTR_BATTERY_TYPE] = battery_type
    if settings.get(ATTR_RECHARGEABLE):
        out[ATTR_RECHARGEABLE] = True
    return out


async def _snapshot(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Everything the settings page shows."""
    options = entry.options
    devices_by_id = {d[ATTR_ENTITY_ID]: d for d in options.get(CONF_DEVICES, [])}
    overrides = options.get(CONF_OVERRIDES, {})
    batteries = []
    if entry.state is ConfigEntryState.LOADED:
        for dev in entry.runtime_data.devices:
            settings = devices_by_id.get(dev.entity_id) if dev.added else overrides.get(dev.entity_id)
            settings = settings or {}
            batteries.append(
                {
                    "entity_id": dev.entity_id,
                    "added": dev.added,
                    "matched": dev.matched,
                    "name": settings.get(ATTR_NAME, ""),
                    "battery_type": settings.get(ATTR_BATTERY_TYPE, ""),
                    "rechargeable": bool(settings.get(ATTR_RECHARGEABLE)),
                    "auto_name": dev.auto_name,
                    "auto_type": dev.auto_type,
                    "area": dev.area,
                    "shown_name": dev.name,
                    "shown_type": dev.battery_type,
                    "last_seen_disabled": dev.last_seen_disabled,
                }
            )
    ent_reg = er.async_get(hass)
    platforms = sorted(
        {
            e.platform
            for e in ent_reg.entities.values()
            if e.domain == "sensor" and (e.device_class or e.original_device_class) == "battery"
        }
        | set(options.get(CONF_INCLUDE_INTEGRATIONS, []))
    )
    integrations = await async_get_integrations(hass, platforms)
    integration_options = sorted(
        (
            {
                "value": p,
                "label": integrations[p].name if not isinstance(integrations.get(p), Exception) else p,
            }
            for p in platforms
        ),
        key=lambda o: o["label"].casefold(),
    )

    # The saved filters, as names (what the page shows as "applied").
    area_reg, dev_reg, label_reg = ar.async_get(hass), dr.async_get(hass), lr.async_get(hass)
    integration_names = {o["value"]: o["label"] for o in integration_options}

    def area_name(area_id: str) -> str:
        area = area_reg.async_get_area(area_id)
        return area.name if area else area_id

    def label_name(label_id: str) -> str:
        label = label_reg.async_get_label(label_id)
        return label.name if label else label_id

    def device_name(device_id: str) -> str:
        device = dev_reg.async_get(device_id)
        return (device.name_by_user or device.name or device_id) if device else device_id

    def entity_name(entity_id: str) -> str:
        state = hass.states.get(entity_id)
        return state.name if state and state.name else entity_id

    applied = {
        CONF_INCLUDE_INTEGRATIONS: [
            integration_names.get(p, p) for p in options.get(CONF_INCLUDE_INTEGRATIONS) or []
        ],
        CONF_INCLUDE_AREAS: [area_name(a) for a in options.get(CONF_INCLUDE_AREAS) or []],
        CONF_INCLUDE_LABELS: [label_name(lb) for lb in options.get(CONF_INCLUDE_LABELS) or []],
        CONF_INCLUDE_DEVICES: [device_name(d) for d in options.get(CONF_INCLUDE_DEVICES) or []],
        CONF_EXCLUDE: [entity_name(e) for e in options.get(CONF_EXCLUDE) or []],
    }
    settings = alert_settings(options)
    return {
        "limits": {
            CONF_LOW_THRESHOLD: settings.low,
            CONF_NOT_SEEN_HOURS: int(settings.not_seen.total_seconds() // 3600),
        },
        "alerts": {
            CONF_ALERT_LOW: settings.alert_low,
            CONF_ALERT_NOT_SEEN: settings.alert_not_seen,
            CONF_REMINDER: settings.reminder,
            CONF_REMINDER_DAYS: [str(d) for d in settings.reminder_days],
            CONF_REMINDER_TIME: f"{settings.reminder_time:%H:%M:%S}",
            CONF_QUIET: settings.quiet,
            CONF_QUIET_FROM: f"{settings.quiet_from:%H:%M:%S}",
            CONF_QUIET_TO: f"{settings.quiet_to:%H:%M:%S}",
        },
        "log": _log(entry),
        "batteries": batteries,
        "filters": {key: list(options.get(key) or []) for key in FILTER_KEYS},
        "applied_filters": applied,
        "found_count": sum(1 for b in batteries if not b["added"]),
        "notify_services": notify_services(options),
        "integration_options": integration_options,
        "notify_options": notify_choices(hass),
    }


def _log(entry: ConfigEntry) -> list[dict[str, Any]]:
    return entry.runtime_data.log if entry.state is ConfigEntryState.LOADED else []


def _whole(lo: int, hi: int) -> vol.All:
    """A whole number from lo to hi (the number fields may send 20.0)."""

    def check(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise vol.Invalid("must be a whole number")
        return int(value)

    return vol.All(check, vol.Range(min=lo, max=hi))


def _hhmmss(value: Any) -> str:
    return f"{cv.time(value):%H:%M:%S}"


def _save(hass: HomeAssistant, entry: ConfigEntry, **changes: Any) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, **changes})
    if entry.state is ConfigEntryState.LOADED:
        entry.runtime_data.async_apply_options()


async def _respond(hass: HomeAssistant, connection, msg_id: int, entry: ConfigEntry) -> None:
    connection.send_result(msg_id, await _snapshot(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get"})
@websocket_api.async_response
async def ws_get(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the settings page data."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/update_battery",
        vol.Required("entity_id"): str,
        vol.Optional("name", default=""): str,
        vol.Optional("battery_type", default=""): str,
        vol.Optional("rechargeable", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_update_battery(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Save one battery's friendly name, battery type and rechargeable tick."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    entity_id = msg["entity_id"]
    settings = _clean(msg)
    devices = [dict(d) for d in entry.options.get(CONF_DEVICES, [])]
    for item in devices:
        if item[ATTR_ENTITY_ID] == entity_id:
            item.clear()
            item.update({ATTR_ENTITY_ID: entity_id, **settings})
            _save(hass, entry, **{CONF_DEVICES: devices})
            break
    else:
        overrides = {k: dict(v) for k, v in entry.options.get(CONF_OVERRIDES, {}).items()}
        if settings:
            overrides[entity_id] = settings
        else:
            overrides.pop(entity_id, None)
        _save(hass, entry, **{CONF_OVERRIDES: overrides})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/add_battery", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_add_battery(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Add one battery sensor to the list (keeps settings it had as a filter match)."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    entity_id = msg["entity_id"]
    devices = [dict(d) for d in entry.options.get(CONF_DEVICES, [])]
    if not any(d[ATTR_ENTITY_ID] == entity_id for d in devices):
        overrides = {k: dict(v) for k, v in entry.options.get(CONF_OVERRIDES, {}).items()}
        devices.append({ATTR_ENTITY_ID: entity_id, **overrides.pop(entity_id, {})})
        exclude = [e for e in entry.options.get(CONF_EXCLUDE, []) if e != entity_id]
        _save(hass, entry, **{CONF_DEVICES: devices, CONF_OVERRIDES: overrides, CONF_EXCLUDE: exclude})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/remove_battery", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_remove_battery(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Take back a battery added one by one.

    If a filter also matches it, it stays on the list as found by that filter,
    keeping its friendly name, battery type and rechargeable tick.
    """
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    entity_id = msg["entity_id"]
    removed = [d for d in entry.options.get(CONF_DEVICES, []) if d[ATTR_ENTITY_ID] == entity_id]
    devices = [d for d in entry.options.get(CONF_DEVICES, []) if d[ATTR_ENTITY_ID] != entity_id]
    overrides = {k: dict(v) for k, v in entry.options.get(CONF_OVERRIDES, {}).items()}
    matched = entry.state is ConfigEntryState.LOADED and any(
        d.entity_id == entity_id and d.matched for d in entry.runtime_data.devices
    )
    settings = _clean(removed[0]) if removed else {}
    if matched and settings:
        overrides[entity_id] = settings
    _save(hass, entry, **{CONF_DEVICES: devices, CONF_OVERRIDES: overrides})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/exclude_battery", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_exclude_battery(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Hide a battery for good, even when a filter matches it.

    Its settings are kept (as an override), so they come back if it is added
    again or no longer excluded.
    """
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    entity_id = msg["entity_id"]
    removed = [d for d in entry.options.get(CONF_DEVICES, []) if d[ATTR_ENTITY_ID] == entity_id]
    devices = [d for d in entry.options.get(CONF_DEVICES, []) if d[ATTR_ENTITY_ID] != entity_id]
    overrides = {k: dict(v) for k, v in entry.options.get(CONF_OVERRIDES, {}).items()}
    if removed and (settings := _clean(removed[0])):
        overrides[entity_id] = settings
    exclude = list(entry.options.get(CONF_EXCLUDE) or [])
    if entity_id not in exclude:
        exclude.append(entity_id)
    _save(hass, entry, **{CONF_DEVICES: devices, CONF_OVERRIDES: overrides, CONF_EXCLUDE: exclude})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_filters",
        **{vol.Optional(key, default=[]): [str] for key in FILTER_KEYS},
    }
)
@websocket_api.async_response
async def ws_set_filters(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Save the automatic filters."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    _save(hass, entry, **{key: list(dict.fromkeys(msg[key])) for key in FILTER_KEYS})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_notify",
        vol.Optional("notify_services", default=[]): [str],
    }
)
@websocket_api.async_response
async def ws_set_notify(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Save where alerts go (kept for a settings page opened before 1.0.8)."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    services = list(dict.fromkeys(s.strip() for s in msg["notify_services"] if s.strip()))
    _save(hass, entry, **{CONF_NOTIFY: services})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_limits",
        vol.Required(CONF_LOW_THRESHOLD): _whole(*LOW_THRESHOLD_RANGE),
        vol.Required(CONF_NOT_SEEN_HOURS): _whole(*NOT_SEEN_HOURS_RANGE),
    }
)
@websocket_api.async_response
async def ws_set_limits(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Save the low battery limit and the not-seen time."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    _save(hass, entry, **{k: msg[k] for k in (CONF_LOW_THRESHOLD, CONF_NOT_SEEN_HOURS)})
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_alerts",
        vol.Optional("notify_services", default=[]): [str],
        vol.Required(CONF_ALERT_LOW): bool,
        vol.Required(CONF_ALERT_NOT_SEEN): bool,
        vol.Required(CONF_REMINDER): bool,
        vol.Required(CONF_REMINDER_DAYS): [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))],
        vol.Required(CONF_REMINDER_TIME): _hhmmss,
        vol.Required(CONF_QUIET): bool,
        vol.Required(CONF_QUIET_FROM): _hhmmss,
        vol.Required(CONF_QUIET_TO): _hhmmss,
    }
)
@websocket_api.async_response
async def ws_set_alerts(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Save where alerts go, which ones are sent, the reminder and quiet hours."""
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_found", "Battery States is not set up")
        return
    if msg[CONF_QUIET] and msg[CONF_QUIET_FROM] == msg[CONF_QUIET_TO]:
        connection.send_error(
            msg["id"], "invalid_format", "Quiet hours need different start and end times"
        )
        return
    services = list(dict.fromkeys(s.strip() for s in msg["notify_services"] if s.strip()))
    _save(
        hass,
        entry,
        **{CONF_NOTIFY: services, CONF_REMINDER_DAYS: sorted(set(msg[CONF_REMINDER_DAYS]))},
        **{
            k: msg[k]
            for k in (
                CONF_ALERT_LOW,
                CONF_ALERT_NOT_SEEN,
                CONF_REMINDER,
                CONF_REMINDER_TIME,
                CONF_QUIET,
                CONF_QUIET_FROM,
                CONF_QUIET_TO,
            )
        },
    )
    await _respond(hass, connection, msg["id"], entry)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe_log"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Send the recent alerts whenever they change (the settings page stays current)."""

    @callback
    def changed() -> None:
        if (entry := _entry(hass)) is not None:
            connection.send_message(websocket_api.event_message(msg["id"], {"log": _log(entry)}))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_LOG, changed)
    connection.send_result(msg["id"])
