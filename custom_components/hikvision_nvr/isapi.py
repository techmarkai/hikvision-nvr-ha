"""Async ISAPI client for Hikvision NVRs / DVRs.

Only depends on aiohttp (already in Home Assistant) and the stdlib XML parser.
Handles both Basic and Digest authentication -- Hikvision firmwares differ in
which one they accept, and the web-auth setting can change it at runtime, so we
negotiate instead of assuming.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import aiohttp
from defusedxml import ElementTree as DET
from yarl import URL

_LOGGER = logging.getLogger(__name__)

# Hikvision namespaces vary by endpoint (isapi.org, hikvision.com, psia). We
# never want to care, so strip them at parse time.
_NS_RE = re.compile(r"\{[^}]*\}")
_TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Some firmwares name an event differently in /ISAPI/Event/triggers than in the
# alert stream. Declared name -> name as it arrives on the wire.
EVENT_ALIASES: dict[str, str] = {
    "tamper": "tamperdetection",
    "shelteralarm": "tamperdetection",
}

# Events that belong to the NVR itself rather than a camera. The alert stream
# leaves channelID empty for these, and we file them against channel 0.
DEVICE_EVENTS = frozenset(
    {
        "diskfull",
        "diskerror",
        "nicbroken",
        "ipconflict",
        "illaccess",
        "recordingfailure",
        "badvideo",
    }
)


class HikvisionError(Exception):
    """Base error."""


class AuthError(HikvisionError):
    """Invalid credentials, or the account is locked out."""


class ConnectionFailed(HikvisionError):
    """The device could not be reached."""


class UnsupportedOperation(HikvisionError):
    """The device answered, but does not implement this endpoint."""


@dataclass(slots=True)
class Channel:
    """One camera channel behind the NVR."""

    id: int
    name: str
    online: bool = True
    ip_address: str | None = None
    streams: list[int] = field(default_factory=lambda: [1, 2])
    ptz: bool = False
    # Event types this channel declares support for, as reported by the device.
    events: set[str] = field(default_factory=set)
    # Most channels have no microphone; only offer audio where there is some.
    has_audio: bool = False
    audio_codec: str | None = None

    @property
    def track_id(self) -> int:
        """Recording track id used by the search/playback APIs."""
        return self.id * 100 + 1

    def stream_id(self, stream: int) -> int:
        """Streaming channel id, e.g. channel 3 sub-stream -> 302."""
        return self.id * 100 + stream


@dataclass(slots=True)
class Recording:
    """One recorded segment."""

    channel: int
    start: datetime
    end: datetime
    playback_uri: str
    event_type: str | None = None
    size: int | None = None

    @property
    def duration(self) -> int:
        return int((self.end - self.start).total_seconds())

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration": self.duration,
            "event_type": self.event_type,
            "size": self.size,
        }


@dataclass(slots=True)
class Event:
    """A pushed event from the alert stream."""

    type: str
    state: str
    channel: int | None
    description: str
    timestamp: datetime

    @property
    def active(self) -> bool:
        return self.state == "active"


def _strip_ns(element: ET.Element) -> ET.Element:
    for node in element.iter():
        node.tag = _NS_RE.sub("", node.tag)
    return element


def _text(element: ET.Element | None, path: str, default: str = "") -> str:
    if element is None:
        return default
    found = element.find(path)
    return default if found is None or found.text is None else found.text.strip()


def _parse_time(value: str) -> datetime:
    """Parse the several timestamp shapes Hikvision emits."""
    value = value.strip()
    if not value:
        raise ValueError("empty timestamp")
    # Compact form used inside playback URIs: 20260810T000024Z
    if "-" not in value and len(value) >= 16:
        return datetime.strptime(value[:16], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    # ISO with or without offset; Python needs +HH:MM, Hikvision sometimes emits Z.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_isapi_time(value: datetime) -> str:
    """Render a datetime the way the search API expects (always UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(_TIME_FMT)


class _Digest:
    """Minimal RFC 2617 digest, MD5 / qop=auth. ~30 lines beats a dependency."""

    def __init__(self, username: str, password: str) -> None:
        self._user = username
        self._password = password
        self._params: dict[str, str] = {}
        self._nc = 0

    def parse_challenge(self, header: str) -> bool:
        if not header or not header.lower().startswith("digest"):
            return False
        self._params = dict(re.findall(r'(\w+)="?([^",]+)"?', header[6:]))
        self._nc = 0
        return "nonce" in self._params and "realm" in self._params

    def header(self, method: str, path: str) -> str:
        realm = self._params["realm"]
        nonce = self._params["nonce"]
        qop = self._params.get("qop")
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
        ha1 = md5(f"{self._user}:{realm}:{self._password}")
        ha2 = md5(f"{method}:{path}")
        parts = [
            f'username="{self._user}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{path}"',
        ]
        if qop:
            self._nc += 1
            nc = f"{self._nc:08x}"
            cnonce = os.urandom(8).hex()
            response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
            parts += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
        else:
            response = md5(f"{ha1}:{nonce}:{ha2}")
        parts.append(f'response="{response}"')
        if opaque := self._params.get("opaque"):
            parts.append(f'opaque="{opaque}"')
        return "Digest " + ", ".join(parts)


class HikvisionISAPI:
    """Talks ISAPI to one NVR."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        rtsp_port: int = 554,
        use_ssl: bool = False,
        verify_ssl: bool = True,
        timeout: int = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.rtsp_port = rtsp_port
        self.use_ssl = use_ssl
        self._session = session
        self._user = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=8)
        self._digest = _Digest(username, password)
        self._auth_mode: str | None = None
        self._lock = asyncio.Lock()

        scheme = "https" if use_ssl else "http"
        self.base_url = f"{scheme}://{host}:{port}"

        self.device_info: dict[str, str] = {}
        self.channels: list[Channel] = []
        # channel id (0 = the NVR itself) -> event types it declares.
        self.event_capabilities: dict[int, set[str]] = {}

    # ------------------------------------------------------------------ HTTP

    @property
    def _ssl(self) -> bool | None:
        return None if not self.use_ssl else self._verify_ssl

    def _basic_header(self) -> str:
        import base64

        token = base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
        return f"Basic {token}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: str | None = None,
        stream: bool = False,
    ) -> aiohttp.ClientResponse:
        """Perform an authenticated ISAPI request.

        The caller owns the response when ``stream`` is True and must release it.
        """
        url = URL(self.base_url + path, encoded=True)
        headers = {"Content-Type": "application/xml"} if data else {}

        # Path used in the digest hash must include the query string.
        digest_path = url.raw_path_qs

        async def _send(auth_header: str | None) -> aiohttp.ClientResponse:
            hdrs = dict(headers)
            if auth_header:
                hdrs["Authorization"] = auth_header
            return await self._session.request(
                method,
                url,
                data=data,
                headers=hdrs,
                timeout=self._timeout,
                ssl=self._ssl,
            )

        try:
            async with self._lock:
                mode = self._auth_mode
            if mode == "digest":
                response = await _send(self._digest.header(method, digest_path))
            else:
                response = await _send(self._basic_header())

            if response.status == 401:
                challenge = response.headers.get("WWW-Authenticate", "")
                response.release()
                if not self._digest.parse_challenge(challenge):
                    raise AuthError("Invalid username or password")
                response = await _send(self._digest.header(method, digest_path))
                if response.status == 401:
                    response.release()
                    raise AuthError("Invalid username or password")
                async with self._lock:
                    self._auth_mode = "digest"
            elif mode is None and response.status < 400:
                async with self._lock:
                    self._auth_mode = "basic"
        except aiohttp.ClientError as err:
            raise ConnectionFailed(f"Cannot reach {self.host}: {err}") from err
        except asyncio.TimeoutError as err:
            raise ConnectionFailed(f"Timeout talking to {self.host}") from err

        if response.status == 403:
            body = await response.text()
            response.release()
            if "notSupport" in body or "Invalid Operation" in body:
                raise UnsupportedOperation(path)
            raise AuthError(f"Forbidden: {path}")
        if response.status >= 400:
            response.release()
            raise HikvisionError(f"{method} {path} returned HTTP {response.status}")

        if not stream:
            await response.read()
        return response

    async def get_xml(self, path: str) -> ET.Element:
        response = await self.request("GET", path)
        return _strip_ns(DET.fromstring(await response.text()))

    async def post_xml(self, path: str, body: str) -> ET.Element:
        response = await self.request("POST", path, data=body)
        return _strip_ns(DET.fromstring(await response.text()))

    async def get_bytes(self, path: str) -> bytes:
        response = await self.request("GET", path)
        return await response.read()

    # ------------------------------------------------------------ discovery

    async def async_connect(self) -> None:
        """Load device info, channels and capabilities. Call once on setup."""
        await self.async_update_device_info()
        await self.async_update_channels()
        await self.async_update_capabilities()

    async def async_update_device_info(self) -> dict[str, str]:
        root = await self.get_xml("/ISAPI/System/deviceInfo")
        self.device_info = {
            "name": _text(root, "deviceName", "Hikvision NVR"),
            "model": _text(root, "model"),
            "serial": _text(root, "serialNumber"),
            "mac": _text(root, "macAddress").lower(),
            "firmware": _text(root, "firmwareVersion"),
            "firmware_released": _text(root, "firmwareReleasedDate"),
            "device_type": _text(root, "deviceType"),
        }
        return self.device_info

    async def async_update_channels(self) -> list[Channel]:
        """Discover channels.

        NVRs expose them via InputProxy; standalone cameras/DVRs do not, so we
        fall back to whatever the streaming channel list reports.
        """
        channels: dict[int, Channel] = {}

        try:
            root = await self.get_xml("/ISAPI/ContentMgmt/InputProxy/channels")
            for node in root.findall("InputProxyChannel"):
                cid = int(_text(node, "id", "0"))
                if cid:
                    channels[cid] = Channel(
                        id=cid,
                        name=_text(node, "name") or f"Camera {cid}",
                        ip_address=_text(node, "sourceInputPortDescriptor/ipAddress")
                        or None,
                    )
        except (HikvisionError, DET.ParseError):
            _LOGGER.debug("No InputProxy channel list; using streaming channels")

        # Streaming channel list tells us which sub-streams actually exist, and
        # is the only source on devices without InputProxy.
        try:
            root = await self.get_xml("/ISAPI/Streaming/channels")
            streams: dict[int, list[int]] = {}
            for node in root.findall("StreamingChannel"):
                sid = int(_text(node, "id", "0"))
                if not sid:
                    continue
                cid, stream = divmod(sid, 100)
                if _text(node, "enabled", "true") != "true":
                    continue
                streams.setdefault(cid, []).append(stream)
                channels.setdefault(
                    cid, Channel(id=cid, name=_text(node, "channelName") or f"Camera {cid}")
                )
                if _text(node, "Audio/enabled") == "true":
                    channels[cid].has_audio = True
                    channels[cid].audio_codec = (
                        _text(node, "Audio/audioCompressionType") or None
                    )
            for cid, available in streams.items():
                channels[cid].streams = sorted(available)
        except (HikvisionError, DET.ParseError) as err:
            if not channels:
                raise HikvisionError("Could not enumerate any channel") from err

        await self._async_apply_channel_status(channels)
        await self._async_apply_ptz(channels)

        self.channels = [channels[cid] for cid in sorted(channels)]
        return self.channels

    async def _async_apply_channel_status(self, channels: dict[int, Channel]) -> None:
        try:
            root = await self.get_xml("/ISAPI/ContentMgmt/InputProxy/channels/status")
        except HikvisionError:
            return
        for node in root.findall("InputProxyChannelStatus"):
            cid = int(_text(node, "id", "0"))
            if cid in channels:
                channels[cid].online = _text(node, "online", "true") == "true"

    async def _async_apply_ptz(self, channels: dict[int, Channel]) -> None:
        for channel in channels.values():
            try:
                await self.get_xml(f"/ISAPI/PTZCtrl/channels/{channel.id}/capabilities")
                channel.ptz = True
            except HikvisionError:
                channel.ptz = False

    async def async_update_capabilities(self) -> dict[int, set[str]]:
        """Ask the device which events it supports, and on which channels.

        /ISAPI/Event/triggers is the device's own declaration -- ids look like
        ``VMD-3`` or ``tamper-1`` for a channel, and plain ``diskfull`` for the
        NVR. Channel 0 collects the device-wide ones. Far better than probing
        each event type, and it means we only create entities that can fire.
        """
        capabilities: dict[int, set[str]] = {}
        try:
            root = await self.get_xml("/ISAPI/Event/triggers")
        except (HikvisionError, DET.ParseError):
            _LOGGER.debug("%s does not expose /Event/triggers", self.host)
            self.event_capabilities = capabilities
            return capabilities

        for node in root.findall("EventTrigger"):
            event_type = _text(node, "eventType")
            if not event_type:
                continue
            event_type = EVENT_ALIASES.get(event_type, event_type)
            trigger_id = _text(node, "id")
            channel_raw = _text(node, "videoInputChannelID") or _text(
                node, "dynVideoInputChannelID"
            )
            if not channel_raw:
                # Fall back to the numeric suffix of the id ("VMD-3" -> 3).
                match = re.search(r"-(\d+)$", trigger_id)
                channel_raw = match.group(1) if match else ""
            channel = (
                int(channel_raw)
                if channel_raw.isdigit() and event_type not in DEVICE_EVENTS
                else 0
            )
            capabilities.setdefault(channel, set()).add(event_type)

        self.event_capabilities = capabilities
        by_id = {channel.id: channel for channel in self.channels}
        for channel_id, events in capabilities.items():
            if channel := by_id.get(channel_id):
                channel.events = events
        return capabilities

    async def async_get_system_status(self) -> dict[str, Any]:
        """Uptime and resource use. Absent on some firmwares, so never fatal."""
        try:
            root = await self.get_xml("/ISAPI/System/status")
        except (HikvisionError, DET.ParseError):
            return {}

        def number(path: str) -> float | None:
            raw = _text(root, path)
            try:
                return float(raw)
            except ValueError:
                return None

        status: dict[str, Any] = {}
        if (uptime := number("deviceUpTime")) is not None:
            status["uptime_seconds"] = int(uptime)
        if (cpu := number("CPUList/CPU/cpuUtilization")) is not None:
            status["cpu_percent"] = cpu
        if (memory := number("MemoryList/Memory/memoryUsage")) is not None:
            status["memory_used_mb"] = round(memory, 1)
        if (available := number("MemoryList/Memory/memoryAvailable")) is not None:
            status["memory_available_mb"] = round(available, 1)
        return status

    async def async_refresh_status(self) -> dict[str, Any]:
        """Cheap periodic poll: channel state, storage health, system status."""
        by_id = {channel.id: channel for channel in self.channels}
        await self._async_apply_channel_status(by_id)
        return {
            "channels": {c.id: c.online for c in self.channels},
            "storage": await self.async_get_storage(),
            "system": await self.async_get_system_status(),
        }

    async def async_get_storage(self) -> list[dict[str, Any]]:
        try:
            root = await self.get_xml("/ISAPI/ContentMgmt/Storage")
        except HikvisionError:
            return []
        disks = []
        for node in root.findall("hddList/hdd"):
            capacity = int(_text(node, "capacity", "0") or 0)
            free = int(_text(node, "freeSpace", "0") or 0)
            disks.append(
                {
                    "id": int(_text(node, "id", "0") or 0),
                    "name": _text(node, "hddName"),
                    "status": _text(node, "status"),
                    "capacity_mb": capacity,
                    "free_mb": free,
                    "used_percent": round((capacity - free) / capacity * 100, 1)
                    if capacity
                    else None,
                    "property": _text(node, "property"),
                }
            )
        return disks

    # ---------------------------------------------------------------- media

    def rtsp_url(self, channel: int, stream: int = 1, credentials: bool = True) -> str:
        """Live RTSP URL for a channel."""
        auth = ""
        if credentials:
            from urllib.parse import quote

            auth = f"{quote(self._user, safe='')}:{quote(self._password, safe='')}@"
        return (
            f"rtsp://{auth}{self.host}:{self.rtsp_port}"
            f"/Streaming/Channels/{channel * 100 + stream}"
        )

    def playback_rtsp_url(
        self, channel: int, start: datetime, end: datetime, credentials: bool = True
    ) -> str:
        """RTSP URL that replays a time range from the NVR's disks."""
        auth = ""
        if credentials:
            from urllib.parse import quote

            auth = f"{quote(self._user, safe='')}:{quote(self._password, safe='')}@"
        fmt = "%Y%m%dT%H%M%SZ"
        start_s = start.astimezone(timezone.utc).strftime(fmt)
        end_s = end.astimezone(timezone.utc).strftime(fmt)
        return (
            f"rtsp://{auth}{self.host}:{self.rtsp_port}"
            f"/Streaming/tracks/{channel * 100 + 1}"
            f"/?starttime={start_s}&endtime={end_s}"
        )

    async def async_snapshot(self, channel: int, stream: int = 2) -> bytes:
        """JPEG still. Sub-stream by default -- an order of magnitude faster."""
        try:
            return await self.get_bytes(
                f"/ISAPI/Streaming/channels/{channel * 100 + stream}/picture"
            )
        except HikvisionError:
            if stream != 1:
                return await self.get_bytes(
                    f"/ISAPI/Streaming/channels/{channel * 100 + 1}/picture"
                )
            raise

    # ------------------------------------------------------------- playback

    async def async_search_recordings(
        self,
        channel: int,
        start: datetime,
        end: datetime,
        *,
        max_results: int = 100,
        offset: int = 0,
        event_type: str | None = None,
    ) -> tuple[list[Recording], int]:
        """Search recorded segments. Returns (segments, total_matches).

        The device caps a single response at ~40 items regardless of
        maxResults, so we page until we have what the caller asked for.
        """
        found: list[Recording] = []
        total = 0
        position = offset
        # Deterministic search id derived from the query -- the NVR keys its
        # result cursor off it, so reusing one per query is correct and avoids
        # leaking search handles.
        search_id = hashlib.md5(
            f"{channel}{start}{end}{event_type}".encode()
        ).hexdigest()
        search_id = "-".join(
            [search_id[:8], search_id[8:12], search_id[12:16], search_id[16:20], search_id[20:32]]
        )

        while len(found) < max_results:
            body = (
                '<?xml version="1.0" encoding="utf-8"?>'
                "<CMSearchDescription>"
                f"<searchID>{search_id}</searchID>"
                f"<trackIDList><trackID>{channel * 100 + 1}</trackID></trackIDList>"
                "<timeSpanList><timeSpan>"
                f"<startTime>{to_isapi_time(start)}</startTime>"
                f"<endTime>{to_isapi_time(end)}</endTime>"
                "</timeSpan></timeSpanList>"
                f"<maxResults>{min(40, max_results - len(found))}</maxResults>"
                f"<searchResultPostion>{position}</searchResultPostion>"
                "<metadataList><metadataDescriptor>"
                "//recordType.meta.std-cgi.com"
                "</metadataDescriptor></metadataList>"
                "</CMSearchDescription>"
            )
            root = await self.post_xml("/ISAPI/ContentMgmt/search", body)
            total = int(_text(root, "numOfMatches", "0") or 0)
            items = root.findall("matchList/searchMatchItem")
            if not items:
                break

            for item in items:
                try:
                    recording = Recording(
                        channel=channel,
                        start=_parse_time(_text(item, "timeSpan/startTime")),
                        end=_parse_time(_text(item, "timeSpan/endTime")),
                        playback_uri=_text(
                            item, "mediaSegmentDescriptor/playbackURI"
                        ),
                        event_type=(
                            _text(item, "metadataMatches/metadataDescriptor")
                            .rsplit("/", 1)[-1]
                            or None
                        ),
                    )
                except ValueError:
                    continue
                if size := re.search(r"size=(\d+)", recording.playback_uri):
                    recording.size = int(size.group(1))
                if event_type and recording.event_type != event_type:
                    continue
                found.append(recording)

            position += len(items)
            if _text(root, "responseStatusStrg", "OK") != "MORE":
                break

        return found[:max_results], total

    async def async_download_stream(
        self, recording: Recording | str, chunk_size: int = 65536
    ) -> AsyncIterator[bytes]:
        """Stream a recorded segment out of the NVR as raw bytes."""
        uri = recording if isinstance(recording, str) else recording.playback_uri
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<downloadRequest version="1.0" '
            'xmlns="http://www.hikvision.com/ver20/XMLSchema">'
            f"<playbackURI>{uri.replace('&', '&amp;')}</playbackURI>"
            "</downloadRequest>"
        )
        response = await self.request(
            "POST", "/ISAPI/ContentMgmt/download", data=body, stream=True
        )
        try:
            async for chunk in response.content.iter_chunked(chunk_size):
                yield chunk
        finally:
            response.release()

    # --------------------------------------------------------------- events

    async def async_listen_events(self) -> AsyncIterator[Event]:
        """Yield events from the device's multipart alert stream.

        Runs until cancelled or the connection drops; the caller is expected to
        reconnect.
        """
        response = await self.request(
            "GET", "/ISAPI/Event/notification/alertStream", stream=True
        )
        buffer = bytearray()
        try:
            async for chunk in response.content.iter_chunked(4096):
                buffer += chunk
                # Each part ends with the closing tag; parse greedily.
                while b"</EventNotificationAlert>" in buffer:
                    body, _, buffer_rest = buffer.partition(
                        b"</EventNotificationAlert>"
                    )
                    buffer = bytearray(buffer_rest)
                    start = body.find(b"<EventNotificationAlert")
                    if start < 0:
                        continue
                    payload = body[start:] + b"</EventNotificationAlert>"
                    if (event := self._parse_event(payload)) is not None:
                        yield event
                if len(buffer) > 65536:  # runaway guard against a garbage stream
                    buffer = bytearray()
        finally:
            response.release()

    def _parse_event(self, payload: bytes) -> Event | None:
        try:
            root = _strip_ns(DET.fromstring(payload))
        except DET.ParseError:
            return None
        channel_raw = _text(root, "channelID") or _text(root, "dynChannelID")
        try:
            timestamp = _parse_time(_text(root, "dateTime"))
        except ValueError:
            timestamp = datetime.now(timezone.utc)
        return Event(
            type=_text(root, "eventType", "unknown"),
            state=_text(root, "eventState", "active"),
            channel=int(channel_raw) if channel_raw.isdigit() else None,
            description=_text(root, "eventDescription"),
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------ PTZ

    async def async_ptz(
        self, channel: int, pan: int = 0, tilt: int = 0, zoom: int = 0
    ) -> None:
        """Continuous PTZ move. Values are -100..100; all zero stops."""
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<PTZData><pan>%d</pan><tilt>%d</tilt><zoom>%d</zoom></PTZData>'
            % (pan, tilt, zoom)
        )
        await self.request(
            "PUT", f"/ISAPI/PTZCtrl/channels/{channel}/continuous", data=body
        )

    async def async_ptz_preset(self, channel: int, preset: int) -> None:
        await self.request(
            "PUT", f"/ISAPI/PTZCtrl/channels/{channel}/presets/{preset}/goto"
        )

    async def async_missing_notifications(self) -> list[str]:
        """Detection triggers that record but never announce.

        A Hikvision trigger linked only to "record" fills the disk while Home
        Assistant hears nothing, which looks exactly like a broken integration.
        Worth telling the user about rather than leaving them to wonder.
        """
        missing: list[str] = []
        try:
            root = await self.get_xml("/ISAPI/Event/triggers")
        except (HikvisionError, DET.ParseError):
            return missing
        for node in root.findall("EventTrigger"):
            event_type = EVENT_ALIASES.get(
                _text(node, "eventType"), _text(node, "eventType")
            )
            if event_type not in ("VMD", "linedetection", "fielddetection"):
                continue
            methods = [
                _text(n, "notificationMethod")
                for n in node.findall(
                    "EventTriggerNotificationList/EventTriggerNotification"
                )
            ]
            if "center" in methods:
                continue
            trigger_id = _text(node, "id")
            channel = trigger_id.rpartition("-")[2]
            path = {
                "VMD": f"/ISAPI/System/Video/inputs/channels/{channel}/motionDetection",
                "linedetection": f"/ISAPI/Smart/LineDetection/{channel}",
                "fielddetection": f"/ISAPI/Smart/FieldDetection/{channel}",
            }[event_type]
            # A trigger exists whether or not the detection behind it is
            # switched on. Warning about a detection nobody enabled would be
            # noise, so only report the ones that are actually running.
            try:
                if _text(await self.get_xml(path), "enabled") == "true":
                    missing.append(trigger_id)
            except (HikvisionError, DET.ParseError):
                continue
        return missing

    async def async_enable_notifications(
        self, event_types: set[str] | None = None, channels: set[int] | None = None
    ) -> list[str]:
        """Make the device announce its events, not just record them.

        A Hikvision trigger can be linked to several actions. "record" starts a
        recording; "center" (Notify Surveillance Center) is the one that pushes
        the event to the alert stream we listen on. A channel set to record only
        will fill the disk with motion clips while Home Assistant never sees a
        thing -- which is the default on many installs.

        Existing notifications are preserved; this only ever adds "center".
        Returns the triggers that were changed.
        """
        changed: list[str] = []
        root = await self.get_xml("/ISAPI/Event/triggers")
        for node in root.findall("EventTrigger"):
            trigger_id = _text(node, "id")
            event_type = EVENT_ALIASES.get(
                _text(node, "eventType"), _text(node, "eventType")
            )
            if event_types and event_type not in event_types:
                continue
            suffix = re.search(r"-(\d+)$", trigger_id)
            channel = int(suffix.group(1)) if suffix else 0
            if channels and channel not in channels:
                continue

            methods = [
                _text(n, "notificationMethod")
                for n in node.findall(
                    "EventTriggerNotificationList/EventTriggerNotification"
                )
            ]
            if "center" in methods:
                continue

            existing = "".join(
                f"<EventTriggerNotification><id>{_text(n, 'id')}</id>"
                f"<notificationMethod>{_text(n, 'notificationMethod')}</notificationMethod>"
                "</EventTriggerNotification>"
                for n in node.findall(
                    "EventTriggerNotificationList/EventTriggerNotification"
                )
            )
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<EventTriggerNotificationList version="2.0" '
                'xmlns="http://www.isapi.org/ver20/XMLSchema">'
                f"{existing}"
                f"<EventTriggerNotification><id>center-{channel or 1}</id>"
                "<notificationMethod>center</notificationMethod>"
                "<notificationRecurrence>beginningandend</notificationRecurrence>"
                "</EventTriggerNotification>"
                "</EventTriggerNotificationList>"
            )
            try:
                await self.request(
                    "PUT",
                    f"/ISAPI/Event/triggers/{trigger_id}/notifications",
                    data=body,
                )
            except HikvisionError as err:
                _LOGGER.warning("Could not enable notifications on %s: %s", trigger_id, err)
                continue
            changed.append(trigger_id)

        if changed:
            _LOGGER.info("Enabled event notifications on: %s", ", ".join(changed))
        return changed

    async def async_reboot(self) -> None:
        await self.request("PUT", "/ISAPI/System/reboot")
