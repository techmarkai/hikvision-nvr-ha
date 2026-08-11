"""REST API for third-party apps.

Everything lives under ``/api/hikvision_nvr/`` and uses Home Assistant's own
authentication -- a long-lived access token or an OAuth bearer token in the
``Authorization`` header, exactly like ``/api/states``. No separate account
system, no separate permission model.

See docs/API.md for the full contract.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.stream import Stream, create_stream
from homeassistant.components.stream.const import HLS_PROVIDER
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import HikvisionCoordinator
from .isapi import AuthError, HikvisionError

_LOGGER = logging.getLogger(__name__)

# Playback streams are torn down this long after the last request for them.
STREAM_IDLE = timedelta(minutes=5)
MAX_PLAYBACK_STREAMS = 8
DATA_STREAMS = f"{DOMAIN}_playback_streams"


# --------------------------------------------------------------------- utils


def _camera_entity_id(hass: HomeAssistant, coordinator, channel_id: int) -> str | None:
    """The real camera entity for a channel, from the registry.

    Guessing "camera." + a slugified name breaks the moment a user renames an
    entity, and the frontend needs the true id to hand the camera to Home
    Assistant's own player -- which is what gets WebRTC instead of HLS.
    """
    registry = er.async_get(hass)
    return registry.async_get_entity_id(
        "camera", DOMAIN, f"{coordinator.device_id}_{channel_id}_camera"
    )


def _coordinators(hass: HomeAssistant) -> dict[str, HikvisionCoordinator]:
    """Every loaded NVR, keyed by its URL-safe slug."""
    result = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, HikvisionCoordinator):
            result[coordinator.slug] = coordinator
    return result


def _get_coordinator(hass: HomeAssistant, device_id: str) -> HikvisionCoordinator:
    coordinators = _coordinators(hass)
    if device_id in coordinators:
        return coordinators[device_id]
    # Convenience: allow addressing by raw serial, host or title too.
    for coordinator in coordinators.values():
        if device_id in (
            coordinator.device_id,
            coordinator.api.host,
            coordinator.config_entry.title,
        ):
            return coordinator
    raise web.HTTPNotFound(reason=f"Unknown NVR '{device_id}'")


def _get_channel(coordinator: HikvisionCoordinator, channel_id: str):
    try:
        wanted = int(channel_id)
    except ValueError:
        raise web.HTTPBadRequest(reason="channel must be an integer") from None
    channel = next((c for c in coordinator.api.channels if c.id == wanted), None)
    if channel is None:
        raise web.HTTPNotFound(reason=f"Unknown channel {channel_id}")
    return channel


def _time_range(request: web.Request) -> tuple[datetime, datetime]:
    """Parse ?start=&end=, defaulting to the last 24 hours.

    Accepts ISO 8601 (with or without offset -- naive means the HA timezone)
    or a Unix epoch in seconds.
    """

    def parse(value: str | None, default: datetime) -> datetime:
        if not value:
            return default
        if value.isdigit():
            return datetime.fromtimestamp(int(value), dt_util.UTC)
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            raise web.HTTPBadRequest(reason=f"Cannot parse time '{value}'")
        return dt_util.as_utc(parsed)

    now = dt_util.utcnow()
    start = parse(request.query.get("start"), now - timedelta(days=1))
    end = parse(request.query.get("end"), now)
    if end <= start:
        raise web.HTTPBadRequest(reason="end must be after start")
    if end - start > timedelta(days=31):
        raise web.HTTPBadRequest(reason="range must not exceed 31 days")
    return start, end


def _int_param(request: web.Request, name: str, default: int, maximum: int) -> int:
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        return max(0, min(int(raw), maximum))
    except ValueError:
        raise web.HTTPBadRequest(reason=f"{name} must be an integer") from None


def _handle_errors(func):
    """Turn client exceptions into honest HTTP statuses."""

    async def wrapper(self, request: web.Request, *args, **kwargs):
        try:
            return await func(self, request, *args, **kwargs)
        except web.HTTPException:
            raise
        except AuthError as err:
            raise web.HTTPBadGateway(reason=f"NVR rejected credentials: {err}") from err
        except HikvisionError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err

    wrapper.__name__ = func.__name__
    return wrapper


# ------------------------------------------------------------------- views


class _BaseView(HomeAssistantView):
    requires_auth = True


class DevicesView(_BaseView):
    """GET /api/hikvision_nvr/devices"""

    url = "/api/hikvision_nvr/devices"
    name = "api:hikvision_nvr:devices"

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        devices = []
        for coordinator in _coordinators(hass).values():
            info = coordinator.api.device_info
            devices.append(
                {
                    "id": coordinator.slug,
                    "name": coordinator.config_entry.title,
                    "host": coordinator.api.host,
                    "model": info.get("model"),
                    "firmware": info.get("firmware"),
                    "serial": info.get("serial"),
                    "mac": info.get("mac"),
                    "available": coordinator.last_update_success,
                    "storage": coordinator.data.get("storage", []),
                    "channels": [
                        {
                            "id": c.id,
                            "name": c.name,
                            "online": c.online,
                            "ptz": c.ptz,
                            "streams": c.streams,
                            "ip_address": c.ip_address,
                            "entity_id": _camera_entity_id(hass, coordinator, c.id),
                        }
                        for c in coordinator.api.channels
                    ],
                }
            )
        return self.json({"devices": devices})


class ChannelsView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/channels"""

    url = "/api/hikvision_nvr/{device_id}/channels"
    name = "api:hikvision_nvr:channels"

    @_handle_errors
    async def get(self, request: web.Request, device_id: str) -> web.Response:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        return self.json(
            {
                "channels": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "online": c.online,
                        "ptz": c.ptz,
                        "streams": c.streams,
                        "snapshot_url": (
                            f"/api/hikvision_nvr/{coordinator.slug}/{c.id}/snapshot"
                        ),
                        "live_url": (
                            f"/api/hikvision_nvr/{coordinator.slug}/{c.id}/live"
                        ),
                        "entity_id": _camera_entity_id(
                            request.app["hass"], coordinator, c.id
                        ),
                    }
                    for c in coordinator.api.channels
                ]
            }
        )


class SnapshotView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/snapshot"""

    url = "/api/hikvision_nvr/{device_id}/{channel}/snapshot"
    name = "api:hikvision_nvr:snapshot"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.Response:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        chan = _get_channel(coordinator, channel)
        stream = _int_param(request, "stream", 2, 2) or 2
        image = await coordinator.api.async_snapshot(chan.id, stream)
        return web.Response(
            body=image,
            content_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )


class RecordingsView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/recordings"""

    url = "/api/hikvision_nvr/{device_id}/{channel}/recordings"
    name = "api:hikvision_nvr:recordings"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.Response:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        chan = _get_channel(coordinator, channel)
        start, end = _time_range(request)
        limit = _int_param(request, "limit", 100, 1000)
        offset = _int_param(request, "offset", 0, 100000)

        recordings, total = await coordinator.api.async_search_recordings(
            chan.id,
            start,
            end,
            max_results=limit,
            offset=offset,
            event_type=request.query.get("event_type"),
        )
        base = f"/api/hikvision_nvr/{coordinator.slug}/{chan.id}"
        return self.json(
            {
                "channel": chan.id,
                "name": chan.name,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "total": total,
                "count": len(recordings),
                "offset": offset,
                "recordings": [
                    {
                        **recording.as_dict(),
                        "playback_url": (
                            f"{base}/playback?start={recording.start.isoformat()}"
                            f"&end={recording.end.isoformat()}"
                        ),
                        "download_url": (
                            f"{base}/download?start={recording.start.isoformat()}"
                            f"&end={recording.end.isoformat()}"
                        ),
                    }
                    for recording in recordings
                ],
            }
        )


class TimelineView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/timeline

    Same data as /recordings, collapsed into contiguous blocks -- what a
    scrubber actually needs. Adjacent segments less than ``gap`` seconds apart
    are merged so a day of motion clips becomes a handful of bars.
    """

    url = "/api/hikvision_nvr/{device_id}/{channel}/timeline"
    name = "api:hikvision_nvr:timeline"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.Response:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        chan = _get_channel(coordinator, channel)
        start, end = _time_range(request)
        gap = _int_param(request, "gap", 60, 3600)

        recordings, total = await coordinator.api.async_search_recordings(
            chan.id, start, end, max_results=1000
        )
        blocks: list[dict[str, Any]] = []
        for recording in sorted(recordings, key=lambda r: r.start):
            if blocks and (
                recording.start - dt_util.parse_datetime(blocks[-1]["end"])
            ) <= timedelta(seconds=gap):
                blocks[-1]["end"] = recording.end.isoformat()
                blocks[-1]["segments"] += 1
                if recording.event_type:
                    blocks[-1]["event_types"] = sorted(
                        set(blocks[-1]["event_types"]) | {recording.event_type}
                    )
                continue
            blocks.append(
                {
                    "start": recording.start.isoformat(),
                    "end": recording.end.isoformat(),
                    "segments": 1,
                    "event_types": [recording.event_type] if recording.event_type else [],
                }
            )
        for block in blocks:
            block["duration"] = int(
                (
                    dt_util.parse_datetime(block["end"])
                    - dt_util.parse_datetime(block["start"])
                ).total_seconds()
            )
        return self.json(
            {
                "channel": chan.id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "segments": total,
                "blocks": blocks,
            }
        )


class LiveView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/live -> HLS URL."""

    url = "/api/hikvision_nvr/{device_id}/{channel}/live"
    name = "api:hikvision_nvr:live"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.Response:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass, device_id)
        chan = _get_channel(coordinator, channel)
        stream_id = _int_param(request, "stream", 1, 2) or 1
        if stream_id not in chan.streams:
            stream_id = chan.streams[0]

        source = coordinator.api.rtsp_url(chan.id, stream_id)
        stream = await _async_get_stream(
            hass, f"live-{coordinator.slug}-{chan.id}-{stream_id}", source
        )
        return self.json(
            {
                "channel": chan.id,
                "stream": stream_id,
                "hls_url": stream.endpoint_url(HLS_PROVIDER),
                # Direct RTSP, credentials stripped -- for apps with their own
                # player that can supply the login itself.
                "rtsp_url": coordinator.api.rtsp_url(
                    chan.id, stream_id, credentials=False
                ),
            }
        )


class PlaybackView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/playback -> HLS URL."""

    url = "/api/hikvision_nvr/{device_id}/{channel}/playback"
    name = "api:hikvision_nvr:playback"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.Response:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass, device_id)
        chan = _get_channel(coordinator, channel)
        start, end = _time_range(request)
        if end - start > timedelta(hours=6):
            raise web.HTTPBadRequest(reason="playback range must not exceed 6 hours")

        base = f"/api/hikvision_nvr/{coordinator.slug}/{chan.id}"
        clip = f"{base}/clip/{start.timestamp():.0f}/{end.timestamp():.0f}.mp4"
        payload = {
            "channel": chan.id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": int((end - start).total_seconds()),
            # Play and save both go through the clip endpoint: real MP4, and a
            # path-only URL, so auth/sign_path can sign it for a bare <video> or
            # a download link.
            "mp4_url": clip,
            # Saving uses the NVR bulk endpoint, which is ~50x faster
            # than reading the real-time-paced playback RTSP.
            "download_url": (
                f"{base}/save/{start.timestamp():.0f}/{end.timestamp():.0f}.mp4"
            ),
            "rtsp_url": coordinator.api.playback_rtsp_url(
                chan.id, start, end, credentials=False
            ),
        }

        # HLS only on request. Building it eagerly started a stream worker that
        # dies on these recordings' G.722.1 audio track -- wasted ffmpeg and a
        # traceback in the log on every single playback call.
        if request.query.get("hls") in ("1", "true", "yes"):
            source = coordinator.api.playback_rtsp_url(chan.id, start, end)
            key = (
                f"pb-{coordinator.slug}-{chan.id}"
                f"-{start.timestamp()}-{end.timestamp()}"
            )
            stream = await _async_get_stream(hass, key, source)
            payload["hls_url"] = stream.endpoint_url(HLS_PROVIDER)

        return self.json(payload)


class DownloadView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/download -> video bytes.

    Streams straight off the NVR's disks without buffering in Home Assistant.
    """

    url = "/api/hikvision_nvr/{device_id}/{channel}/download"
    name = "api:hikvision_nvr:download"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.StreamResponse:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        chan = _get_channel(coordinator, channel)
        start, end = _time_range(request)
        if end - start > timedelta(hours=2):
            raise web.HTTPBadRequest(reason="download range must not exceed 2 hours")

        uri = coordinator.api.playback_rtsp_url(chan.id, start, end, credentials=False)
        filename = (
            f"{chan.name.replace(' ', '_')}_{start:%Y%m%d_%H%M%S}"
            f"-{end:%H%M%S}.mp4"
        )
        response = web.StreamResponse(
            headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": f'attachment; filename="{filename}"',
            }
        )
        await response.prepare(request)
        async for chunk in coordinator.api.async_download_stream(uri):
            await response.write(chunk)
        await response.write_eof()
        return response


class ClipView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/clip/{start}/{end}.mp4

    Fragmented MP4, remuxed live by ffmpeg and streamed straight to the player.

    Playback deliberately does not go through Home Assistant's stream
    component. These NVRs record a G.722.1 audio track, PyAV cannot name that
    codec, and HA's stream worker dies on it with
    "'AudioStream' object has no attribute 'name'" -- so HLS playback produces a
    player stuck at 0:00. Dropping audio (-an) sidesteps it entirely, and
    fragmented MP4 starts faster than HLS because there is no segmenting step.

    Times are Unix seconds in the path, not the query string, because signed
    URLs (auth/sign_path) sign an empty parameter list -- a query would break
    the signature that lets <video src> load this without a header.
    """

    url = "/api/hikvision_nvr/{device_id}/{channel}/clip/{start}/{end}.mp4"
    name = "api:hikvision_nvr:clip"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str, start: str, end: str
    ) -> web.StreamResponse:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass, device_id)
        chan = _get_channel(coordinator, channel)
        try:
            start_dt = dt_util.utc_from_timestamp(float(start))
            end_dt = dt_util.utc_from_timestamp(float(end))
        except ValueError:
            raise web.HTTPBadRequest(reason="start and end must be epoch seconds") from None
        if not timedelta(0) < end_dt - start_dt <= timedelta(hours=2):
            raise web.HTTPBadRequest(reason="clip must be 0-2 hours long")

        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        source = coordinator.api.playback_rtsp_url(chan.id, start_dt, end_dt)
        process = await asyncio.create_subprocess_exec(
            get_ffmpeg_manager(hass).binary,
            "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            # Cap stream probing: ffmpeg otherwise spends seconds analysing
            # before it emits anything, which is most of the startup delay.
            "-probesize", "1000000",
            "-analyzeduration", "1000000",
            "-fflags", "+nobuffer+flush_packets",
            "-flags", "low_delay",
            "-i", source,
            "-an",                      # see the docstring: HA/PyAV vs G.722.1
            "-c:v", "copy",             # no transcode; the NVR's bitstream as-is
            "-f", "mp4",
            # Every fragment must begin on a keyframe or the browser cannot
            # start decoding it. Capping fragment duration was tried and made
            # things worse for exactly that reason -- it closes fragments on a
            # timer, producing ones that open mid-GOP.
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        filename = (
            f"{chan.name.replace(' ', '_')}_"
            f"{dt_util.as_local(start_dt):%Y%m%d_%H%M%S}.mp4"
        )
        response = web.StreamResponse(
            headers={
                "Content-Type": "video/mp4",
                "Cache-Control": "no-store",
                # inline so <video> plays it; the card's save button uses an
                # <a download> anchor, which overrides this for a real download.
                "Content-Disposition": f'inline; filename="{filename}"',
            }
        )
        await response.prepare(request)
        disconnected = False
        try:
            while chunk := await process.stdout.read(65536):
                await response.write(chunk)
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            # Viewer seeked or closed the card.
            disconnected = True
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        # A viewer seeking away leaves ffmpeg writing into a closed pipe, so it
        # exits non-zero through no fault of its own. Only a failure while the
        # viewer was still watching is worth a warning.
        # 8 is ffmpeg's "could not write output" -- what a viewer closing the
        # tab looks like from here, not something worth a warning.
        if not disconnected and process.returncode not in (0, None, -9, 8):
            _LOGGER.warning(
                "ffmpeg exited %s for channel %s clip", process.returncode, chan.id
            )
        elif disconnected:
            _LOGGER.debug("clip stopped early for channel %s (viewer left)", chan.id)
        return response


class SaveView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/{channel}/save/{start}/{end}.mp4

    A download, as fast as the network allows.

    The clip endpoint reads the NVR's playback RTSP, which is paced at real
    time: sixty seconds of video takes about 141 seconds to fetch, so a ten
    minute clip outlives any browser's patience and the download appears to
    fail part way. /ISAPI/ContentMgmt/download hands over the same footage at
    roughly 1.1 MB/s -- some fifty times quicker -- but in Hikvision's own IMKH
    container, so ffmpeg remuxes it to MP4 on the way past.

    The NVR only accepts a playbackURI it issued itself, complete with its
    name and size parameters, so the range is resolved to real segments with a
    search first.
    """

    url = "/api/hikvision_nvr/{device_id}/{channel}/save/{start}/{end}.mp4"
    name = "api:hikvision_nvr:save"

    @_handle_errors
    async def get(
        self, request: web.Request, device_id: str, channel: str, start: str, end: str
    ) -> web.StreamResponse:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass, device_id)
        chan = _get_channel(coordinator, channel)
        try:
            start_dt = dt_util.utc_from_timestamp(float(start))
            end_dt = dt_util.utc_from_timestamp(float(end))
        except ValueError:
            raise web.HTTPBadRequest(
                reason="start and end must be epoch seconds"
            ) from None
        if not timedelta(0) < end_dt - start_dt <= timedelta(hours=2):
            raise web.HTTPBadRequest(reason="clip must be 0-2 hours long")

        recordings, _ = await coordinator.api.async_search_recordings(
            chan.id, start_dt, end_dt, max_results=200
        )
        if not recordings:
            raise web.HTTPNotFound(reason="nothing recorded in that range")

        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        process = await asyncio.create_subprocess_exec(
            get_ffmpeg_manager(hass).binary,
            "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-an",
            "-c:v", "copy",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        filename = (
            f"{chan.name.replace(' ', '_')}_"
            f"{dt_util.as_local(start_dt):%Y%m%d_%H%M%S}.mp4"
        )
        response = web.StreamResponse(
            headers={
                "Content-Type": "video/mp4",
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
            }
        )
        await response.prepare(request)

        async def feed() -> None:
            """Pour every segment of the range into ffmpeg, back to back."""
            try:
                for recording in recordings:
                    async for chunk in coordinator.api.async_download_stream(recording):
                        process.stdin.write(chunk)
                        await process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError, HikvisionError):
                pass
            finally:
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()

        pump = asyncio.ensure_future(feed())
        try:
            while chunk := await process.stdout.read(65536):
                await response.write(chunk)
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            pump.cancel()
            if process.returncode is None:
                process.kill()
                await process.wait()
        return response


class EventsView(_BaseView):
    """GET /api/hikvision_nvr/{device_id}/events -> recent pushed events."""

    url = "/api/hikvision_nvr/{device_id}/events"
    name = "api:hikvision_nvr:events"

    @_handle_errors
    async def get(self, request: web.Request, device_id: str) -> web.Response:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        since = request.query.get("since")
        cutoff = dt_util.parse_datetime(since) if since else None
        events = [
            {
                "type": event.type,
                "state": event.state,
                "channel": event.channel,
                "description": event.description,
                "timestamp": event.timestamp.isoformat(),
            }
            for event in coordinator.recent_events
            if cutoff is None or event.timestamp > dt_util.as_utc(cutoff)
        ]
        return self.json(
            {
                "events": events,
                "active": [
                    {"channel": channel, "type": event_type}
                    for (channel, event_type) in coordinator.active_events
                ],
            }
        )


class PtzView(_BaseView):
    """POST /api/hikvision_nvr/{device_id}/{channel}/ptz"""

    url = "/api/hikvision_nvr/{device_id}/{channel}/ptz"
    name = "api:hikvision_nvr:ptz"

    @_handle_errors
    async def post(
        self, request: web.Request, device_id: str, channel: str
    ) -> web.Response:
        coordinator = _get_coordinator(request.app["hass"], device_id)
        chan = _get_channel(coordinator, channel)
        if not chan.ptz:
            raise web.HTTPBadRequest(reason=f"Channel {chan.id} has no PTZ")
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(reason="body must be JSON") from None

        if (preset := body.get("preset")) is not None:
            await coordinator.api.async_ptz_preset(chan.id, int(preset))
        else:
            clamp = lambda v: max(-100, min(100, int(v)))  # noqa: E731
            await coordinator.api.async_ptz(
                chan.id,
                pan=clamp(body.get("pan", 0)),
                tilt=clamp(body.get("tilt", 0)),
                zoom=clamp(body.get("zoom", 0)),
            )
        return self.json({"ok": True})


# ------------------------------------------------------------ stream cache


def _create_stream_kwargs(label: str) -> dict[str, Any]:
    """Build create_stream() kwargs that match this Home Assistant version.

    DynamicStreamSettings has moved between homeassistant.components.stream and
    .stream.core across releases, and create_stream's optional arguments have
    come and gone. Rather than pin to one version's shape, ask the signature
    what it takes and pass only that -- a wrong guess here is a hard 500 on
    every live view and playback request.
    """
    # These are Home Assistant's own option names (STREAM_OPTIONS_SCHEMA), not
    # raw ffmpeg flags -- it validates them and converts to PyAV options itself.
    # TCP because Hikvision over UDP drops packets under load;
    # use_wallclock_as_timestamps because these NVRs emit erratic DTS.
    kwargs: dict[str, Any] = {
        "options": {"rtsp_transport": "tcp", "use_wallclock_as_timestamps": True}
    }
    # Read parameter names off the code object, not inspect.signature: on
    # Python 3.14 signature() evaluates annotations (PEP 649), and Home
    # Assistant annotates dynamic_stream_settings under TYPE_CHECKING only, so
    # inspecting it raises NameError.
    code = create_stream.__code__
    params = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
    if "stream_label" in params:
        kwargs["stream_label"] = label
    if "dynamic_stream_settings" in params:
        # The class has moved across releases: camera.prefs on 2026.8, and
        # the stream package before that. It is a required argument, so if we
        # cannot find it, say so plainly rather than let create_stream raise a
        # bare TypeError.
        settings = None
        for module, attr in (
            ("homeassistant.components.camera", "DynamicStreamSettings"),
            ("homeassistant.components.camera.prefs", "DynamicStreamSettings"),
            ("homeassistant.components.stream.core", "DynamicStreamSettings"),
            ("homeassistant.components.stream", "DynamicStreamSettings"),
        ):
            try:
                settings_cls = getattr(
                    __import__(module, fromlist=[attr]), attr
                )
            except (ImportError, AttributeError):
                continue
            # Every field has a default; constructing bare survives renames.
            settings = settings_cls()
            break
        if settings is None:
            raise HikvisionError(
                "Cannot locate DynamicStreamSettings in this Home Assistant "
                "version; please report the Home Assistant version"
            )
        kwargs["dynamic_stream_settings"] = settings
    return kwargs


async def _async_get_stream(hass: HomeAssistant, key: str, source: str) -> Stream:
    """Return a running HLS stream for ``source``, reusing an existing one.

    Re-requesting the same playback range while it is already playing must not
    spin up a second ffmpeg -- that is what makes seeking in the card cheap.
    """
    streams: dict[str, tuple[Stream, Any]] = hass.data.setdefault(DATA_STREAMS, {})

    if (existing := streams.get(key)) is not None:
        stream, cancel_idle = existing
        cancel_idle()
    else:
        if len(streams) >= MAX_PLAYBACK_STREAMS:
            oldest = next(iter(streams))
            await _async_stop_stream(hass, oldest)
        stream = create_stream(hass, source, **_create_stream_kwargs(key))

    # Both paths, every time. Home Assistant reaps a provider that has had no
    # client for a while (remove_provider), which leaves a cached Stream whose
    # endpoint_url raises "not configured for format 'hls'". add_provider
    # returns the existing provider when there is one, and start() no-ops while
    # the worker thread is alive, so re-arming here is free and always correct.
    stream.add_provider(HLS_PROVIDER)
    await stream.start()
    streams[key] = (stream, _schedule_idle_stop(hass, key))
    return stream


@callback
def _schedule_idle_stop(hass: HomeAssistant, key: str):
    async def _stop(_now) -> None:
        await _async_stop_stream(hass, key)

    return async_call_later(hass, STREAM_IDLE.total_seconds(), _stop)


async def _async_stop_stream(hass: HomeAssistant, key: str) -> None:
    streams: dict[str, tuple[Stream, Any]] = hass.data.get(DATA_STREAMS, {})
    if (entry := streams.pop(key, None)) is None:
        return
    stream, cancel_idle = entry
    cancel_idle()
    await stream.stop()


# --------------------------------------------------------------- registration

_VIEWS = (
    DevicesView,
    ChannelsView,
    SnapshotView,
    RecordingsView,
    TimelineView,
    LiveView,
    PlaybackView,
    DownloadView,
    ClipView,
    SaveView,
    EventsView,
    PtzView,
)


@callback
def async_register_api(hass: HomeAssistant) -> None:
    """Register the REST views once per Home Assistant run."""
    if hass.data.get(f"{DOMAIN}_api_registered"):
        return
    hass.data[f"{DOMAIN}_api_registered"] = True
    for view in _VIEWS:
        hass.http.register_view(view)
