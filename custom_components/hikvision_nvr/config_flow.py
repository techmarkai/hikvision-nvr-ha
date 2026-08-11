"""Config and options flow for Hikvision NVR."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CHANNELS,
    CONF_EVENTS,
    CONF_RTSP_PORT,
    CONF_SNAPSHOT_STREAM,
    CONF_STREAM,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_ENABLED_EVENTS,
    DEFAULT_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_SSL_PORT,
    DOMAIN,
    EVENT_CLASSES,
    STREAM_MAIN,
    STREAM_SUB,
)
from .isapi import AuthError, ConnectionFailed, HikvisionError, HikvisionISAPI

_LOGGER = logging.getLogger(__name__)

# Readable names for the options screen. Mirrors the entity translations, which
# the flow cannot reach.
EVENT_LABELS = {
    "VMD": "Motion",
    "linedetection": "Line crossing",
    "fielddetection": "Intrusion",
    "tamperdetection": "Tampering",
    "videoloss": "Video loss",
    "facedetection": "Face detected",
    "faceSnap": "Face captured",
    "scenechangedetection": "Scene change",
    "IO": "Alarm input",
    "softIO": "Software alarm input",
    "PIR": "PIR",
    "regionEntrance": "Region entrance",
    "regionExiting": "Region exit",
    "diskfull": "Disk full",
    "diskerror": "Disk error",
    "nicbroken": "Network down",
    "ipconflict": "IP conflict",
    "illaccess": "Illegal login",
    "recordingfailure": "Recording failure",
    "badvideo": "Video quality problem",
}


async def _async_probe(hass, data: Mapping[str, Any]) -> HikvisionISAPI:
    """Connect and discover, raising the flow-facing errors."""
    api = HikvisionISAPI(
        async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True)),
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        port=data[CONF_PORT],
        rtsp_port=data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
        use_ssl=data.get(CONF_USE_SSL, False),
        verify_ssl=data.get(CONF_VERIFY_SSL, True),
    )
    await api.async_connect()
    return api


class HikvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an NVR by host and credentials."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # Default the HTTP port to match the chosen scheme when untouched.
            if user_input.get(CONF_USE_SSL) and user_input[CONF_PORT] == DEFAULT_PORT:
                user_input[CONF_PORT] = DEFAULT_SSL_PORT
            try:
                api = await _async_probe(self.hass, user_input)
            except AuthError:
                errors["base"] = "invalid_auth"
            except ConnectionFailed:
                errors["base"] = "cannot_connect"
            except HikvisionError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error setting up Hikvision NVR")
                errors["base"] = "unknown"
            else:
                unique_id = api.device_info.get("serial") or api.device_info.get("mac")
                await self.async_set_unique_id(unique_id)
                if self._reauth_entry:
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                    return self.async_update_reload_and_abort(
                        self._reauth_entry, data_updates=user_input
                    )
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=api.device_info.get("name") or api.host,
                    data=user_input,
                    options={
                        CONF_STREAM: STREAM_MAIN,
                        CONF_SNAPSHOT_STREAM: STREAM_SUB,
                        CONF_CHANNELS: [str(c.id) for c in api.channels],
                    },
                )

        suggested = dict(user_input or {})
        if self._reauth_entry and not suggested:
            suggested = dict(self._reauth_entry.data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=suggested.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_USERNAME, default=suggested.get(CONF_USERNAME, "admin")
                ): str,
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_PORT, default=suggested.get(CONF_PORT, DEFAULT_PORT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(
                    CONF_RTSP_PORT,
                    default=suggested.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(
                    CONF_USE_SSL, default=suggested.get(CONF_USE_SSL, False)
                ): BooleanSelector(),
                vol.Required(
                    CONF_VERIFY_SSL, default=suggested.get(CONF_VERIFY_SSL, False)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._reauth_entry = self._get_reconfigure_entry()
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> HikvisionOptionsFlow:
        return HikvisionOptionsFlow()


class HikvisionOptionsFlow(OptionsFlow):
    """Pick which channels and events to expose, and which stream to use."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        coordinator = getattr(self.config_entry, "runtime_data", None)
        channels = coordinator.api.channels if coordinator else []
        options = [
            SelectOptionDict(value=str(c.id), label=f"{c.id}: {c.name}")
            for c in channels
        ]
        current = self.config_entry.options

        # Only offer what this device actually declares; a capable NVR supports
        # a lot of event types and most people want a handful of them.
        declared: set[str] = set()
        if coordinator:
            for events in coordinator.api.event_capabilities.values():
                declared |= events
        declared &= EVENT_CLASSES.keys()
        event_options = [
            SelectOptionDict(value=event, label=EVENT_LABELS.get(event, event))
            for event in sorted(declared, key=lambda e: EVENT_LABELS.get(e, e))
        ]
        default_events = current.get(
            CONF_EVENTS, sorted(declared & DEFAULT_ENABLED_EVENTS)
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CHANNELS,
                    default=current.get(
                        CONF_CHANNELS, [o["value"] for o in options]
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                ),
                vol.Optional(CONF_EVENTS, default=default_events): SelectSelector(
                    SelectSelectorConfig(
                        options=event_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_STREAM, default=current.get(CONF_STREAM, STREAM_MAIN)
                ): vol.In({STREAM_MAIN: "Main stream", STREAM_SUB: "Sub stream"}),
                vol.Required(
                    CONF_SNAPSHOT_STREAM,
                    default=current.get(CONF_SNAPSHOT_STREAM, STREAM_SUB),
                ): vol.In({STREAM_MAIN: "Main stream", STREAM_SUB: "Sub stream"}),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
