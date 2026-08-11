# Changelog

Versioning is [semantic](https://semver.org/): the minor version moves for
features, the patch version for fixes. `manifest.json` carries the version HACS
installs, and the frontend cache-busts on it — so an upgrade always ships a
matching card.

## 1.20.0 — first public release

Everything below 1.20 was development, released only to the machine it was
being built against; those tags have been removed. What that work produced:

- **Live view** over WebRTC where the browser and Home Assistant support it,
  HLS otherwise. Median start-up on the reference device is 1.6 s, down from
  4.6 s on the HLS-only path.
- **Playback** on a zoomable timeline, straight from the NVR's disks. Zoom and
  pan work on the day already loaded, so they never wait on the device.
- **Downloads** from the NVR's bulk endpoint plus a remux, roughly fifteen times
  faster than reading playback in real time, with a selectable clip length so a
  download saves exactly what the timeline plays.
- **Events** pushed over ISAPI's alert stream rather than polled, created from
  the device's own capability declaration and switchable per type.
- **Live audio** where the channel actually has a microphone. G.711 passes
  through untouched.
- **Health**: uptime, CPU, memory, per-disk usage and status, per-channel
  connectivity from the NVR's own probe.
- **Card, panel and REST API**, all registered on install.
- A **repair** for the condition that makes motion sensors sit silently off: a
  detection the NVR records but never reports.
