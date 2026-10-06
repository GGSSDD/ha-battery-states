"""Battery monitoring for Battery States.

Keeps every monitored battery's last real reading (also across restarts),
flags devices that stopped reporting, sends the alerts and the reminder, and
provides the data the sensor and the card show.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
import math
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_AREA,
    ATTR_BATTERY_TYPE,
    ATTR_ENTITY_ID,
    ATTR_NAME,
    ATTR_RECHARGEABLE,
    CONF_DEVICES,
    CONF_EXCLUDE,
    CONF_INCLUDE_AREAS,
    CONF_INCLUDE_DEVICES,
    CONF_INCLUDE_INTEGRATIONS,
    CONF_INCLUDE_LABELS,
    CONF_ALERT_LOW,
    CONF_ALERT_NOT_SEEN,
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
    DEFAULT_LOW_THRESHOLD,
    DEFAULT_NOT_SEEN_HOURS,
    DEFAULT_QUIET_FROM,
    DEFAULT_QUIET_TO,
    DEFAULT_REMINDER_DAYS,
    DEFAULT_REMINDER_TIME,
    DOMAIN,
    LOG_SIZE,
    LOW_THRESHOLD_RANGE,
    NOT_SEEN_HOURS_RANGE,
    NOTIFY_TITLE,
    RECHARGEABLE_TYPE,
    SIGNAL_LOG,
    SIGNAL_UPDATE,
    UNKNOWN_TYPE,
)
from .library import BatteryLibrary

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 1  # seconds, for value / not-seen changes (rare)
LAST_SEEN_SAVE_INTERVAL = 600  # seconds, for last_seen-only changes (frequent)
RESOLVE_DELAY = 2  # seconds, to coalesce registry updates
DELIVERY_WAIT = 10  # seconds an unload waits for alerts still being sent
WAIT_FOR_START = "waiting for Home Assistant to start"
SEND_MESSAGE = "send_message"  # notify's service for notify entities


@dataclass(frozen=True)
class AlertSettings:
    """Limits and alert settings (settings page); defaults = the former fixed values."""

    low: int
    not_seen: timedelta
    alert_low: bool
    alert_not_seen: bool
    reminder: bool
    reminder_days: tuple[int, ...]
    reminder_time: time
    quiet: bool
    quiet_from: time
    quiet_to: time


def _setting_int(value: Any, default: int, bounds: tuple[int, int]) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return default
    return int(value) if bounds[0] <= value <= bounds[1] else default


def _setting_time(value: Any, default: str) -> time:
    parsed = dt_util.parse_time(value) if isinstance(value, str) else None
    return parsed or dt_util.parse_time(default)  # type: ignore[return-value]


def alert_settings(options: Any) -> AlertSettings:
    """The settings from the options; a missing or broken value uses its default."""
    days = options.get(CONF_REMINDER_DAYS, DEFAULT_REMINDER_DAYS)
    if not isinstance(days, list):
        days = DEFAULT_REMINDER_DAYS
    return AlertSettings(
        low=_setting_int(options.get(CONF_LOW_THRESHOLD), DEFAULT_LOW_THRESHOLD, LOW_THRESHOLD_RANGE),
        not_seen=timedelta(
            hours=_setting_int(
                options.get(CONF_NOT_SEEN_HOURS), DEFAULT_NOT_SEEN_HOURS, NOT_SEEN_HOURS_RANGE
            )
        ),
        alert_low=options.get(CONF_ALERT_LOW, True) is not False,
        alert_not_seen=options.get(CONF_ALERT_NOT_SEEN, True) is not False,
        reminder=options.get(CONF_REMINDER, True) is not False,
        reminder_days=tuple(
            sorted({d for d in days if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6})
        ),
        reminder_time=_setting_time(options.get(CONF_REMINDER_TIME), DEFAULT_REMINDER_TIME),
        quiet=options.get(CONF_QUIET) is True,
        quiet_from=_setting_time(options.get(CONF_QUIET_FROM), DEFAULT_QUIET_FROM),
        quiet_to=_setting_time(options.get(CONF_QUIET_TO), DEFAULT_QUIET_TO),
    )


@dataclass(frozen=True)
class MonitoredDevice:
    """One battery shown on the card."""

    entity_id: str
    name: str
    battery_type: str
    area: str
    last_seen_entity: str | None
    # What applies without settings (shown in the settings screen)
    auto_name: str = ""
    auto_area: str = ""
    auto_type: str = ""
    added: bool = False  # added one by one (else found by a filter)
    matched: bool = False  # a filter matches it (also when added one by one)
    last_seen_disabled: bool = False  # its last_seen sensor is disabled: no not-seen check


def number_of(state: State | None) -> float | None:
    """Return the state as a finite number, or None if it isn't a real reading."""
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def timestamp_of(state: State | None) -> datetime | None:
    """Return a last_seen sensor's state as an aware datetime, or None."""
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    parsed = dt_util.parse_datetime(state.state)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _parse(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def notify_services(options: Any) -> list[str]:
    """Where alerts go, from the options (older versions stored one string).

    Each is a notify service ("notify.mobile_app_phone") or a notify entity
    ("notify.phone", sent with notify.send_message).
    """
    value = options.get(CONF_NOTIFY)
    if isinstance(value, str):
        value = [value]
    return [s.strip() for s in value or [] if isinstance(s, str) and s.strip()]


def notify_choices(hass: HomeAssistant) -> list[dict[str, str]]:
    """What alerts can be sent to: the notify services, then the notify entities.

    notify.send_message itself is left out: it needs an entity, which is listed.
    """
    choices = {
        f"notify.{name}": f"notify.{name}"
        for name in sorted(hass.services.async_services_for_domain("notify"))
        if name != SEND_MESSAGE
    }
    entities = sorted(hass.states.async_all("notify"), key=lambda st: st.name.casefold())
    for st in entities:
        choices.setdefault(st.entity_id, f"{st.name} ({st.entity_id})")
    return [{"value": value, "label": label} for value, label in choices.items()]


def one_decimal(raw: str) -> float:
    """The value as the former pyscript stored it ("%.1f")."""
    return float(f"{float(raw):.1f}")


def alert_percent(raw: str) -> int:
    """The whole percentage the alert prints ("%.1f" string through Jinja's int, as before)."""
    return math.trunc(one_decimal(raw))


def recharge_word(battery_type: str) -> str:
    """Same wording as the former alerts."""
    return "recharging" if battery_type == "Rechargeable" else "replacing"


class BatteryMonitor:
    """Tracks the monitored batteries of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        # entity_id -> {"state": raw str | None, "last_seen": iso | None, "not_seen": bool}
        self._mem: dict[str, dict[str, Any]] = {}
        self.devices: list[MonitoredDevice] = []
        self._by_entity: dict[str, list[MonitoredDevice]] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._track_unsub: CALLBACK_TYPE | None = None
        self._timers: dict[str, tuple[datetime, CALLBACK_TYPE]] = {}
        self._started_unsub: CALLBACK_TYPE | None = None
        self._resolve_unsub: CALLBACK_TYPE | None = None
        self._last_seen_save_unsub: CALLBACK_TYPE | None = None
        # Batteries added one by one whose entity is gone from Home Assistant:
        # hidden, kept in the settings, shown again when the entity comes back.
        self._missing: set[str] = set()
        self._stopped = False
        self.settings = alert_settings(entry.options)
        self._schedule_unsubs: list[CALLBACK_TYPE] = []  # the daily reminder
        self._quiet_end_unsub: CALLBACK_TYPE | None = None
        # Recent alerts, newest first (shown on the settings page), and the
        # ones waiting for the end of quiet hours (the same objects).
        self.log: list[dict[str, Any]] = []
        self._held: list[dict[str, Any]] = []
        self._deliveries: set[asyncio.Task[None]] = set()
        self.library = BatteryLibrary(hass, self._library_updated)

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        """Load the saved readings and start tracking."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("devices"), dict):
            self._mem = stored["devices"]
            self._credit_downtime(_parse(stored.get("observed_at")))
        if isinstance(stored, dict):
            self._load_log(stored.get("log"), stored.get("held"))
        await self.library.async_load()
        self.library.async_start_updates()
        self._resolve_devices()
        self._track()
        self._evaluate_all()
        self._arm_schedules()
        # Quiet hours may have ended while Home Assistant was off (when starting
        # up, this waits until Home Assistant has started: see _ha_started).
        self._flush_held()

        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            ar.EVENT_AREA_REGISTRY_UPDATED,
        ):
            self._unsubs.append(
                self.hass.bus.async_listen(event_type, self._registry_updated)
            )
        # Home Assistant does not unload integrations when it stops: hand the
        # latest data to the store, which writes it in its final write.
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._ha_stopping)
        )
        if self.hass.state is not CoreState.running:
            self._started_unsub = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._ha_started
            )

    @callback
    def _ha_stopping(self, _event: Event) -> None:
        self._store.async_delay_save(self._data_to_save, 0)

    async def async_stop(self) -> None:
        """Stop tracking and save (a reload must find the latest data)."""
        self._stopped = True
        self.library.async_stop()
        for unsub in self._unsubs + self._schedule_unsubs:
            unsub()
        self._unsubs.clear()
        self._schedule_unsubs.clear()
        if self._quiet_end_unsub:
            self._quiet_end_unsub()
            self._quiet_end_unsub = None
        if self._track_unsub:
            self._track_unsub()
            self._track_unsub = None
        for _, unsub in self._timers.values():
            unsub()
        self._timers.clear()
        if self._started_unsub:
            self._started_unsub()
            self._started_unsub = None
        if self._resolve_unsub:
            self._resolve_unsub()
            self._resolve_unsub = None
        if self._last_seen_save_unsub:
            self._last_seen_save_unsub()
            self._last_seen_save_unsub = None
        if self._deliveries:
            # Let alerts being sent finish, so the saved history has their result.
            await asyncio.wait(set(self._deliveries), timeout=DELIVERY_WAIT)
        await self._store.async_save(self._data_to_save())

    @callback
    def _credit_downtime(self, observed_at: datetime | None) -> None:
        """Home Assistant was not running since observed_at: that time does not
        count as silence (no device could be heard)."""
        if observed_at is None:
            return
        now = dt_util.utcnow()
        downtime = max(0.0, (now - observed_at).total_seconds())
        for mem in self._mem.values():
            if unavailable_since := _parse(mem.get("unavailable_since")):
                mem["unavailable_since"] = (unavailable_since + timedelta(seconds=downtime)).isoformat()
            if not mem.get("last_seen"):
                continue
            credit = float(mem.get("credit") or 0.0)
            if offline_since := _parse(mem.get("offline_since")):
                credit += max(0.0, (observed_at - offline_since).total_seconds())
                mem["offline_since"] = None
            mem["credit"] = credit + downtime

    @callback
    def _ha_started(self, _event: Event) -> None:
        """Home Assistant finished starting: unavailable / missing now means something."""
        self._started_unsub = None
        self._delayed_resolve(None)
        self._evaluate_all()
        self._flush_held()  # alerts that waited for the start (notify services exist now)

    # ------------------------------------------------------- device selection

    @callback
    def _registry_updated(self, event: Event) -> None:
        """Re-resolve the devices shortly after registry changes."""
        # Changes to this integration's own entities/device don't matter.
        if event.event_type == er.EVENT_ENTITY_REGISTRY_UPDATED:
            if event.data.get("action") == "update" and (old_id := event.data.get("old_entity_id")):
                self._entity_id_changed(old_id, event.data["entity_id"])
            entry = er.async_get(self.hass).async_get(event.data.get("entity_id", ""))
            if entry is not None and entry.platform == DOMAIN:
                return
        elif event.event_type == dr.EVENT_DEVICE_REGISTRY_UPDATED:
            device = dr.async_get(self.hass).async_get(event.data.get("device_id", ""))
            if device is not None and any(i[0] == DOMAIN for i in device.identifiers):
                return
        if self._resolve_unsub:
            self._resolve_unsub()
        self._resolve_unsub = async_call_later(
            self.hass, RESOLVE_DELAY, self._delayed_resolve
        )

    @callback
    def _entity_id_changed(self, old: str, new: str) -> None:
        """An entity ID was changed in Home Assistant: the battery's settings,
        exclusion and readings follow it to the new ID."""
        if old in self._mem and new not in self._mem:
            self._mem[new] = self._mem.pop(old)
        options = self.entry.options
        changes: dict[str, Any] = {}
        devices = options.get(CONF_DEVICES, [])
        if any(d.get(ATTR_ENTITY_ID) == old for d in devices):
            changes[CONF_DEVICES] = [
                {**d, ATTR_ENTITY_ID: new} if d.get(ATTR_ENTITY_ID) == old else d for d in devices
            ]
        overrides = options.get(CONF_OVERRIDES, {})
        if old in overrides:
            changes[CONF_OVERRIDES] = {
                (new if k == old else k): v for k, v in overrides.items() if k != new
            }
        exclude = options.get(CONF_EXCLUDE, [])
        if old in exclude:
            changes[CONF_EXCLUDE] = list(dict.fromkeys(new if e == old else e for e in exclude))
        if changes:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**options, **changes}
            )

    @callback
    def async_apply_options(self) -> None:
        """Apply changed options (settings, filters, limits, alerts) in place."""
        old = self.settings
        self.settings = alert_settings(self.entry.options)
        self._delayed_resolve(None)
        if self.settings != old and not self._stopped:
            self._arm_schedules()
            self._evaluate_all()  # the limits decide low / not seen
            self._flush_held()  # quiet hours may be off or over now
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def _library_updated(self) -> None:
        """A newer battery library arrived: types may have changed."""
        self._delayed_resolve(None)

    @callback
    def _delayed_resolve(self, now: datetime | None) -> None:
        if now is None and self._resolve_unsub:
            # Resolving right away covers a pending delayed resolve too.
            self._resolve_unsub()
        self._resolve_unsub = None
        if self._stopped:
            return
        old = self.devices
        self._resolve_devices()
        if self.devices != old:
            self._track()
            self._evaluate_all()

    @callback
    def _resolve_devices(self) -> None:
        """Build the monitored list.

        Batteries added one by one come first, in the order they were added;
        batteries found by the filters follow, sorted by name. Every battery can
        have settings; whatever isn't set comes from Home Assistant (name, area)
        or the battery library (type).
        """
        options = self.entry.options
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        exclude = set(options.get(CONF_EXCLUDE, []))
        overrides: dict[str, dict[str, Any]] = options.get(CONF_OVERRIDES, {})

        def area_name(area_id: str | None) -> str:
            if not area_id:
                return ""
            area = area_reg.async_get_area(area_id)
            return area.name if area else ""

        def device_of(entry: er.RegistryEntry | None) -> dr.DeviceEntry | None:
            if entry is None or not entry.device_id:
                return None
            return dev_reg.async_get(entry.device_id)

        def ha_area_id(entry: er.RegistryEntry | None) -> str | None:
            if entry is None:
                return None
            if entry.area_id:
                return entry.area_id
            device = device_of(entry)
            return device.area_id if device else None

        def last_seen_of(entry: er.RegistryEntry | None) -> er.RegistryEntry | None:
            """The device's last_seen sensor, an enabled one first.

            A disabled one is returned only to say so on the settings page; it is
            not used (it has no state), as before.
            """
            if entry is None or not entry.device_id:
                return None
            found = [
                sibling
                for sibling in er.async_entries_for_device(
                    ent_reg, entry.device_id, include_disabled_entities=True
                )
                if sibling.domain == "sensor"
                and (sibling.device_class or sibling.original_device_class) == "timestamp"
                and (
                    (sibling.original_name or "").strip().lower() == "last seen"
                    or sibling.translation_key == "last_seen"
                )
            ]
            return next((e for e in found if e.disabled_by is None), found[0] if found else None)

        def native_name(entry: er.RegistryEntry | None, entity_id: str) -> str:
            device = device_of(entry)
            if device:
                return device.name_by_user or device.name or entity_id
            state = self.hass.states.get(entity_id)
            if state and state.name:
                return state.name
            return entity_id

        def library_type(entry: er.RegistryEntry | None) -> str:
            device = device_of(entry)
            if device is None:
                return UNKNOWN_TYPE
            return (
                self.library.battery_type(
                    device.manufacturer, device.model, device.model_id, device.hw_version
                )
                or UNKNOWN_TYPE
            )

        integrations = set(options.get(CONF_INCLUDE_INTEGRATIONS) or [])
        areas = set(options.get(CONF_INCLUDE_AREAS) or [])
        labels = set(options.get(CONF_INCLUDE_LABELS) or [])
        devices = set(options.get(CONF_INCLUDE_DEVICES) or [])

        def matches_filters(entry: er.RegistryEntry | None) -> bool:
            """Whether a battery sensor matches any filter (exclusions aside)."""
            if (
                entry is None
                or entry.domain != "sensor"
                or entry.disabled_by is not None
                or (entry.device_class or entry.original_device_class) != "battery"
            ):
                return False
            device = device_of(entry)
            entry_labels = set(entry.labels) | (set(device.labels) if device else set())
            return (
                entry.platform in integrations
                or ha_area_id(entry) in areas
                or bool(entry_labels & labels)
                or entry.device_id in devices
            )

        def build(entity_id: str, settings: dict[str, Any], added: bool) -> MonitoredDevice:
            entry = ent_reg.async_get(entity_id)
            auto_name = native_name(entry, entity_id)
            auto_area = area_name(ha_area_id(entry))
            auto_type = library_type(entry)
            if settings.get(ATTR_RECHARGEABLE):
                battery_type = RECHARGEABLE_TYPE
            else:
                battery_type = (settings.get(ATTR_BATTERY_TYPE) or "").strip() or auto_type
            last_seen = last_seen_of(entry)
            return MonitoredDevice(
                entity_id=entity_id,
                name=(settings.get(ATTR_NAME) or "").strip() or auto_name,
                battery_type=battery_type,
                area=area_name(settings.get(ATTR_AREA)) or auto_area,
                last_seen_entity=(
                    last_seen.entity_id if last_seen and last_seen.disabled_by is None else None
                ),
                auto_name=auto_name,
                auto_area=auto_area,
                auto_type=auto_type,
                added=added,
                matched=matches_filters(entry),
                last_seen_disabled=bool(last_seen and last_seen.disabled_by is not None),
            )

        result: list[MonitoredDevice] = []
        seen: set[str] = set()
        missing: set[str] = set()

        for item in options.get(CONF_DEVICES, []):
            entity_id = item.get(ATTR_ENTITY_ID)
            if not entity_id or entity_id in seen or entity_id in exclude:
                continue
            if (
                ent_reg.async_get(entity_id) is None
                and self.hass.states.get(entity_id) is None
                # While starting, an entity without a registry entry may not be loaded yet.
                and self.hass.state is CoreState.running
            ):
                missing.add(entity_id)
                continue
            result.append(build(entity_id, item, added=True))
            seen.add(entity_id)

        if integrations or areas or labels or devices:
            found: list[MonitoredDevice] = []
            for entry in ent_reg.entities.values():
                if entry.entity_id in seen or entry.entity_id in exclude:
                    continue
                if matches_filters(entry):
                    found.append(build(entry.entity_id, overrides.get(entry.entity_id, {}), added=False))
            found.sort(key=lambda d: (d.name.casefold(), d.entity_id))
            result.extend(found)
            seen.update(d.entity_id for d in found)

        self.devices = result
        self._missing = missing
        by_entity: dict[str, list[MonitoredDevice]] = {}
        for dev in result:
            by_entity.setdefault(dev.entity_id, []).append(dev)
            if dev.last_seen_entity:
                by_entity.setdefault(dev.last_seen_entity, []).append(dev)
        self._by_entity = by_entity
        # Forget timers of devices that are no longer monitored.
        for entity_id in list(self._timers):
            if entity_id not in seen:
                self._timers.pop(entity_id)[1]()

    @callback
    def _track(self) -> None:
        if self._track_unsub:
            self._track_unsub()
            self._track_unsub = None
        if self._by_entity or self._missing:
            self._track_unsub = async_track_state_change_event(
                self.hass, list(self._by_entity) + sorted(self._missing), self._state_changed
            )

    # ------------------------------------------------------------ evaluation

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if entity_id in self._missing and event.data["new_state"] is not None:
            self._delayed_resolve(None)  # a missing battery is back
            return
        for dev in self._by_entity.get(entity_id, []):
            self._evaluate(dev)

    @callback
    def _evaluate_all(self) -> None:
        for dev in self.devices:
            self._evaluate(dev, publish=False)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def _evaluate(self, dev: MonitoredDevice, publish: bool = True) -> None:
        """Update one device's held reading and not-seen flag."""
        mem = self._mem.setdefault(
            dev.entity_id, {"state": None, "last_seen": None, "not_seen": False}
        )
        before = dict(mem)
        now = dt_util.utcnow()
        running = self.hass.state is CoreState.running

        state = self.hass.states.get(dev.entity_id)
        value = number_of(state)
        if value is not None:
            old_raw = mem.get("state")
            if old_raw is not None and float(old_raw) > self.settings.low >= value:
                self._notify_low(dev, state.state)
            mem["state"] = state.state

        if dev.last_seen_entity:
            # Zigbee2MQTT and alike: not seen = silent for the not-seen time while it
            # could have been heard. Time while the last_seen sensor is unavailable
            # (Zigbee2MQTT down) or Home Assistant is down is credited, so restarts,
            # reboots and outages never make a device "not seen".
            seen_at = timestamp_of(self.hass.states.get(dev.last_seen_entity))
            held = _parse(mem.get("last_seen"))
            if seen_at is not None:
                if offline_since := _parse(mem.get("offline_since")):
                    mem["credit"] = float(mem.get("credit") or 0.0) + max(
                        0.0, (now - offline_since).total_seconds()
                    )
                    mem["offline_since"] = None
                if held is None or seen_at > held:
                    # A new message from the device.
                    mem["last_seen"] = seen_at.isoformat()
                    mem["credit"] = 0.0
                    held = seen_at
            elif held is not None and not mem.get("offline_since"):
                mem["offline_since"] = now.isoformat()
            if held is None or mem.get("offline_since"):
                # Can't judge while it can't be heard: keep the current flag.
                not_seen = bool(mem.get("not_seen"))
                due = None
            else:
                due = held + timedelta(seconds=float(mem.get("credit") or 0.0)) + self.settings.not_seen
                not_seen = now >= due
            self._schedule(dev, None if (due is None or not_seen) else due)
        elif value is not None:
            not_seen = False
            mem["unavailable_since"] = None
            self._schedule(dev, None)
        elif (
            running
            and state is not None
            and state.state == STATE_UNAVAILABLE
            and not state.attributes.get("restored")
        ):
            # No last_seen sensor (e.g. SwitchBot, ZHA): not seen = unavailable for
            # the not-seen time, so a short outage (a reload, a Wi-Fi or cloud blip,
            # a hub restart) never counts. Time Home Assistant is down is credited.
            since = _parse(mem.get("unavailable_since"))
            if since is None:
                since = now
                mem["unavailable_since"] = now.isoformat()
            if mem.get("not_seen"):
                # Already not seen (also when flagged by an older version): it
                # stays so until a real reading comes.
                not_seen = True
            else:
                not_seen = now >= since + self.settings.not_seen
            self._schedule(dev, None if not_seen else since + self.settings.not_seen)
        else:
            not_seen = bool(mem.get("not_seen"))
            if running and state is not None and state.state != STATE_UNAVAILABLE:
                # Back, without a reading yet (unknown): the outage is over.
                mem["unavailable_since"] = None
                self._schedule(dev, None)

        if not_seen and not mem.get("not_seen"):
            self._notify_not_seen(dev)
        mem["not_seen"] = not_seen

        if mem != before:
            if (
                mem.get("state") != before.get("state")
                or mem["not_seen"] != before.get("not_seen")
                or mem.get("unavailable_since") != before.get("unavailable_since")
            ):
                self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
            elif self._last_seen_save_unsub is None:
                self._last_seen_save_unsub = async_call_later(
                    self.hass, LAST_SEEN_SAVE_INTERVAL, self._save_last_seen
                )
            if publish:
                async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def _save_last_seen(self, _now: datetime) -> None:
        self._last_seen_save_unsub = None
        self._store.async_delay_save(self._data_to_save, 0)

    @callback
    def _schedule(self, dev: MonitoredDevice, when: datetime | None) -> None:
        """Re-check a device exactly when it passes the not-seen time."""
        current = self._timers.get(dev.entity_id)
        if current and current[0] == when:
            return
        if current:
            current[1]()
            del self._timers[dev.entity_id]
        if when is None:
            return

        @callback
        def _due(_now: datetime) -> None:
            self._timers.pop(dev.entity_id, None)
            self._evaluate(dev)

        self._timers[dev.entity_id] = (
            when,
            async_track_point_in_utc_time(self.hass, _due, when),
        )

    def _data_to_save(self) -> dict[str, Any]:
        # Also keep the readings of hidden (missing) batteries for their return.
        keep = {dev.entity_id for dev in self.devices} | self._missing
        return {
            "observed_at": dt_util.utcnow().isoformat(),
            "devices": {k: v for k, v in self._mem.items() if k in keep},
            "log": self.log,
            "held": self._held,
        }

    # ------------------------------------------------------------ the output

    def snapshot(self) -> tuple[int, dict[str, Any]]:
        """Return (low count, attributes) for the sensor and the card."""
        devices: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        types: set[str] = set()
        total = 0
        for dev in self.devices:
            mem = self._mem.get(dev.entity_id, {})
            raw = mem.get("state")
            not_seen = bool(mem.get("not_seen"))
            reading = raw is not None and not not_seen
            types.add(dev.battery_type)
            # Count exactly as before: the reading itself, 0 when there is none.
            if (float(raw) if reading else 0.0) <= self.settings.low:
                counts[dev.battery_type] = counts.get(dev.battery_type, 0) + 1
                total += 1
            devices.append(
                {
                    "entity_id": dev.entity_id,
                    "name": dev.name,
                    "battery_type": dev.battery_type,
                    "area": dev.area,
                    "state": raw if reading else "0",
                    "value": one_decimal(raw) if reading else 0.0,
                    "reading": reading,
                    "not_seen": not_seen,
                }
            )
        return total, {
            "battery_low_count": [{t: counts.get(t, 0)} for t in sorted(types)],
            "devices": devices,
            "low_threshold": self.settings.low,
        }

    def low_count(self) -> int:
        """Number of batteries at or below the threshold (not seen = 0 %)."""
        return self.snapshot()[0]

    # ------------------------------------------------------------- alerts

    @callback
    def _load_log(self, log: Any, held: Any) -> None:
        """The saved recent alerts; held ones point at their history entries."""
        if isinstance(log, list):
            self.log = [e for e in log if isinstance(e, dict) and e.get("id")][:LOG_SIZE]
        for entry in self.log:
            if entry.get("status") == "sending":
                entry["status"] = "unknown"
                entry["note"] = "Home Assistant stopped before the result came back"
        by_id = {e["id"]: e for e in self.log}
        if isinstance(held, list):
            self._held = [
                by_id.get(h["id"], h) for h in held if isinstance(h, dict) and h.get("id")
            ]

    @callback
    def _log_changed(self) -> None:
        if not self._stopped:
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        async_dispatcher_send(self.hass, SIGNAL_LOG)

    @callback
    def _arm_schedules(self) -> None:
        """The daily reminder (local time) and the end of the current quiet hours."""
        for unsub in self._schedule_unsubs:
            unsub()
        self._schedule_unsubs = []
        at = self.settings.reminder_time
        self._schedule_unsubs.append(
            async_track_time_change(
                self.hass, self._async_reminder, hour=at.hour, minute=at.minute, second=at.second
            )
        )
        self._schedule_quiet_end()

    @callback
    def _schedule_quiet_end(self) -> None:
        """While alerts are held, send them when quiet hours end.

        One exact moment rather than a daily clock time: a daily time inside the
        hour skipped when clocks go forward would not run that day; this moment
        then falls just after the jump.
        """
        if self._quiet_end_unsub:
            self._quiet_end_unsub()
            self._quiet_end_unsub = None
        if not self._held or not self.settings.quiet or self._stopped:
            return
        now = dt_util.now()
        end = datetime.combine(now.date(), self.settings.quiet_to, tzinfo=now.tzinfo)
        if end <= now:
            end = datetime.combine(now.date() + timedelta(days=1), self.settings.quiet_to, tzinfo=now.tzinfo)
        self._quiet_end_unsub = async_track_point_in_utc_time(
            self.hass, self._quiet_ended, dt_util.as_utc(end)
        )

    @callback
    def _quiet_ended(self, _now: datetime) -> None:
        self._quiet_end_unsub = None
        self._flush_held()

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        """Whether now is inside the quiet hours (the end time itself is outside)."""
        s = self.settings
        if not s.quiet or s.quiet_from == s.quiet_to:
            return False
        t = dt_util.as_local(now or dt_util.utcnow()).time()
        if s.quiet_from < s.quiet_to:
            return s.quiet_from <= t < s.quiet_to
        return t >= s.quiet_from or t < s.quiet_to

    @callback
    def _alert(
        self, kind: str, message: str, dev: MonitoredDevice | None = None, detail: str = ""
    ) -> None:
        """Send an alert now, or hold it until quiet hours end; keep it in the history."""
        entry: dict[str, Any] = {
            "id": uuid4().hex,
            "time": dt_util.utcnow().isoformat(),
            "kind": kind,
            "entity_id": dev.entity_id if dev else None,
            "name": dev.name if dev else "",
            "area": dev.area if dev else "",
            "detail": detail,
            "message": message,
            "status": "",
            "note": "",
            "targets": [],
        }
        self.log.insert(0, entry)
        del self.log[LOG_SIZE:]
        if self.in_quiet_hours():
            entry["status"] = "held"
            entry["note"] = self._quiet_note()
            self._held.append(entry)
            self._schedule_quiet_end()
        elif self.hass.state is not CoreState.running:
            # Notify services (e.g. the mobile app) may not exist yet.
            entry["status"] = "held"
            entry["note"] = WAIT_FOR_START
            self._held.append(entry)
        else:
            self._deliver(entry)
        self._log_changed()

    def _quiet_note(self) -> str:
        return f"quiet hours until {self.settings.quiet_to:%H:%M}"

    @callback
    def _flush_held(self, _now: datetime | None = None) -> None:
        """Quiet hours are over (or Home Assistant has started): send what is
        still true, skip the rest."""
        if not self._held or self._stopped or self.hass.state is not CoreState.running:
            return
        if self.in_quiet_hours():
            for entry in self._held:
                if entry.get("note") == WAIT_FOR_START:
                    entry["note"] = self._quiet_note()
            self._schedule_quiet_end()
            self._log_changed()
            return
        held, self._held = self._held, []
        sent: set[tuple[str, str | None]] = set()
        at = dt_util.as_local(dt_util.utcnow())
        for entry in reversed(held):  # newest first
            key = (entry.get("kind", ""), entry.get("entity_id"))
            reason = "already sent once" if key in sent else self._no_longer_true(entry)
            if reason:
                entry["status"] = "skipped"
                entry["note"] = reason
                continue
            sent.add(key)
            entry["note"] = (
                "sent once Home Assistant had started"
                if entry.get("note") == WAIT_FOR_START
                else f"held for quiet hours, sent at {at:%H:%M}"
            )
            self._deliver(entry)  # replaces the note if nothing can be sent
        self._log_changed()

    def _no_longer_true(self, entry: dict[str, Any]) -> str | None:
        """Why a held alert is not sent any more (None = send it)."""
        s = self.settings
        kind = entry.get("kind")
        if kind == "reminder":
            if not s.reminder:
                return "the reminder is off"
            count = self.low_count()
            if count == 0:
                return "nothing low or not seen any more"
            entry["message"] = self._reminder_text(count)
            entry["detail"] = str(count)
            return None
        if kind not in ("low", "not_seen"):
            return None
        if kind == "low" and not s.alert_low:
            return "the low battery alert is off"
        if kind == "not_seen" and not s.alert_not_seen:
            return "the stopped reporting alert is off"
        if entry.get("entity_id") not in {d.entity_id for d in self.devices}:
            return "the battery is no longer on the list"
        mem = self._mem.get(entry["entity_id"], {})
        if kind == "not_seen":
            return None if mem.get("not_seen") else "it reported again"
        raw = mem.get("state")
        if raw is None or float(raw) > s.low:
            return f"the battery is above {s.low} % again"
        return None

    @callback
    def _deliver(self, entry: dict[str, Any]) -> asyncio.Task[None] | None:
        """Send an entry's message to the saved notify services."""
        targets = notify_services(self.entry.options)
        entry["targets"] = []
        if not targets:
            _LOGGER.debug("No notify service configured, not sending: %s", entry["message"])
            entry["status"] = "not_sent"
            entry["note"] = "no notify service is set"
            return None
        entry["status"] = "sending"
        # In the background: a slow notify service never holds up Home Assistant.
        task = self.hass.async_create_background_task(
            self._async_deliver(entry, targets), f"{DOMAIN} notification"
        )
        self._deliveries.add(task)
        task.add_done_callback(self._deliveries.discard)
        return task

    async def _async_deliver(self, entry: dict[str, Any], targets: list[str]) -> None:
        results: list[dict[str, Any]] = []
        for service in targets:
            domain, _, name = service.partition(".")
            data: dict[str, Any] = {"title": NOTIFY_TITLE, "message": entry["message"]}
            if domain != "notify" or not name:
                results.append({"service": service, "ok": False, "error": "not a notify service"})
                continue
            if name == SEND_MESSAGE:
                results.append({"service": service, "ok": False, "error": "needs a notify entity: choose one instead"})
                continue
            if not self.hass.services.has_service(domain, name):
                # Not a notify service: a notify entity, sent with notify.send_message.
                state = self.hass.states.get(service)
                if state is None:
                    _LOGGER.warning("Notify target %s not found", service)
                    results.append({"service": service, "ok": False, "error": "doesn't exist"})
                    continue
                if state.state == STATE_UNAVAILABLE:
                    # Home Assistant skips an unavailable entity without an error.
                    results.append({"service": service, "ok": False, "error": "is unavailable"})
                    continue
                name = SEND_MESSAGE
                data["entity_id"] = service
            try:
                await self.hass.services.async_call(domain, name, data, blocking=True)
            except Exception as err:  # noqa: BLE001 - shown on the settings page
                _LOGGER.warning("Battery alert not sent with %s: %s", service, err)
                results.append({"service": service, "ok": False, "error": str(err) or type(err).__name__})
            else:
                results.append({"service": service, "ok": True})
        ok = sum(1 for r in results if r["ok"])
        entry["targets"] = results
        entry["status"] = "sent" if ok == len(results) else "partly" if ok else "not_sent"
        self._log_changed()

    @staticmethod
    def _where(dev: MonitoredDevice) -> str:
        """The alerts' location part; left out when the device has no area."""
        return f" located in the {dev.area.upper()}" if dev.area else ""

    @callback
    def _notify_low(self, dev: MonitoredDevice, raw: str) -> None:
        if not self.settings.alert_low:
            return
        self._alert(
            "low",
            f"The battery level for the device {dev.name.upper()}{self._where(dev)}, "
            f"with battery type: {dev.battery_type.upper()}, has "
            f"dropped to {alert_percent(raw)}%. Consider "
            f"{recharge_word(dev.battery_type)} soon!",
            dev,
            f"{alert_percent(raw)} %",
        )

    @callback
    def _notify_not_seen(self, dev: MonitoredDevice) -> None:
        if not self.settings.alert_not_seen:
            return
        self._alert(
            "not_seen",
            f"The device {dev.name.upper()}{self._where(dev)}, with "
            f"battery type: {dev.battery_type.upper()}, has stopped reporting. Its "
            f"battery may be dead. Consider {recharge_word(dev.battery_type)} it soon!",
            dev,
        )

    def _reminder_text(self, count: int) -> str:
        one = count == 1
        return (
            f"You still have {count} {'device' if one else 'devices'} with the battery level below "
            f"{self.settings.low}%. Consider replacing or recharging {'it' if one else 'them'} soon!"
        )

    @callback
    def _async_reminder(self, now: datetime) -> None:
        s = self.settings
        if not s.reminder or dt_util.as_local(now).weekday() not in s.reminder_days:
            return
        count = self.low_count()
        if count > 0:
            self._alert("reminder", self._reminder_text(count), detail=str(count))
