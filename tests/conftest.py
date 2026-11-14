"""Test setup: the integration under test is this repo's custom_components/battery_states."""
import pathlib
import sys

import pytest

_root = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(_root))
import custom_components  # noqa: E402

# The test package has its own custom_components folder; this repo's one wins.
custom_components.__path__ = [str(_root / "custom_components"), *custom_components.__path__]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield

