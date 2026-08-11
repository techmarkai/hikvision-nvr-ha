"""Constants for the Hikvision NVR integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "hikvision_nvr"

CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"
CONF_RTSP_PORT = "rtsp_port"
CONF_STREAM = "stream"
CONF_SNAPSHOT_STREAM = "snapshot_stream"
CONF_CHANNELS = "channels"

DEFAULT_PORT = 80
DEFAULT_SSL_PORT = 443
DEFAULT_RTSP_PORT = 554
DEFAULT_TIMEOUT = 15

# 1 = main stream (quality), 2 = sub stream (fast/low bandwidth).
STREAM_MAIN = 1
STREAM_SUB = 2

# Device/state polling. Events arrive by push (alertStream), so this is only a
# safety net for channel online/offline and storage.
SCAN_INTERVAL = timedelta(seconds=60)

# How long a pushed motion/event stays "on" without a matching inactive event.
EVENT_AUTO_OFF = timedelta(seconds=15)

# Event types we expose as binary sensors, mapped to HA device classes.
#
# The device declares which of these it actually supports, per channel, at
# /ISAPI/Event/triggers -- see HikvisionISAPI.async_update_capabilities. This
# table only decides how a supported event is presented.
EVENT_CLASSES: dict[str, str] = {
    "VMD": "motion",
    "linedetection": "motion",
    "fielddetection": "motion",
    "regionEntrance": "motion",
    "regionExiting": "motion",
    "attendedBaggage": "motion",
    "unattendedBaggage": "motion",
    "facedetection": "occupancy",
    "faceSnap": "occupancy",
    "peopleDetection": "occupancy",
    "scenechangedetection": "tamper",
    "videoloss": "problem",
    "tamperdetection": "tamper",
    "shelteralarm": "tamper",
    "diskfull": "problem",
    "diskerror": "problem",
    "nicbroken": "problem",
    "ipconflict": "problem",
    "illaccess": "problem",
    "badvideo": "problem",
    "recordingfailure": "problem",
    "PIR": "motion",
    "IO": "motion",
    "softIO": "motion",
}

# Created for every capable channel. Anything else the device declares is
# created too, but disabled by default so the entity list stays workable --
# users switch on what they care about.
DEFAULT_ENABLED_EVENTS = frozenset(
    {"VMD", "linedetection", "fielddetection", "tamperdetection", "videoloss"}
)

SIGNAL_EVENT = f"{DOMAIN}_event"

ATTR_CHANNEL = "channel"
ATTR_START = "start_time"
ATTR_END = "end_time"

SERVICE_SEARCH_RECORDINGS = "search_recordings"
SERVICE_EXPORT_RECORDING = "export_recording"
SERVICE_PTZ = "ptz"
SERVICE_REBOOT = "reboot"
SERVICE_ENABLE_NOTIFICATIONS = "enable_notifications"
