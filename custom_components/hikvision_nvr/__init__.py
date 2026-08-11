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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import async_register_api
from .const import (
    CONF_RTSP_PORT,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_RTSP_PORT,
    DOMAIN,
)
from .coordinator import HikvisionConfigEntry, HikvisionCoordinator
from .frontend import async_setup_frontend
from .isapi import (
    DEVICE_EVENTS,
    AuthError,
    ConnectionFailed,
    HikvisionError,
    HikvisionISAPI,
)
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

    # Register the NVR itself before the platforms register cameras that point
    # at it with via_device -- HA warns (and from 2025.12 will fail) if a
    # via_device target does not exist yet.
    info = api.device_info
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.device_id)},
        connections={(dr.CONNECTION_NETWORK_MAC, info["mac"])} if info.get("mac") else set(),
        name=info.get("name") or api.host,
        manufacturer="Hikvision",
        model=info.get("model"),
        sw_version=info.get("firmware"),
        serial_number=info.get("serial"),
        configuration_url=api.base_url,
    )

    _async_remove_obsolete_entities(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


@callback
def _async_remove_obsolete_entities(
    hass: HomeAssistant, entry: HikvisionConfigEntry, coordinator: HikvisionCoordinator
) -> None:
    """Drop NVR-level entities for events that belong to a channel.

    Before capabilities were read from the device, an event arriving without a
    channel id created an entity on the NVR itself -- this firmware emits
    videoloss that way every few seconds, so a "Home Video loss" appeared that
    nothing intended. Those events are now attributed to the channels that
    declare them, leaving the old entity permanently unavailable. Remove it
    rather than leave the user to guess which one is real.
    """
    registry = er.async_get(hass)
    prefix = f"{coordinator.device_id}_0_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not entity.unique_id.startswith(prefix):
            continue
        event_type = entity.unique_id[len(prefix) :]
        if event_type and event_type not in DEVICE_EVENTS:
            _LOGGER.debug(
                "Removing %s: %s is reported per channel, not per device",
                entity.entity_id,
                event_type,
            )
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload one NVR. The event task is owned by the entry and dies with it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(
    hass: HomeAssistant, entry: HikvisionConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
