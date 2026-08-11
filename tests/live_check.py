"""Live self-check against a real NVR. No framework, no fixtures.

    python tests/live_check.py 192.168.1.10 admin PASSWORD

Fails loudly if any ISAPI surface the integration depends on breaks.
"""

from __future__ import annotations

import asyncio
import importlib.util  # noqa: E402
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp  # noqa: E402

# Load isapi.py directly: importing it as part of the package would pull in
# Home Assistant, and the whole point of this check is that it does not need it.
_SPEC = importlib.util.spec_from_file_location(
    "hikvision_isapi",
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hikvision_nvr"
    / "isapi.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE  # dataclasses resolves annotations through this
_SPEC.loader.exec_module(_MODULE)
HikvisionISAPI = _MODULE.HikvisionISAPI


async def main(host: str, user: str, password: str) -> None:
    async with aiohttp.ClientSession() as session:
        api = HikvisionISAPI(session, host, user, password)

        await api.async_connect()
        info = api.device_info
        assert info["model"], "no model reported"
        assert info["serial"], "no serial reported"
        print(f"device      : {info['name']} / {info['model']} / fw {info['firmware']}")

        assert api.channels, "no channels discovered"
        for channel in api.channels:
            print(
                f"channel {channel.id:>2}  : {channel.name!r:28} "
                f"online={channel.online} streams={channel.streams} ptz={channel.ptz}"
            )
        first = next((c for c in api.channels if c.online), api.channels[0])

        capabilities = api.event_capabilities
        assert capabilities, "device declared no event capabilities"
        assert any(
            "VMD" in events for events in capabilities.values()
        ), "no channel declares motion detection"
        for channel_id in sorted(capabilities):
            label = "NVR" if channel_id == 0 else f"channel {channel_id}"
            print(f"events {label:<11}: {', '.join(sorted(capabilities[channel_id]))}")
        # Channels must have picked up their own capabilities.
        assert any(c.events for c in api.channels), "capabilities not attached"
        assert all(c.id != 0 for c in api.channels), "channel 0 is reserved for the NVR"

        status = await api.async_get_system_status()
        assert "uptime_seconds" in status, f"no uptime reported: {status}"
        assert status["uptime_seconds"] > 0
        print(
            f"system      : up {status['uptime_seconds'] // 3600}h, "
            f"cpu {status.get('cpu_percent')}%, "
            f"mem {status.get('memory_used_mb')} MB"
        )

        jpeg = await api.async_snapshot(first.id)
        assert jpeg[:2] == b"\xff\xd8", "snapshot is not a JPEG"
        print(f"snapshot    : ch{first.id} {len(jpeg)} bytes JPEG OK")

        disks = await api.async_get_storage()
        assert disks, "no storage reported"
        for disk in disks:
            print(
                f"disk {disk['id']}      : {disk['status']} "
                f"{disk['used_percent']}% used of {disk['capacity_mb']} MB"
            )

        end = datetime.now(UTC)
        start = end - timedelta(days=1)
        recordings, total = await api.async_search_recordings(
            first.id, start, end, max_results=60
        )
        assert recordings, "no recordings found in the last 24h"
        assert total >= len(recordings)
        # Paging must actually advance, not repeat page one.
        assert len({r.start for r in recordings}) == len(recordings), "paging repeated"
        assert all(r.end > r.start for r in recordings), "bad segment bounds"
        print(
            f"playback    : {len(recordings)} of {total} segments, "
            f"first {recordings[0].start:%Y-%m-%d %H:%M} "
            f"({recordings[0].duration}s, {recordings[0].event_type})"
        )

        rtsp = api.playback_rtsp_url(
            first.id, recordings[0].start, recordings[0].end, credentials=False
        )
        assert "starttime=" in rtsp and "/tracks/" in rtsp
        print(f"rtsp back   : {rtsp}")
        print(f"rtsp live   : {api.rtsp_url(first.id, 1, credentials=False)}")

        # The whole download path, which every save and export now shares.
        # It was silently broken once: the endpoint only accepts a playbackURI
        # the device issued, and rejects anything constructed by hand.
        segments = await api.async_resolve_range(
            first.id, recordings[0].start, recordings[0].end
        )
        assert segments, "range resolved to no segments"
        received = 0
        header = b""
        async for chunk in api.async_download_segments(segments):
            header = header or chunk[:4]
            received += len(chunk)
            if received > 200_000:
                break
        assert received > 100_000, "download produced almost nothing"
        assert header == b"IMKH", f"unexpected container {header!r}"
        print(f"download    : {received} bytes of {header.decode()} streamed OK")

        events = []
        try:
            async with asyncio.timeout(12):
                async for event in api.async_listen_events():
                    events.append(event)
                    if len(events) >= 3:
                        break
        except TimeoutError:
            pass
        assert events, "alert stream produced no events in 12s"
        print(f"events      : {len(events)} received, e.g. {events[0]}")

        print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
