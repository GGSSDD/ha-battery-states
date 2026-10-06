"""Battery type library (Battery Notes' community library, MIT licence).

The device matching below follows Battery Notes' own rules
(custom_components/battery_notes/library.py, get_device_battery_details) so a
device gets the same battery type it would get there. A copy of the library
ships with this integration; a newer one is downloaded once a week and kept.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LIBRARY_URL = (
    "https://raw.githubusercontent.com/andrew-codechimp/HA-Battery-Notes/main/library/library.json"
)
REFRESH_INTERVAL = timedelta(days=7)
FIRST_DOWNLOAD_DELAY = 60  # seconds after setup, so startup isn't slowed
BUNDLED = Path(__file__).parent / "library" / "library.json"
LIBRARY_STORE_KEY = f"{DOMAIN}.library"


def _cf(value: Any) -> str:
    return str(value or "").casefold()


def _valid(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("devices"), list)
        and all(
            isinstance(d, dict) and d.get("manufacturer") and d.get("model") and d.get("battery_type")
            for d in data["devices"]
        )
    )


class BatteryLibrary:
    """Looks up a device's battery type by manufacturer / model / model_id / hw_version."""

    def __init__(self, hass: HomeAssistant, on_update: Callable[[], None]) -> None:
        """Initialize."""
        self.hass = hass
        self._on_update = on_update
        self._store: Store[dict[str, Any]] = Store(hass, 1, LIBRARY_STORE_KEY)
        self._by_manufacturer: dict[str, list[dict[str, Any]]] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._task: asyncio.Task[None] | None = None

    async def async_load(self) -> None:
        """Use the downloaded copy if there is one, else the bundled copy."""
        cached = await self._store.async_load()
        if _valid(cached):
            self._index(cached)
            return
        data = await self.hass.async_add_executor_job(
            lambda: json.loads(BUNDLED.read_text(encoding="utf-8"))
        )
        if _valid(data):
            self._index(data)
        else:
            _LOGGER.error("The bundled battery library is not valid")

    @callback
    def async_start_updates(self) -> None:
        """Download a newer library shortly after start and then weekly."""
        self._unsubs.append(
            async_call_later(self.hass, FIRST_DOWNLOAD_DELAY, self._scheduled_download)
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._scheduled_download, REFRESH_INTERVAL)
        )

    @callback
    def async_stop(self) -> None:
        """Stop the updates (also a download in progress)."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    @callback
    def _scheduled_download(self, _now: Any) -> None:
        if self._task is not None and not self._task.done():
            return  # still downloading
        self._task = self.hass.async_create_background_task(
            self._async_download(), f"{DOMAIN} library"
        )

    async def _async_download(self) -> None:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(60):
                resp = await session.get(LIBRARY_URL)
                resp.raise_for_status()
                data = json.loads(await resp.text())
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Battery library download failed, keeping the current copy: %s", err)
            return
        except Exception:  # noqa: BLE001 - a background refresh must never fail loudly
            _LOGGER.debug("Battery library download failed, keeping the current copy", exc_info=True)
            return
        if not _valid(data):
            _LOGGER.warning("Downloaded battery library is not valid, keeping the current copy")
            return
        await self._store.async_save(data)
        self._index(data)
        self._on_update()

    def _index(self, data: dict[str, Any]) -> None:
        index: dict[str, list[dict[str, Any]]] = {}
        for device in data["devices"]:
            index.setdefault(_cf(device["manufacturer"]), []).append(device)
        self._by_manufacturer = index

    # ------------------------------------------------- Battery Notes' matching

    def battery_type(
        self,
        manufacturer: str | None,
        model: str | None,
        model_id: str | None,
        hw_version: str | None,
    ) -> str | None:
        """The library's battery type (type only), or None if unknown."""
        if not manufacturer:
            return None
        candidates = [
            x for x in self._by_manufacturer.get(_cf(manufacturer), []) if self._basic_match(x, model)
        ]
        if not candidates:
            return None

        if model_id is None and hw_version is None:
            generic = [x for x in candidates if x.get("model_id") is None and x.get("hw_version") is None]
            if not generic:
                return None
            candidates = generic
        else:
            candidates = [
                x
                for x in candidates
                if not (model_id is None and x.get("model_id") is not None)
                and not (hw_version is None and x.get("hw_version") is not None)
                and not (
                    model_id is not None
                    and x.get("model_id") is not None
                    and _cf(x["model_id"]) != _cf(model_id)
                )
                and not (
                    hw_version is not None
                    and x.get("hw_version") is not None
                    and _cf(x["hw_version"]) != _cf(hw_version)
                )
            ]
            if not candidates:
                return None

        if len(candidates) > 1:
            partial = [x for x in candidates if self._partial_match(x, model_id, hw_version)]
            if partial:
                candidates = partial
                if len(candidates) > 1:
                    full = [
                        x
                        for x in candidates
                        if _cf(x.get("hw_version")) == _cf(hw_version)
                        and _cf(x.get("model_id")) == _cf(model_id)
                    ]
                    if full:
                        candidates = full

        first = candidates[0]
        keys = ("manufacturer", "model", "model_id", "hw_version", "model_match_method", "battery_type", "battery_quantity")
        if any(any(x.get(k) != first.get(k) for k in keys) for x in candidates[1:]):
            return None  # ambiguous, as in Battery Notes
        battery_type = str(first["battery_type"]).strip()
        if not battery_type or battery_type.casefold() == "manual":
            return None
        return battery_type

    @staticmethod
    def _basic_match(x: dict[str, Any], model: str | None) -> bool:
        method = x.get("model_match_method")
        lib_model = _cf(x["model"])
        dev_model = _cf(model)
        if method == "startswith":
            return dev_model.startswith(lib_model)
        if method == "endswith":
            return dev_model.endswith(lib_model)
        if method == "contains":
            return lib_model in dev_model
        if method:
            return False
        return lib_model == dev_model

    @staticmethod
    def _partial_match(x: dict[str, Any], model_id: str | None, hw_version: str | None) -> bool:
        if hw_version is None and model_id is None:
            return x.get("hw_version") is None and x.get("model_id") is None
        if (
            hw_version is not None
            and model_id is not None
            and _cf(x.get("hw_version")) == _cf(hw_version)
            and _cf(x.get("model_id")) == _cf(model_id)
        ):
            return True
        if hw_version is not None and _cf(x.get("hw_version")) == _cf(hw_version):
            return True
        if model_id is not None and _cf(x.get("model_id")) == _cf(model_id):
            return True
        return False
