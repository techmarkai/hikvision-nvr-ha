"""Diagnostics dump for bug reports."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import HikvisionConfigEntry

REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_HOST, "serial", "mac", "ip_address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikvisionConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "device_info": coordinator.api.device_info,
            "channels": [
                {
                    "id": channel.id,
                    "name": channel.name,
                    "online": channel.online,
                    "streams": channel.streams,
                    "ptz": channel.ptz,
                    "ip_address": channel.ip_address,
                }
                for channel in coordinator.api.channels
            ],
            "storage": coordinator.data.get("storage", []),
            "active_events": [
                {"channel": channel, "type": event_type}
                for channel, event_type in coordinator.active_events
            ],
            "recent_events": [
                {
                    "type": event.type,
                    "state": event.state,
                    "channel": event.channel,
                    "timestamp": event.timestamp.isoformat(),
                    "disk": event.disk,
                    "post_count": event.post_count,
                }
                for event in list(coordinator.recent_events)[-25:]
            ],
        },
        REDACT,
    )
