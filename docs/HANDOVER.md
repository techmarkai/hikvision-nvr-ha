# Handover — Hikvision NVR integration

Everything a second engineer needs to own this: what was built, what was proven
against real hardware, where the sharp edges are, and how to operate it.

---

## 1. What this is

A Home Assistant **custom integration** (not a Supervisor add-on — it runs
inside Home Assistant, so it gets entities, the media browser and the auth
system for free) plus a **custom Lovelace card**.

```
custom_components/hikvision_nvr/
  isapi.py          ISAPI client. Pure aiohttp; no Home Assistant imports.
  coordinator.py    Polling + the push event listener.
  config_flow.py    UI setup, reauth, reconfigure, options.
  entity.py         Device-registry wiring shared by all platforms.
  camera.py         One camera entity per channel.
  binary_sensor.py  Event entities, created on demand.
  sensor.py         Disk usage / free space / online channel count.
  media_source.py   NVR → camera → day → segment browsing.
  api.py            The REST API and the HLS stream cache.
  services.py       search_recordings, export_recording, ptz, reboot.
  diagnostics.py    Redacted dump for bug reports.
  frontend.py       Static path, auto-loaded card, sidebar panel.
  frontend/         The card and the panel, shipped with the integration.
tests/live_check.py         End-to-end check against a real NVR.
```

The layering matters: **`isapi.py` knows nothing about Home Assistant**. It can
be run, tested and debugged standalone, which is exactly what `live_check.py`
does. Anything device-specific belongs there; anything Home Assistant-specific
belongs above it.

---

## 2. Verified device behaviour

Everything below was confirmed against the live unit, not read from a datasheet.

**Device:** DS-7608NI-K2/8P · firmware V4.40.015 · 8 channels ·
serial `DS-7608NI-K2-8P08201711…WCVU` · MAC `b4:a3:82:xx:xx:xx`

| Surface | Endpoint | Result |
|---|---|---|
| Identity | `GET /ISAPI/System/deviceInfo` | 200. Both **Basic and Digest** accepted. |
| Channels | `GET /ISAPI/ContentMgmt/InputProxy/channels` | 8 named channels, all online. |
| Channel health | `…/channels/status` | `online`, source IP, per-channel stream ids. |
| Streams | `GET /ISAPI/Streaming/channels` | 101/102 … 801/802. **Channel 3 has no sub-stream.** |
| Snapshot | `GET /ISAPI/Streaming/channels/{id}/picture` | 200, ~29 KB JPEG. |
| Storage | `GET /ISAPI/ContentMgmt/Storage` | 2 × 2.8 TB SATA, one `Redund`, one `RW`. |
| Recording search | `POST /ISAPI/ContentMgmt/search` | 784 segments in 24 h on channel 1. |
| Segment download | `POST /ISAPI/ContentMgmt/download` | 200, `Opaque/data`, streams fine. |
| Event push | `GET /ISAPI/Event/notification/alertStream` | 200, `multipart/mixed`, live events. |
| Live RTSP | `rtsp://…/Streaming/Channels/101` | DESCRIBE 200, **H.265**. |
| Playback RTSP | `rtsp://…/Streaming/tracks/101/?starttime=…&endtime=…` | DESCRIBE 200, video + G.722.1 audio. |
| Not supported | `GET /ISAPI/System/Video/inputs/channels` | 403 `notSupport` — handled. |

Things the firmware does that the code exists to absorb:

1. **Search paging.** `maxResults` is capped near 40 regardless of what you ask
   for, and `responseStatusStrg` returns `MORE`. `async_search_recordings`
   pages with `searchResultPostion` until the caller's limit is met.
2. **Namespaces vary** between `isapi.org`, `hikvision.com` and PSIA across
   endpoints. All parsing strips namespaces rather than matching them.
3. **`channelID` is empty** on device-wide events (video loss, disk). Those are
   filed against channel `0`, which is the NVR device itself.
4. **Not every channel has both streams.** Requesting a non-existent sub-stream
   fails the stream outright, so both camera and API clamp to what the channel
   actually reports.
5. **The serial number contains a slash** (`DS-7608NI-K2/8P0820…`). It is fine
   as an entity unique_id, but it cannot go in a URL path or a media-source
   identifier -- `coordinator.slug` is the sanitised form used for both.
6. **The NVR clock is its own.** Event freshness is stamped on receipt, not from
   `<dateTime>`, so a drifting NVR clock cannot make every motion event look
   expired.

---

## 3. How the pieces work

### Live view
`camera.stream_source()` returns the RTSP URL with embedded credentials. Home
Assistant's `stream` component does the RTSP → HLS work, which means go2rtc,
WebRTC, casting and the mobile app all work with no extra code.

### create_stream across versions
`DynamicStreamSettings` has lived in `stream`, `stream.core` and now
`camera.prefs`, and is a *required* argument to `create_stream`. On Python 3.14
`inspect.signature` evaluates annotations (PEP 649) and blows up on Home
Assistant's `TYPE_CHECKING`-only import of it, so `_create_stream_kwargs`
reads parameter names off the code object and resolves the class from a list of
candidate modules. If it ever cannot, it raises a named error rather than a
TypeError from inside Home Assistant.

### Playback
The NVR itself can replay a time range over RTSP
(`/Streaming/tracks/{track}/?starttime=…&endtime=…`). We hand that URL to the
same `stream` component and return the HLS endpoint. Nothing is copied, cached
or re-encoded — seeking is a new RTSP session on the NVR, which is why it is
fast.

`api.py::_async_get_stream` keeps a small cache of running streams keyed by
range, cancels the idle timer on re-request, and caps concurrency at 8. Streams
stop 5 minutes after their last use.

### Why playback does not use the stream component
These NVRs record a **G.722.1** audio track. PyAV cannot name that codec, so
Home Assistant's `stream/worker.py` dies with `'AudioStream' object has no
attribute 'name'` and the player sits at 0:00 forever. Live view is unaffected
because the live RTSP stream carries no audio. Playback therefore goes through
`ClipView`: ffmpeg remuxes the NVR's playback RTSP into fragmented MP4 with
`-an`, streamed straight to a `<video>` tag. No transcode, no segmenting, lower
latency than HLS, and signable because its times live in the path rather than
the query. `export_recording` drops audio for the same reason.

### Events
One long-lived HTTP connection consumes the multipart alert stream. It is an
entry-owned background task with exponential backoff (5 s → 5 min). Events
dispatch through `async_dispatcher_send`; entities are created lazily the first
time an event type is actually seen, so a system with only motion configured
does not get 160 dead entities.

`EVENT_AUTO_OFF` (15 s) exists because some firmwares repeat `active` and never
send `inactive`.

### The card and panel
Both ship inside the integration and are served from `/hikvision_nvr_frontend`.
`add_extra_js_url` loads the card on every dashboard, so there is no Lovelace
resource to register; `async_register_built_in_panel` adds the **Cameras**
sidebar entry, whose *Add NVR* button deep-links to the config-flow dialog. The
card guards its `customElements.define` because the panel imports it under a
second URL.

The card uses Home Assistant's own `<ha-hls-player>` — obtained by forcing the frontend's
camera chunk to load — so there is no bundled `hls.js` and no build step. Falls
back to a native `<video>` where the frontend player is unavailable. Thumbnails
use `auth/sign_path` signed URLs and stop refreshing when the tab is hidden.
Device-supplied strings are HTML-escaped before rendering.

---

## 4. Security posture

- The REST API is `requires_auth = True` — standard Home Assistant tokens only.
- NVR credentials never leave the server. `rtsp_url` in API responses is
  credential-free; only the internal `stream_source` carries the login.
- `export_recording` rejects any `filename` containing a path separator or `..`,
  and writes only under the configured media directory.
- Diagnostics redact host, credentials, serial, MAC and camera IPs.
- XML is parsed with `defusedxml`; a compromised or spoofed NVR cannot land an
  XXE or billion-laughs payload.
- Range limits are enforced server-side: 31 days for search, 6 hours for
  playback, 2 hours for download and export.

---

## 5. Operations

### Verify the device end of the stack

```bash
python tests/live_check.py 192.168.1.222 admin '<password>'
```

Exercises connect, channel discovery, snapshot, storage, recording search with
paging, playback URL construction, segment download and the event stream. It
asserts, so a broken firmware upgrade fails it loudly. Run this **first**
whenever something looks wrong — it isolates "the NVR changed" from "Home
Assistant changed".

### Logs

```yaml
logger:
  logs:
    custom_components.hikvision_nvr: debug
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `invalid_auth` with correct password | NVR locked the account after failed attempts | Unlock in *System → User*, wait 30 min, retry. |
| `cannot_connect` | ISAPI disabled | Enable Hikvision-CGI in *Security → Authentication*. |
| `No dts in N consecutive packets` in the log | RTSP over UDP with camera timestamps | Already fixed: the camera entities set `stream_options` to TCP + wallclock timestamps. |
| One disk full, the other idle | A disk is set to `Redund` while no channel has redundant recording enabled, so it stores nothing | Set every disk to `RW` (*Storage → Storage Device → Edit*), or enable redundant recording if that is what you want. |
| Live view black in desktop Chrome | H.265 sub-stream | Switch the channel to H.264, or put go2rtc in front. |
| Live view fine, playback black | Recording codec is H.265 | Same fix, or use `export_recording`. |
| Motion sensors never fire | Motion not armed on the NVR, or no *Notify Surveillance Center* linkage | Enable both per channel. |
| Timeline empty for a day | Nothing recorded, or the day predates the disk overwrite window | Cross-check with `search_recordings`. |
| Playback stalls after ~6 h range | Deliberate server-side cap | Request a shorter range. |
| Snapshots stop, live still works | Sub-stream disabled on that channel | Set the snapshot stream to main in Options. |

### Capacity

Each concurrent live view and each playback session is one RTSP connection to
the NVR plus one ffmpeg process in Home Assistant. A DS-7608 handles a handful
comfortably; the 8-stream cap in `api.py` is the guard rail. Prefer sub-streams
for wall views.

---

## 6. Deliberate limitations

Each of these is a decision, not an oversight — with the trigger for revisiting.

| Not built | Why | Build it when |
|---|---|---|
| Two-way audio | Needs the proprietary SDK, not ISAPI | A model with ISAPI `/ISAPI/System/TwoWayAudio` is in scope. |
| Per-camera config write-back (motion regions, schedules) | The NVR's own UI does it better and the XML schemas differ per firmware | Users ask to automate arming. |
| Days-with-recordings shading in the media browser | Would mean 30 search calls to grey out a few dates | Search gets cheap, or a `trackDailyDistribution` endpoint is confirmed on target firmware. |
| ONVIF fallback | Every tested unit speaks ISAPI | A non-Hikvision OEM unit needs support. |
| Local recording/retention in Home Assistant | The NVR already has 5.7 TB and does it better | Never, ideally. |

---

## 7. Deployment status

- **Written and validated on disk:** all modules compile, JSON/YAML parse, the
  card passes `node --check`.
- **Validated against the live NVR at 192.168.1.222:** every ISAPI surface in
  §2, via `tests/live_check.py`, all assertions passing.
- **Not yet installed on the Home Assistant instance** at
  `https://homeassistant.local:8123` (HA 2026.8.1). That instance has no Samba or SSH
  add-on, and the File editor add-on's write API is not reachable through the
  Supervisor ingress proxy, so the files could not be copied over remotely.
  Install by either route in the README (HACS custom repository, or copying the
  two directories into `config/`), then restart and add the integration.

### Post-install checklist

1. Restart Home Assistant, check the log for `hikvision_nvr`.
2. Add the integration; expect **8 camera entities**, storage sensors, and one
   motion sensor per channel.
3. Open a camera — live view should start within a couple of seconds.
4. **Media → Hikvision NVR** — browse to a day and play a segment.
5. Add the card, switch to **History**, click the timeline.
6. `curl` `/api/hikvision_nvr/devices` with a long-lived token.
7. Walk in front of a camera; the motion sensor should turn on within a second.
