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
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_BASE = f"/{DOMAIN}_frontend"
PANEL_PATH = "hikvision-nvr"
# Set once the frontend is registered; also carries the card and panel URLs,
# so its presence is the "already done" flag.
DATA_URLS = f"{DOMAIN}_frontend_urls"


async def _async_asset_urls(hass: HomeAssistant) -> tuple[str, str]:
    """Card and panel URLs, cache-busted by the integration's own version.

    This was a hand-maintained constant, and it drifted: it sat at 1.8.0 while
    the integration shipped 1.9.0, 1.10.0 and 1.10.1, so the URL never changed
    across three releases. A browser that cached a *failed* module fetch for
    that URL -- which happens if an upgrade is briefly broken -- kept failing,
    because a failed module is cached hard and nothing ever changed the key.

    Deriving it from the manifest means every release is a new URL, so a poisoned
    entry cannot outlive an update, and there is nothing left to forget to bump.
    """
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version or "dev")
    return (
        f"{URL_BASE}/hikvision-nvr-card.js?v={version}",
        f"{URL_BASE}/hikvision-nvr-panel.js?v={version}",
    )


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Register the static files, the card and the sidebar panel. Idempotent."""
    if DATA_URLS in hass.data:
        return
    card_url, panel_url = await _async_asset_urls(hass)
    hass.data[DATA_URLS] = (card_url, panel_url)

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=False
            )
        ]
    )

    # Loads the card on every dashboard, so `type: custom:hikvision-nvr-card`
    # just works without a Resources entry.
    add_extra_js_url(hass, card_url)
    # Lovelace may not be set up yet -- we run during async_setup. Wait for
    # start, so the resource lands whatever the component ordering.
    async_at_started(hass, _async_register_lovelace_resource)

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
                "module_url": panel_url,
                "embed_iframe": False,
                "trust_external": False,
            }
        },
    )
    _LOGGER.debug("Hikvision NVR frontend registered at %s", card_url)


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Also register the card as a Lovelace resource.

    add_extra_js_url injects a script tag into the frontend's index.html, which
    the companion app caches hard -- so on mobile the card can be missing while
    it works in a browser. Lovelace resources are delivered over the websocket
    instead, so they reach a client running a stale index. Belt and braces: the
    module is fetched once either way, and its customElements.define is
    guarded.
    """
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        return  # Lovelace not set up (or YAML mode with none defined)
    if not hasattr(resources, "async_create_item"):
        _LOGGER.debug("Lovelace is in YAML mode; add the card resource yourself")
        return

    card_url = hass.data.get(DATA_URLS, (None, None))[0]
    if not card_url:
        return

    try:
        if not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True

        stem = card_url.split("?")[0]
        for item in resources.async_items():
            if item.get("url", "").split("?")[0] != stem:
                continue
            if item["url"] == card_url:
                return  # already current
            # Same card, older version: point it at the new one.
            await resources.async_update_item(item["id"], {"url": card_url})
            return
        await resources.async_create_item({"res_type": "module", "url": card_url})
        _LOGGER.debug("Registered %s as a Lovelace resource", card_url)
    except Exception as err:  # noqa: BLE001 - never block setup over a resource
        _LOGGER.debug("Could not register the Lovelace resource: %s", err)
