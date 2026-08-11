<img src="custom_components/hikvision_nvr/brand/logo.png" alt="Hikvision NVR for Home Assistant" width="420">

# Hikvision NVR for Home Assistant

[![Validate](https://github.com/techmarkai/hikvision-nvr-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/techmarkai/hikvision-nvr-ha/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/techmarkai/hikvision-nvr-ha?sort=semver)](https://github.com/techmarkai/hikvision-nvr-ha/releases)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-black.svg)](LICENSE)

Live view, recorded-history playback, motion events and a full REST API for a
Hikvision NVR/DVR — as a native Home Assistant integration, added from the UI.

Developed and verified against a **DS-7608NI-K2/8P, firmware V4.40.015, 8
channels**. Capabilities are read from the device rather than assumed, so other
ISAPI models work to the extent their firmware exposes the same surfaces.

<p align="center">
  <img src="docs/images/card-live.png" alt="The card in live view: one channel large, the rest as a strip below" width="480">
  &nbsp;
  <img src="docs/images/setup-dialog.png" alt="Adding an NVR from the Home Assistant UI" width="420">
</p>
<p align="center"><sub>The card in live view, and setup from the UI. Camera imagery is blurred here; it is not blurred in the integration.</sub></p>

## What you get

| | |
|---|---|
| **Live view** | One `camera` entity per channel, RTSP → HLS through Home Assistant's own stream engine. Works in the app, on the web, on Chromecast. |
| **Playback** | Scrub a day of recordings on a timeline and play any moment, straight from the NVR's disks. No local copy, no re-recording. |
| **Media browser** | Recordings appear under **Media → Hikvision NVR → camera → day**. |
| **Events** | Motion, line crossing, intrusion, tamper, video loss, face detection and more — pushed over ISAPI's alert stream, not polled. The integration asks the NVR which events each channel actually supports and creates exactly those, so nothing dead appears and nothing real is missed. |
| **NVR health** | Uptime, CPU, memory, per-disk usage, free space and health, online channel count, plus disk / network / illegal-login / recording-failure sensors. |
| **Camera health** | A connectivity sensor per channel, from the NVR's own per-channel probe — truer than an ICMP ping, and it works even when the cameras sit on the NVR's PoE subnet. |
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

Add it from **Add card → Hikvision** and pick everything from dropdowns — the
NVR, which cameras to show, whether it opens on live or history, live quality
and the grid width. No YAML, no entity ids to look up.

The equivalent YAML, if you prefer it:

```yaml
type: custom:hikvision-nvr-card
device: DS-7608NI-…      # optional, defaults to the first NVR
channels: [1, 2, 4]      # optional, defaults to all
default_mode: live       # or: playback
live_stream: 1           # 1 = main, 2 = sub
columns: 4
```

In **History**, the **Clip** dropdown sets how much video a click on the timeline
plays — and therefore how much **Download** saves, so the two never disagree.

The timeline zooms: **scroll** on it (or **pinch** on a touchscreen) to zoom
around the pointer. Once zoomed, move around with the **◀ ▶** buttons, by
**dragging**, or with the arrow keys; **Whole day** goes back out. Tick labels
get finer as you zoom in. All of it works on the day already loaded, so zooming
never waits on the NVR.

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
- **Events to expose** — which detections become binary sensors. Only what your
  device actually reports is offered, and unticking one removes its entities
  rather than leaving them behind. A capable NVR reports far more than most
  people want.
- **Live view stream** — main (full resolution) or sub (light, starts faster).
- **Snapshot stream** — sub stream keeps thumbnails cheap on large systems.

## Documentation

- [docs/API.md](docs/API.md) — the REST API, endpoint by endpoint, with examples.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how it is built, verified device
  behaviour, operations and troubleshooting.

## A note on H.265

If your channels record in H.265/HEVC, playback works in Safari, iOS and most
Android devices, but desktop Chrome and Firefox cannot decode HEVC in HLS. Set
the recording or sub-stream codec to **H.264** on the NVR for universal
playback, or put go2rtc in front to transcode. `export_recording` produces a
plain MP4 that plays everywhere either way.

## Motion sensors staying off

A Hikvision trigger can record, notify, both, or neither — and "record only" is
a common factory default. A channel set that way fills the disk with motion
clips while Home Assistant never hears a thing, because only *Notify
Surveillance Center* pushes an event to the alert stream this integration
listens on.

Check with the service, which reports what it changed:

```yaml
action: hikvision_nvr.enable_notifications
response_variable: result      # {"changed": ["VMD-1", ...], "count": 8}
```

It only ever adds the notification; recording and every other linkage is left
exactly as it was. Narrow it with `event_type:` and `channel:` if you want.

## Branding

The mark in `brand/` is original artwork for this project, not the Hikvision
logo — that is Hikvision's trademark and not ours to redistribute. "Hikvision"
appears here only to say which hardware this integration talks to.

The icon on **Settings → Devices & Services** comes from
`custom_components/hikvision_nvr/brand/`. Home Assistant serves brand images
straight from any integration that has a `brand` directory, so nothing needs to
be published anywhere for it to appear. Only `icon.png` is required — Home
Assistant falls back through it for the `@2x` and dark variants.

To use your own artwork, edit `brand/icon.svg` and run `python brand/render.py`,
or drop your own PNGs into `custom_components/hikvision_nvr/brand/`.

Submitting to [home-assistant/brands](https://github.com/home-assistant/brands)
is therefore optional; it would only matter if this integration were ever
accepted into Home Assistant core. Assets and instructions for that are in
[`brand/home-assistant-brands/`](brand/home-assistant-brands/README.md).

## Contributing

Pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
checks CI runs and the one structural rule (`isapi.py` never imports Home
Assistant). Report anything exploitable through
[SECURITY.md](SECURITY.md) rather than a public issue.

This project is not affiliated with or endorsed by Hikvision.

## Licence

MIT — see [LICENSE](LICENSE).
