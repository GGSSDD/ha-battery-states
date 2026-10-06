"""Test setup: the integration under test is this repo's custom_components/battery_states."""
import pathlib
import sys
from unittest.mock import patch

import pytest

_root = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(_root))
import custom_components  # noqa: E402

# The test package has its own custom_components folder; this repo's one wins.
custom_components.__path__ = [str(_root / "custom_components"), *custom_components.__path__]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_library_download():
    """The tests never download the battery library."""
    async def _noop(self):
        return None
    from custom_components.battery_states import library
    with patch.object(library.BatteryLibrary, "_async_download", _noop):
        yield
