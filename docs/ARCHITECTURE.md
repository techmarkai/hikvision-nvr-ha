# Architecture and operations

How the integration is put together, what was proven against real hardware,
where the sharp edges are, and how to run it.

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

**Reference device:** DS-7608NI-K2/8P · firmware V4.40.015 · 8 channels.

| Surface | Endpoint | Result |
|---|---|---|
| Identity | `GET /ISAPI/System/deviceInfo` | 200. Both **Basic and Digest** accepted. |
| Channels | `GET /ISAPI/ContentMgmt/InputProxy/channels` | 8 named channels, all online. |
| Channel health | `…/channels/status` | `online`, source IP, per-channel stream ids. |
| Streams | `GET /ISAPI/Streaming/channels` | 101/102 … 801/802. **Channel 3 has no sub-stream.** |
| Snapshot | `GET /ISAPI/Streaming/channels/{id}/picture` | 200, ~29 KB JPEG. |
| Storage | `GET /ISAPI/ContentMgmt/Storage` | 2 × 2.8 TB SATA, one `Redund`, one `RW`. |
| Recording search | `POST /ISAPI/ContentMgmt/search` | 784 segments in 24 h on channel 1. |
| Segment download | `POST /ISAPI/ContentMgmt/download` | 200, `Opaque/data`, ~1.4 MB/s. Only accepts a playbackURI the device issued. |
| Event push | `GET /ISAPI/Event/notification/alertStream` | 200, `multipart/mixed`, live events. |
| Live RTSP | `rtsp://…/Streaming/Channels/101` | DESCRIBE 200, **H.265**. |
| Playback RTSP | `rtsp://…/Streaming/tracks/101/?starttime=…&endtime=…` | DESCRIBE 200, video + G.722.1 audio. |
| Not supported | `GET /ISAPI/System/Video/inputs/channels` | 403 `notSupport` — handled. |
| Capabilities | `GET /ISAPI/Event/triggers` | 16 event types, per channel; 6 device-wide. |
| System status | `GET /ISAPI/System/status` | Uptime, CPU, memory. |

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
5. **The serial number contains a slash** (`DS-7608NI-K2-8P0000000000AAAA000000000AAAA`). It is fine
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

`api.py::async_get_stream` keeps a small cache of running streams keyed by
range, cancels the idle timer on re-request, and caps concurrency at 8. Streams
stop 5 minutes after their last use.

### Live view latency: WebRTC, measured

Time from mounting the player to the first frame with real pixels
(`videoWidth > 100 && readyState >= 2`), measured in the card's own stage:

| Path | First frame |
|---|---|
| Our `/live` -> HA `stream` -> HLS (was) | 2899, 4599, 5078 ms — median **4599** |
| `ha-camera-stream` -> WebRTC via go2rtc (now) | 994, 1005, 1562, 4002 ms — median **1562** |

Roughly 3x faster, with the best cases at ~1.0s, and it no longer starts a
second stream (and second ffmpeg) beside the one the camera entity already runs.

Three things had to be right, none of them obvious:

* `ha-camera-stream` takes its Home Assistant handles from **Lit contexts**
  (`configContext`, `apiContext`, `connectionContext`), not from a `hass`
  property. Setting `hass` does nothing. The contexts do reach into this card's
  nested shadow DOM.
* It is a Lit element, so it must be **attached before** `stateObj` is assigned;
  properties set while detached are dropped on upgrade, leaving a black stage.
* A watchdog falls back to the HLS path if nothing decodes within
  `NATIVE_LIVE_TIMEOUT`, so a channel WebRTC cannot carry never shows black.

**Benchmarking trap, for whoever measures this next.** `_render()` replaces
`ha-card.innerHTML`, so a harness that captures `#stage` *before* calling it
then searches a detached node and always finds nothing. That mistake made a
working build look broken and cost a release to revert and re-apply. Re-query
the stage on every poll. Also leave several seconds between channel switches:
tearing streams down and up every second measures the teardown, not the start.

**Open question: transcoding cost.** Channels 1, 2, 3 and 7 are H.265, which
browsers cannot decode over WebRTC, yet they negotiate WebRTC successfully --
so go2rtc is very likely transcoding them. Only the selected camera streams at
a time (the wall is JPEG snapshots), so it is one transcode at most, and the
card defaults to the sub-stream. Setting **Live quality** to the main stream on
an H.265 channel would ask it to transcode 2560x1440, which has not been
measured and should be before anyone recommends it.

### Playback seek latency: where the time actually goes

Measured at the RTSP layer, with no ffmpeg involved:

| Operation | Time |
|---|---|
| `DESCRIBE` on a **live** URL | 92 ms |
| `DESCRIBE` on a **playback** URL (a seek) | **2117-2902 ms** |
| `SETUP` + `PLAY` after that | ~50-60 ms |
| `PLAY` with a new `Range` on an **already open** session | **48, 79, 80 ms** |

So a seek costs what it costs because the NVR spends two to three seconds
answering the first `DESCRIBE` for a time range -- locating footage on disk.
Our whole pipeline adds about half a second on top: clip time-to-first-byte is
2.60-2.87s against a 2.12s `DESCRIBE` for the same range.

Two consequences worth knowing before optimising further:

* **Nothing on our side can fix a 2.9s `DESCRIBE`.** ffmpeg flags, proxy hops
  and round trips are noise beside it.
* **Re-seeking inside an open session is ~35x faster** (50-80ms). Exploiting
  that means holding RTSP sessions server-side and re-issuing `PLAY` with a new
  `Range`, then depacketising RTP to fMP4 ourselves -- ffmpeg cannot be
  re-seeked mid-stream. That is go2rtc-class machinery and was not attempted.

Older footage is markedly slower to locate: a clip from 20 hours ago took
14.9s to first byte against 2.6s for one from 4 hours ago. Same code, same
range length.

**Tried and reverted: `-frag_duration 500000`.** The theory was sound --
`frag_keyframe` cannot close a fragment until the next keyframe arrives, and
these are 2560x1440 recordings with a long GOP. In practice it made things
worse: no decodable frame within 30s, against 22.8s before. A timed fragment
can open mid-GOP, and a browser cannot start decoding a fragment that does not
begin on a keyframe.

Playback also has no lighter option available: `/ISAPI/ContentMgmt/record/tracks`
reports 8 tracks, one per channel, all main-stream. There is no sub-stream
recording to play back instead of 1440p H.265.

### Why playback does not use the stream component
These NVRs record a **G.722.1** audio track. PyAV cannot name that codec, so
Home Assistant's `stream/worker.py` dies with `'AudioStream' object has no
attribute 'name'` and the player sits at 0:00 forever. Live view is unaffected
because the live RTSP stream carries no audio. Playback therefore goes through
`ClipView`: ffmpeg remuxes the NVR's playback RTSP into fragmented MP4 with
`-an`, streamed straight to a `<video>` tag. No transcode, no segmenting, lower
latency than HLS, and signable because its times live in the path rather than
the query. `export_recording` drops audio for the same reason.

### Capability detection
`/ISAPI/Event/triggers` is the device's own declaration of which events it
supports and on which channel: ids read `VMD-3`, `tamper-1`, or plain `diskfull`
for the NVR. `async_update_capabilities` turns that into
`{channel: {event types}}`, with channel 0 for device-wide events, and the
binary sensor platform creates exactly those entities. Motion, line crossing,
intrusion, tamper and video loss are enabled by default; everything else the
device declares is created but switched off, so a capable NVR does not bury the
user. A firmware that does not expose the endpoint falls back to motion only.

On the test rig this yields per-channel sets that genuinely differ — channel 8
has no tamper or line/field detection, channels 1 and 2 have no line crossing.

**Unattributed events.** This firmware emits `videoloss` every few seconds with
an empty `channelID`, while declaring videoloss per channel. Taken literally the
per-channel sensors would never move. `HikvisionCoordinator._channels_for`
therefore applies an event that names no channel to every channel that declares
it, and only device-level types land on the NVR.

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

## 3b. Audio

Audited against the test rig, and there is very little to carry:

| Channel | Audio | Codec |
|---|---|---|
| 8 (Camera 01) | enabled | G.711 u-law |
| 1-7 | **not enabled** | -- |

Two-way audio exists on channels 1 and 2, also G.711 u-law, and is not wired up
(it needs the proprietary SDK on most models rather than ISAPI).

What this means:

* **Live**, audio is achievable on channel 8 at no cost: G.711 u-law is PCMU,
  a native WebRTC codec, so go2rtc can carry it without transcoding. The card
  currently mutes the player, and browsers block autoplay with sound anyway, so
  it would need an unmute control rather than a default.
* **Playback** audio would have to be transcoded. Recorded audio comes back as
  G.722.1 (Siren), which has no valid MP4 mapping and is what crashes Home
  Assistant's HLS worker -- see the section above. `-an` stays until someone
  wants AAC transcoding for one channel.

Nothing here is worth building until someone actually wants audio from Camera 01.

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
python tests/live_check.py 192.168.1.10 admin '<password>'
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
| Motion sensors never fire | The trigger is linked to `record` but not `center` | Run `hikvision_nvr.enable_notifications`. Recording is untouched; it only adds the notification. Confirm with `GET /ISAPI/Event/triggers`. |
| Card shows in a browser but not the mobile app | The app cached `index.html`, which is where `add_extra_js_url` injects the script | Fixed by also registering a Lovelace resource, delivered over the websocket. Failing that, Companion App → Debugging → Reset frontend cache. |
| No icon on the integration page | `custom_components/hikvision_nvr/brand/icon.png` is missing | Home Assistant serves brand images from an integration that has a `brand` directory (`Integration.has_branding`), via `/api/brands/integration/<domain>/icon.png`. Run `python brand/render.py` and restart. No PR to home-assistant/brands is needed. |
| Timeline empty for a day | Nothing recorded, or the day predates the disk overwrite window | Cross-check with `search_recordings`. |
| Playback stalls after ~6 h range | Deliberate server-side cap | Request a shorter range. |
| Snapshots stop, live still works | Sub-stream disabled on that channel | Set the snapshot stream to main in Options. |

### Stability findings

Two problems were visible in a day of real use, both now fixed:

* **Signed snapshot URLs expired under a backgrounded tab.** They were minted
  with a 300 second life while the card refreshes every 10 seconds, which
  sounds safe -- until the browser throttles a background tab's timers, wakes,
  and re-requests a dead URL. Home Assistant counts every rejection towards
  banning the browser's IP, and it had logged hundreds. Now an hour.
* **`ffmpeg exited 8` warnings.** Exit 8 is "could not write output", which is
  what a viewer closing the tab looks like from the server side. Logged as a
  warning it reads like a fault; it is now treated as normal alongside -9.

Not seen at all, having been watched for: coordinator update failures, event
stream disconnects that did not recover, orphaned streams past the eight-stream
cap, or entity churn.

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
