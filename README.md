# Hikvision NVR for Home Assistant

Live view, recorded-history playback, motion events and a full REST API for a
Hikvision NVR/DVR — as a native Home Assistant integration, added from the UI.

Verified against a **DS-7608NI-K2/8P, firmware V4.40.015, 8 channels**.

## What you get

| | |
|---|---|
| **Live view** | One `camera` entity per channel, RTSP → HLS through Home Assistant's own stream engine. Works in the app, on the web, on Chromecast. |
| **Playback** | Scrub a day of recordings on a timeline and play any moment, straight from the NVR's disks. No local copy, no re-recording. |
| **Media browser** | Recordings appear under **Media → Hikvision NVR → camera → day**. |
| **Events** | Motion, line crossing, intrusion, tamper, video loss, disk errors — pushed over ISAPI's alert stream, not polled. |
| **Storage** | Per-disk usage and free-space sensors. |
| **Sidebar panel** | A **Cameras** page with the whole system in it, and an **Add NVR** button — IP, username and password, all from the GUI. |
| **REST API** | `/api/hikvision_nvr/…` for third-party mobile apps, using ordinary Home Assistant tokens. |
| **Services** | Search recordings, export a clip to MP4, PTZ, reboot. |

## Install

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add this repository, category **Integration**.
2. Install **Hikvision NVR**, then restart Home Assistant.
3. Restart, then either click **Cameras** in the sidebar → **Add NVR**, or go to
   **Settings → Devices & Services → Add Integration → Hikvision NVR**.

### Manual

Copy `custom_components/hikvision_nvr/` into your `config/custom_components/`
directory and restart Home Assistant.

### The card and the sidebar

Nothing to do. The integration ships its own frontend and registers it on
startup, so after installing you get:

- a **Cameras** entry in the sidebar — the full-page view, with an **Add NVR**
  button that opens the setup dialog right there;
- the card available on any dashboard, with no Lovelace resource to add:

```yaml
type: custom:hikvision-nvr-card
columns: 4          # optional, thumbnail grid width
default_mode: live  # or: playback
# device: 192.168.1.222      # optional, defaults to the first NVR
# channels: [1, 2, 4]        # optional, defaults to all
```

If the card does not appear right after an update, hard-refresh the browser
(Ctrl-Shift-R) — the frontend caches by version.

## Setup

| Field | Notes |
|---|---|
| Host | IP or hostname of the **NVR** (not a camera). |
| Username / password | A device account. Operator is enough; admin is only needed for `reboot`. |
| HTTP port | 80 by default, 443 with HTTPS. |
| RTSP port | 554 by default. |

On the NVR, make sure **Hikvision-CGI / ISAPI** is enabled
(*Configuration → System → Security → Authentication*) and that the account is
not locked out from earlier failed logins.

## Options

**Settings → Devices & Services → Hikvision NVR → Configure**

- **Channels to expose** — hide unused channels.
- **Live view stream** — main (full resolution) or sub (light, starts faster).
- **Snapshot stream** — sub stream keeps thumbnails cheap on large systems.

## Documentation

- [docs/API.md](docs/API.md) — the REST API, endpoint by endpoint, with examples.
- [docs/HANDOVER.md](docs/HANDOVER.md) — architecture, verified device behaviour,
  operations and troubleshooting.

## A note on H.265

If your channels record in H.265/HEVC, playback works in Safari, iOS and most
Android devices, but desktop Chrome and Firefox cannot decode HEVC in HLS. Set
the recording or sub-stream codec to **H.264** on the NVR for universal
playback, or put go2rtc in front to transcode. `export_recording` produces a
plain MP4 that plays everywhere either way.

## Licence

MIT.
