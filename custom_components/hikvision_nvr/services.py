"""Services: recording search, MP4 export, PTZ, reboot."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CHANNEL,
    ATTR_END,
    ATTR_START,
    DOMAIN,
    SERVICE_EXPORT_RECORDING,
    SERVICE_PTZ,
    SERVICE_REBOOT,
    SERVICE_SEARCH_RECORDINGS,
)
from .coordinator import HikvisionCoordinator
from .isapi import HikvisionError

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"
ATTR_FILENAME = "filename"
ATTR_EVENT_TYPE = "event_type"
ATTR_LIMIT = "limit"

_BASE = {
    vol.Optional(ATTR_DEVICE_ID): cv.string,
    vol.Required(ATTR_CHANNEL): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
}

SEARCH_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_EVENT_TYPE): cv.string,
        vol.Optional(ATTR_LIMIT, default=100): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1000)
        ),
    }
)

EXPORT_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_START): cv.datetime,
        vol.Required(ATTR_END): cv.datetime,
        vol.Optional(ATTR_FILENAME): cv.string,
    }
)

PTZ_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional("pan", default=0): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        vol.Optional("tilt", default=0): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        vol.Optional("zoom", default=0): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        vol.Optional("preset"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
        vol.Optional("duration", default=0.5): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=10)
        ),
    }
)

REBOOT_SCHEMA = vol.Schema({vol.Optional(ATTR_DEVICE_ID): cv.string})


def _resolve(hass: HomeAssistant, device_id: str | None) -> HikvisionCoordinator:
    """Find the target NVR. With one NVR configured, device_id is optional."""
    coordinators = [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(getattr(entry, "runtime_data", None), HikvisionCoordinator)
    ]
    if not coordinators:
        raise ServiceValidationError("No Hikvision NVR is configured")
    if device_id is None:
        if len(coordinators) > 1:
            raise ServiceValidationError(
                "Several NVRs are configured; pass device_id"
            )
        return coordinators[0]
    for coordinator in coordinators:
        if device_id in (
            coordinator.device_id,
            coordinator.api.host,
            coordinator.config_entry.entry_id,
        ):
            return coordinator
    raise ServiceValidationError(f"Unknown NVR '{device_id}'")


def _range(call: ServiceCall) -> tuple:
    end = call.data.get(ATTR_END) or dt_util.utcnow()
    start = call.data.get(ATTR_START) or end - timedelta(days=1)
    start, end = dt_util.as_utc(start), dt_util.as_utc(end)
    if end <= start:
        raise ServiceValidationError("end_time must be after start_time")
    return start, end


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register services once. Safe to call repeatedly."""
    if hass.services.has_service(DOMAIN, SERVICE_SEARCH_RECORDINGS):
        return

    async def search(call: ServiceCall) -> ServiceResponse:
        coordinator = _resolve(hass, call.data.get(ATTR_DEVICE_ID))
        start, end = _range(call)
        try:
            recordings, total = await coordinator.api.async_search_recordings(
                call.data[ATTR_CHANNEL],
                start,
                end,
                max_results=call.data[ATTR_LIMIT],
                event_type=call.data.get(ATTR_EVENT_TYPE),
            )
        except HikvisionError as err:
            raise HomeAssistantError(f"Recording search failed: {err}") from err
        return {
            "total": total,
            "recordings": [recording.as_dict() for recording in recordings],
        }

    async def export(call: ServiceCall) -> ServiceResponse:
        coordinator = _resolve(hass, call.data.get(ATTR_DEVICE_ID))
        channel = call.data[ATTR_CHANNEL]
        start, end = _range(call)
        if end - start > timedelta(hours=2):
            raise ServiceValidationError("Export range must not exceed 2 hours")

        default = f"hikvision_ch{channel}_{start:%Y%m%d_%H%M%S}.mp4"
        filename = call.data.get(ATTR_FILENAME) or default
        # Never let a service call write outside the media directory.
        if re.search(r"[\\/]|\.\.", filename):
            raise ServiceValidationError("filename must not contain a path")
        media_dir = Path(hass.config.media_dirs.get("local", hass.config.path("media")))
        target = media_dir / DOMAIN / filename
        if not hass.config.is_allowed_path(str(target.parent.parent)):
            raise ServiceValidationError(f"{media_dir} is not an allowed path")

        source = coordinator.api.playback_rtsp_url(channel, start, end)
        await hass.async_add_executor_job(
            lambda: target.parent.mkdir(parents=True, exist_ok=True)
        )

        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        binary = get_ffmpeg_manager(hass).binary
        process = await asyncio.create_subprocess_exec(
            binary,
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", source,
            # G.722.1 has no valid MP4 mapping and PyAV/ffmpeg choke on it;
            # video only keeps the export universally playable.
            "-an",
            "-c:v", "copy",
            "-movflags", "+faststart",
            "-y", str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # An export can only ever be as long as the clip; +60s covers handshake.
        timeout = (end - start).total_seconds() + 60
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            raise HomeAssistantError("Export timed out") from None
        if process.returncode != 0:
            raise HomeAssistantError(
                f"ffmpeg failed: {stderr.decode(errors='replace')[:400]}"
            )

        size = await hass.async_add_executor_job(lambda: target.stat().st_size)
        return {
            "path": str(target),
            "media_content_id": f"media-source://media_source/local/{DOMAIN}/{filename}",
            "size": size,
        }

    async def ptz(call: ServiceCall) -> None:
        coordinator = _resolve(hass, call.data.get(ATTR_DEVICE_ID))
        channel = call.data[ATTR_CHANNEL]
        try:
            if (preset := call.data.get("preset")) is not None:
                await coordinator.api.async_ptz_preset(channel, preset)
                return
            await coordinator.api.async_ptz(
                channel,
                pan=call.data["pan"],
                tilt=call.data["tilt"],
                zoom=call.data["zoom"],
            )
            if duration := call.data["duration"]:
                # Continuous move runs until told to stop; stop it ourselves so
                # a single service call is a nudge, not a runaway pan.
                await asyncio.sleep(duration)
                await coordinator.api.async_ptz(channel, 0, 0, 0)
        except HikvisionError as err:
            raise HomeAssistantError(f"PTZ failed: {err}") from err

    async def reboot(call: ServiceCall) -> None:
        coordinator = _resolve(hass, call.data.get(ATTR_DEVICE_ID))
        try:
            await coordinator.api.async_reboot()
        except HikvisionError as err:
            raise HomeAssistantError(f"Reboot failed: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_RECORDINGS,
        search,
        schema=SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_RECORDING,
        export,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_PTZ, ptz, schema=PTZ_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REBOOT, reboot, schema=REBOOT_SCHEMA
    )
