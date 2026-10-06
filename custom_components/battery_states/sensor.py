"""Low batteries sensor: the count (home badge, reminder) and the card's data."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BatteryStatesConfigEntry
from .const import SIGNAL_UPDATE
from .entity import BatteryStatesEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: BatteryStatesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor."""
    async_add_entities([LowBatteriesSensor(entry)])


class LowBatteriesSensor(BatteryStatesEntity, SensorEntity):
    """Number of batteries at or below the low limit (a battery not seen counts as 0 %)."""

    # The device list changes with every reading; keep it out of the database.
    _unrecorded_attributes = frozenset(
        {"devices", "battery_low_count", "low_threshold"}
    )

    def __init__(self, entry: BatteryStatesConfigEntry) -> None:
        """Initialize."""
        super().__init__(entry.entry_id, "low_batteries")
        self._monitor = entry.runtime_data
        self._attrs: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Follow the monitor."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._refresh)
        )
        self._refresh(write=False)

    @callback
    def _refresh(self, write: bool = True) -> None:
        count, attrs = self._monitor.snapshot()
        self._attr_native_value = count
        self._attrs = attrs
        if write:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The card's data."""
        return self._attrs
