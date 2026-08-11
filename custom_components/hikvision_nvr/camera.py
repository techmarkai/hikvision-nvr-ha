"""Camera platform: one entity per NVR channel."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CHANNELS,
    CONF_SNAPSHOT_STREAM,
    CONF_STREAM,
    STREAM_MAIN,
    STREAM_SUB,
)
from .coordinator import HikvisionConfigEntry, HikvisionCoordinator
from .entity import HikvisionEntity
from .isapi import Channel, HikvisionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    enabled = entry.options.get(CONF_CHANNELS)
    async_add_entities(
        HikvisionCamera(coordinator, channel, entry)
        for channel in coordinator.api.channels
        if enabled is None or str(channel.id) in enabled
    )


class HikvisionCamera(HikvisionEntity, Camera):
    """Live view for one channel."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: HikvisionCoordinator,
        channel: Channel,
        entry: HikvisionConfigEntry,
    ) -> None:
        HikvisionEntity.__init__(self, coordinator, channel)
        Camera.__init__(self)
        self._attr_unique_id = f"{coordinator.device_id}_{channel.id}_camera"

        # Home Assistant defaults to RTSP over UDP with the camera's own
        # timestamps. Hikvision encoders emit erratic DTS and drop packets under
        # UDP, which surfaces as the stream worker dying with "No dts in N
        # consecutive packets" on whichever channel is busiest. TCP plus
        # wallclock timestamps is the standard remedy.
        self.stream_options = {
            "rtsp_transport": "tcp",
            "use_wallclock_as_timestamps": True,
        }

        wanted = entry.options.get(CONF_STREAM, STREAM_MAIN)
        # Not every channel has a sub-stream; never point at one that does
        # not exist or the stream simply fails to start.
        self._stream = wanted if wanted in channel.streams else channel.streams[0]
        snapshot = entry.options.get(CONF_SNAPSHOT_STREAM, STREAM_SUB)
        self._snapshot_stream = (
            snapshot if snapshot in channel.streams else channel.streams[0]
        )

    @property
    def available(self) -> bool:
        return super().available and self.channel.online

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "channel": self.channel.id,
            "stream": self._stream,
            "camera_ip": self.channel.ip_address,
        }

    async def stream_source(self) -> str:
        return self.coordinator.api.rtsp_url(self.channel.id, self._stream)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            return await self.coordinator.api.async_snapshot(
                self.channel.id, self._snapshot_stream
            )
        except HikvisionError as err:
            _LOGGER.debug("Snapshot failed for channel %s: %s", self.channel.id, err)
            return None
