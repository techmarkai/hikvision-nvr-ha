# Changelog

Per-release notes live on the
[Releases page](https://github.com/techmarkai/hikvision-nvr-ha/releases). This
file records the milestones worth knowing about when deciding whether to
upgrade.

Versioning is [semantic](https://semver.org/): the minor version moves for
features, the patch version for fixes. `manifest.json` carries the version HACS
installs, and the frontend cache-busts on it — so an upgrade always ships a
matching card.

## 1.20

Home Assistant best-practices pass. A repair now appears when the NVR is set to
record a detection but not to report it — the condition that makes motion
sensors sit silently off. Only detections that are actually switched on are
counted.

## 1.19

Live audio, where the hardware has it. Channels are probed for a microphone and
codec, and only those get a speaker control. G.711 is passed through untouched.

## 1.16 – 1.18

WebRTC live view through Home Assistant's native camera stream element, which
took median start-up from 4.6 s to 1.6 s on the reference device. Downloads
moved from a real-time RTSP read to the NVR's bulk endpoint plus a remux —
about fifteen times quicker — and the clip length became selectable so a
download saves exactly what the timeline plays.

## 1.13 – 1.15

Timeline zoom and pan, on the day already loaded, so zooming never waits on the
NVR. Event sensors became opt-in per type: capability detection offers only what
the device reports, and unticking one removes its entities instead of leaving
them behind.

## 1.10 – 1.12

Capability-driven events, NVR health and storage sensors, per-channel
connectivity from the device's own probe, and the branding shown on the
integrations dashboard.

## 1.0 – 1.9

Live view, recorded-history playback on a timeline, the media browser, the
sidebar panel with GUI setup, the auto-registered Lovelace card, the REST API
and the services.
