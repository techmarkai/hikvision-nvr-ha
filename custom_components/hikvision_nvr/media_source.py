"""Media source: browse NVR recordings in the Home Assistant media browser.

Hierarchy: NVR -> camera -> day -> segment. Playing a segment hands the
frontend an HLS stream produced from the NVR's own playback RTSP URL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import _async_get_stream
from .const import DOMAIN
from .coordinator import HikvisionCoordinator
from .isapi import HikvisionError

_LOGGER = logging.getLogger(__name__)

MAX_DAYS = 30


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return HikvisionMediaSource(hass)


class HikvisionMediaSource(MediaSource):
    """Recordings, browsable."""

    name = "Hikvision NVR"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    # identifier shapes:
    #   ""                                  -> list NVRs
    #   "<device>"                          -> list channels
    #   "<device>/<channel>"                -> list days
    #   "<device>/<channel>/<YYYY-MM-DD>"   -> list segments
    #   "<device>/<channel>/<start>/<end>"  -> playable

    def _coordinators(self) -> dict[str, HikvisionCoordinator]:
        return {
            entry.runtime_data.device_id: entry.runtime_data
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if isinstance(getattr(entry, "runtime_data", None), HikvisionCoordinator)
        }

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = item.identifier.split("/")
        if len(parts) != 4:
            raise Unresolvable(f"Not playable: {item.identifier}")
        device_id, channel, start_raw, end_raw = parts
        coordinator = self._coordinators().get(device_id)
        if coordinator is None:
            raise Unresolvable(f"Unknown NVR {device_id}")

        start = dt_util.utc_from_timestamp(float(start_raw))
        end = dt_util.utc_from_timestamp(float(end_raw))
        source = coordinator.api.playback_rtsp_url(int(channel), start, end)
        stream = await _async_get_stream(
            self.hass, f"pb-{device_id}-{channel}-{start_raw}-{end_raw}", source
        )
        return PlayMedia(stream.endpoint_url("hls"), "application/vnd.apple.mpegurl")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        identifier = item.identifier or ""
        parts = [part for part in identifier.split("/") if part]

        if not parts:
            return self._browse_root()
        coordinator = self._coordinators().get(parts[0])
        if coordinator is None:
            raise Unresolvable(f"Unknown NVR {parts[0]}")
        if len(parts) == 1:
            return self._browse_channels(coordinator)
        if len(parts) == 2:
            return self._browse_days(coordinator, int(parts[1]))
        if len(parts) == 3:
            return await self._browse_segments(coordinator, int(parts[1]), parts[2])
        raise Unresolvable(f"Cannot browse {identifier}")

    # ---------------------------------------------------------------- levels

    def _node(
        self,
        identifier: str,
        title: str,
        *,
        children: list[BrowseMediaSource] | None = None,
        playable: bool = False,
        thumbnail: str | None = None,
    ) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.VIDEO if playable else MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=title,
            can_play=playable,
            can_expand=not playable,
            children=children or [],
            children_media_class=(
                None
                if playable or not children
                else MediaClass.VIDEO
                if children[0].can_play
                else MediaClass.DIRECTORY
            ),
            thumbnail=thumbnail,
        )

    def _browse_root(self) -> BrowseMediaSource:
        return self._node(
            "",
            "Hikvision NVR",
            children=[
                self._node(coordinator.device_id, coordinator.config_entry.title)
                for coordinator in self._coordinators().values()
            ],
        )

    def _browse_channels(self, coordinator: HikvisionCoordinator) -> BrowseMediaSource:
        return self._node(
            coordinator.device_id,
            coordinator.config_entry.title,
            children=[
                self._node(
                    f"{coordinator.device_id}/{channel.id}",
                    channel.name,
                    thumbnail=(
                        f"/api/hikvision_nvr/{coordinator.device_id}/{channel.id}/snapshot"
                    ),
                )
                for channel in coordinator.api.channels
            ],
        )

    def _browse_days(
        self, coordinator: HikvisionCoordinator, channel: int
    ) -> BrowseMediaSource:
        # Listing days is a pure calendar operation -- querying the NVR 30 times
        # just to grey out empty days is not worth the seconds it would cost.
        today = dt_util.now().date()
        name = next(
            (c.name for c in coordinator.api.channels if c.id == channel), str(channel)
        )
        return self._node(
            f"{coordinator.device_id}/{channel}",
            name,
            children=[
                self._node(
                    f"{coordinator.device_id}/{channel}/{day}",
                    "Today" if day == today else day.strftime("%a %d %b %Y"),
                )
                for day in (today - timedelta(days=offset) for offset in range(MAX_DAYS))
            ],
        )

    async def _browse_segments(
        self, coordinator: HikvisionCoordinator, channel: int, day: str
    ) -> BrowseMediaSource:
        try:
            date = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError as err:
            raise Unresolvable(f"Bad date {day}") from err

        start = dt_util.as_utc(
            datetime.combine(date, datetime.min.time()).replace(
                tzinfo=dt_util.get_default_time_zone()
            )
        )
        end = start + timedelta(days=1)
        try:
            recordings, _ = await coordinator.api.async_search_recordings(
                channel, start, end, max_results=500
            )
        except HikvisionError as err:
            raise Unresolvable(f"Recording search failed: {err}") from err

        children = []
        for recording in recordings:
            local_start = dt_util.as_local(recording.start)
            label = (
                f"{local_start:%H:%M:%S} - {recording.duration}s"
                + (f" ({recording.event_type})" if recording.event_type else "")
            )
            children.append(
                self._node(
                    f"{coordinator.device_id}/{channel}"
                    f"/{recording.start.timestamp()}/{recording.end.timestamp()}",
                    label,
                    playable=True,
                )
            )
        return self._node(
            f"{coordinator.device_id}/{channel}/{day}", day, children=children
        )
