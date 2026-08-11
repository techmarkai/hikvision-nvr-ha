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
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import async_register_api
from .const import (
    CONF_EVENTS,
    CONF_RTSP_PORT,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_RTSP_PORT,
    DOMAIN,
    EVENT_CLASSES,
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
    await _async_check_notifications(hass, entry, api)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def _async_check_notifications(
    hass: HomeAssistant, entry: HikvisionConfigEntry, api: HikvisionISAPI
) -> None:
    """Warn when the NVR records detections but never announces them.

    A trigger linked only to "record" fills the disk while Home Assistant hears
    nothing, so motion sensors sit permanently off. It looks like a broken
    integration and is really a device setting, so say so plainly and point at
    the service that fixes it.
    """
    issue_id = f"notifications_disabled_{entry.entry_id}"
    missing = await api.async_missing_notifications()
    if not missing:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="notifications_disabled",
        translation_placeholders={
            "count": str(len(missing)),
            "triggers": ", ".join(missing[:6]) + ("..." if len(missing) > 6 else ""),
        },
    )


@callback
def _async_remove_obsolete_entities(
    hass: HomeAssistant, entry: HikvisionConfigEntry, coordinator: HikvisionCoordinator
) -> None:
    """Remove event entities that should no longer exist.

    Two cases:

    NVR-level entities for per-channel events. Before capabilities were read
    from the device, an event arriving without a channel id created an entity on
    the NVR itself -- this firmware emits videoloss that way every few seconds,
    so a "Home Video loss" appeared that nothing intended. Those events are now
    attributed to the channels that declare them, leaving the old entity
    permanently unavailable.

    Event types the user has switched off in the options. Disabling them would
    leave rows behind in the entity list, which is the opposite of the point --
    if you asked for fewer sensors you should get fewer sensors.
    """
    registry = er.async_get(hass)
    device_prefix = f"{coordinator.device_id}_0_"
    chosen = entry.options.get(CONF_EVENTS)
    # Anything that is not an event sensor: connectivity, disks, system values.
    keep_suffixes = ("_online", "_problem", "_usage", "_free", "_uptime")

    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = entity.unique_id

        if unique_id.startswith(device_prefix):
            event_type = unique_id[len(device_prefix) :]
            if event_type and event_type not in DEVICE_EVENTS:
                _LOGGER.debug(
                    "Removing %s: %s is reported per channel, not per device",
                    entity.entity_id,
                    event_type,
                )
                registry.async_remove(entity.entity_id)
                continue

        if chosen is None or entity.domain != "binary_sensor":
            continue
        if unique_id.endswith(keep_suffixes):
            continue
        # unique_id is "<device>_<channel>_<event type>"; the event type is
        # whatever follows the channel number.
        parts = unique_id.rsplit("_", 1)
        event_type = parts[-1] if len(parts) == 2 else ""
        if event_type and event_type in EVENT_CLASSES and event_type not in chosen:
            _LOGGER.debug(
                "Removing %s: %s is switched off in the options",
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
