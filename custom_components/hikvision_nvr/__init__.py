"""The Hikvision NVR integration."""

from __future__ import annotations

import logging

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import async_register_api
from .const import (
    CONF_RTSP_PORT,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_RTSP_PORT,
)
from .coordinator import HikvisionConfigEntry, HikvisionCoordinator
from .frontend import async_setup_frontend
from .isapi import AuthError, ConnectionFailed, HikvisionError, HikvisionISAPI
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CAMERA, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the REST API, services and frontend once, not per entry."""
    async_register_api(hass)
    async_setup_services(hass)
    await async_setup_frontend(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Set up one NVR."""
    api = HikvisionISAPI(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        port=entry.data[CONF_PORT],
        rtsp_port=entry.data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
        use_ssl=entry.data.get(CONF_USE_SSL, False),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )

    try:
        await api.async_connect()
    except AuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (ConnectionFailed, HikvisionError) as err:
        raise ConfigEntryNotReady(f"Cannot connect to {api.host}: {err}") from err

    coordinator = HikvisionCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.start_event_listener()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload one NVR. The event task is owned by the entry and dies with it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(
    hass: HomeAssistant, entry: HikvisionConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
