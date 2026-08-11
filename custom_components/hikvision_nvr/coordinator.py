"""Data coordinator and push-event listener for a single NVR."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import replace
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_AUTO_OFF, SCAN_INTERVAL, SIGNAL_EVENT
from .isapi import (
    DEVICE_EVENTS,
    AuthError,
    Event,
    HikvisionError,
    HikvisionISAPI,
)

_LOGGER = logging.getLogger(__name__)

type HikvisionConfigEntry = ConfigEntry[HikvisionCoordinator]


class HikvisionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls slow-moving state, and keeps the push event stream alive."""

    def __init__(
        self, hass: HomeAssistant, entry: HikvisionConfigEntry, api: HikvisionISAPI
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {api.host}",
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        self.api = api
        # (channel, event_type) -> last time we saw it active.
        self.active_events: dict[tuple[int, str], datetime] = {}
        # Short history so apps that poll /events do not miss a burst.
        self.recent_events: deque[Event] = deque(maxlen=200)
        self._listener: asyncio.Task | None = None

    @property
    def device_id(self) -> str:
        """Stable id for this NVR. Used in entity unique_ids -- never change it."""
        return self.api.device_info.get("serial") or self.api.host

    @property
    def slug(self) -> str:
        """URL-safe id, for the REST API and media-source identifiers.

        Hikvision serials contain the model, so they contain a slash
        ("DS-7608NI-K2-8P0000000000AAAA000000000AAAA"). Put that in a URL path and no route matches;
        put it in a media-source identifier and splitting on "/" corrupts it.
        """
        return re.sub(r"[^A-Za-z0-9._-]", "-", self.device_id)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.async_refresh_status()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HikvisionError as err:
            raise UpdateFailed(str(err)) from err

    # ----------------------------------------------------------- event push

    def start_event_listener(self) -> None:
        if self._listener is None or self._listener.done():
            self._listener = self.config_entry.async_create_background_task(
                self.hass, self._event_loop(), f"{DOMAIN}-events-{self.api.host}"
            )

    async def _event_loop(self) -> None:
        """Consume the alert stream forever, reconnecting with backoff."""
        backoff = 5
        while True:
            try:
                async for event in self.api.async_listen_events():
                    backoff = 5
                    self._handle_event(event)
            except asyncio.CancelledError:
                raise
            except AuthError:
                _LOGGER.error("Event stream authentication failed; giving up")
                return
            except Exception as err:  # noqa: BLE001 - never kill the listener
                _LOGGER.debug("Event stream dropped (%s); retrying in %ss", err, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    @callback
    def _channels_for(self, event: Event) -> list[int]:
        """Which channels an event applies to.

        The stream does not always say. This NVR emits videoloss with an empty
        channelID roughly every seven seconds, and videoloss is a per-channel
        capability -- so taken literally, the per-channel sensors would never
        move and an NVR-level one would appear that nothing designed. An
        unattributed event is therefore applied to every channel that declares
        it, which is the only reading that works whether or not the firmware
        names the channel.
        """
        if event.channel:
            return [event.channel]
        if event.type in DEVICE_EVENTS:
            return [0]
        declared = [
            channel_id
            for channel_id, events in self.api.event_capabilities.items()
            if channel_id and event.type in events
        ]
        return declared or [0]

    @callback
    def _handle_event(self, event: Event) -> None:
        self.recent_events.append(event)
        for channel in self._channels_for(event):
            key = (channel, event.type)
            if event.active:
                # Stamped on receipt, not with event.dateTime: NVR clocks drift
                # and a device an hour behind would make every event look
                # expired.
                self.active_events[key] = dt_util.utcnow()
            else:
                self.active_events.pop(key, None)
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_EVENT}_{self.device_id}",
                replace(event, channel=channel),
            )

    @callback
    def is_event_active(self, channel: int, event_type: str) -> bool:
        """True while an event is live.

        Some firmwares only ever send ``active`` heartbeats and never a closing
        ``inactive``, so an entry also expires on its own.
        """
        last = self.active_events.get((channel, event_type))
        if last is None:
            return False
        if dt_util.utcnow() - last > EVENT_AUTO_OFF:
            self.active_events.pop((channel, event_type), None)
            return False
        return True
