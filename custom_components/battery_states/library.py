"""Battery type library (Battery Notes' community library, MIT licence).

The device matching below follows Battery Notes' own rules
(custom_components/battery_notes/library.py, get_device_battery_details) so a
device gets the same battery type it would get there. A copy of the library
ships with this integration and is refreshed with each release. Battery Notes
asks other projects not to download the library from its repository, so this
integration never downloads it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BUNDLED = Path(__file__).parent / "library" / "library.json"
# Versions up to 1.2.6 kept a downloaded copy here; it is removed on load.
OLD_DOWNLOAD_STORE_KEY = f"{DOMAIN}.library"


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

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self._by_manufacturer: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        """Use the copy that ships with the integration."""
        await Store(self.hass, 1, OLD_DOWNLOAD_STORE_KEY).async_remove()
        data = await self.hass.async_add_executor_job(
            lambda: json.loads(BUNDLED.read_text(encoding="utf-8"))
        )
        if _valid(data):
            self._index(data)
        else:
            _LOGGER.error("The bundled battery library is not valid")

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
