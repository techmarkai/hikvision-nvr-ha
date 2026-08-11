# Changelog

Versioning is [semantic](https://semver.org/): the minor version moves for
features, the patch version for fixes. `manifest.json` carries the version HACS
installs, and the frontend cache-busts on it — so an upgrade always ships a
matching card.

## 1.0.0 — first release

- **Live view** through Home Assistant's own camera element, which negotiates
  WebRTC via go2rtc: a decoding frame in about a second on the reference
  device, against roughly three to five for an HLS URL. HLS remains as a
  fallback and is only fetched if the WebRTC path is unavailable or stays
  black.
- **Playback** on a zoomable timeline, straight from the NVR's disks. Zoom and
  pan work on the day already loaded, so neither waits on the device. History
  plays as a fragmented MP4 in a plain `<video>`, which starts sooner than HLS
  and seeks natively.
- **Downloads and exports** from the NVR's bulk endpoint rather than its
  playback RTSP, which the device paces at real time — sixty seconds of video
  in about four and a half seconds against a sixty second floor — trimmed to
  the range asked for.
- **Events** pushed over ISAPI's alert stream rather than polled, created from
  the device's own capability declaration and switchable per type, so a capable
  NVR does not force fifty entities on anyone.
- **Live audio** where the channel actually has a microphone. G.711 passes
  through untouched.
- **Health**: uptime, CPU, memory, per-disk usage, free space and status, and
  per-channel connectivity from the NVR's own probe rather than an ICMP ping.
  A storage alarm re-reads the disks immediately, because an intermittent fault
  can begin and end between two polls.
- **Card, sidebar panel and REST API**, all registered on install — no Lovelace
  resource to add and no YAML to write.
- **Services**: search recordings, export to MP4, PTZ, reboot, and
  `enable_notifications` for the condition that makes motion sensors sit
  silently off — a detection the NVR records but never reports. That one has a
  preview mode, only ever adds the missing linkage, and is proven idempotent
  and strictly scoped against real hardware.
