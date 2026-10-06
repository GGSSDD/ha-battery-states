"""HA's brands service serves the integration's own icon from brand/."""
import pathlib

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

import custom_components.battery_states as integration

BRAND = pathlib.Path(integration.__file__).parent / "brand"  # the copy under test


@pytest.mark.parametrize(("image", "file"), [("icon.png", "icon.png"), ("icon@2x.png", "icon@2x.png"),
                                             ("dark_icon.png", "dark_icon.png"), ("dark_icon@2x.png", "dark_icon@2x.png"),
                                             ("logo.png", "icon.png"), ("dark_logo@2x.png", "dark_icon@2x.png")])
async def test_brand_images_served(hass: HomeAssistant, hass_client, image, file) -> None:
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    resp = await client.get(f"/api/brands/integration/battery_states/{image}")
    assert resp.status == 200, await resp.text()
    assert resp.content_type == "image/png"
    assert await resp.read() == (BRAND / file).read_bytes()
