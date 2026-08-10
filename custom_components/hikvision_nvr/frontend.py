"""Serve and register the card and the sidebar panel.

The card is shipped inside the integration and loaded with ``add_extra_js_url``,
so it works the moment the integration is installed -- no copying into ``www/``
and no manual Lovelace resource entry.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Bumping this busts the browser cache for both files.
FRONTEND_VERSION = "1.1.0"

URL_BASE = f"/{DOMAIN}_frontend"
CARD_URL = f"{URL_BASE}/hikvision-nvr-card.js?v={FRONTEND_VERSION}"
PANEL_URL = f"{URL_BASE}/hikvision-nvr-panel.js?v={FRONTEND_VERSION}"
PANEL_PATH = "hikvision-nvr"


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Register the static files, the card and the sidebar panel. Idempotent."""
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    hass.data[f"{DOMAIN}_frontend"] = True

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=False
            )
        ]
    )

    # Loads the card on every dashboard, so `type: custom:hikvision-nvr-card`
    # just works without a Resources entry.
    add_extra_js_url(hass, CARD_URL)

    async_register_built_in_panel(
        hass,
        "custom",
        sidebar_title="Cameras",
        sidebar_icon="mdi:cctv",
        frontend_url_path=PANEL_PATH,
        require_admin=False,
        config={
            "_panel_custom": {
                "name": "hikvision-nvr-panel",
                "module_url": PANEL_URL,
                "embed_iframe": False,
                "trust_external": False,
            }
        },
    )
    _LOGGER.debug("Hikvision NVR frontend registered at %s", URL_BASE)
