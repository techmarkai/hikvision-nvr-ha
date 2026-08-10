"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HikvisionCoordinator
from .isapi import Channel


class HikvisionEntity(CoordinatorEntity[HikvisionCoordinator]):
    """Base for anything belonging to an NVR."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HikvisionCoordinator, channel: Channel | None = None
    ) -> None:
        super().__init__(coordinator)
        self.channel = channel
        info = coordinator.api.device_info
        nvr_id = (DOMAIN, coordinator.device_id)

        if channel is None:
            self._attr_device_info = DeviceInfo(
                identifiers={nvr_id},
                name=info.get("name") or coordinator.api.host,
                manufacturer="Hikvision",
                model=info.get("model"),
                sw_version=info.get("firmware"),
                serial_number=info.get("serial"),
                configuration_url=coordinator.api.base_url,
                connections=(
                    {("mac", info["mac"])} if info.get("mac") else set()
                ),
            )
        else:
            # Each camera is its own device, hung off the NVR. Lets users assign
            # cameras to areas independently.
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{coordinator.device_id}_{channel.id}")},
                name=channel.name,
                manufacturer="Hikvision",
                model=f"{info.get('model', 'NVR')} channel {channel.id}",
                via_device=nvr_id,
                configuration_url=(
                    f"http://{channel.ip_address}" if channel.ip_address else None
                ),
            )
