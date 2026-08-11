# Hikvision NVR — REST API

Every endpoint lives under `/api/hikvision_nvr/` on your Home Assistant host and
uses **Home Assistant's own authentication**. There is no separate account, no
separate token, no separate permission model: if a token can read
`/api/states`, it can use this API.

Base URL in the examples below: `https://homeassistant.local:8123`.

---

## 1. Authentication

Create a **Long-Lived Access Token**: Home Assistant → profile (bottom left) →
**Security** → *Long-lived access tokens* → **Create token**.

Send it as a bearer token:

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6…
```

```bash
curl -H "Authorization: Bearer $HA_TOKEN" \
     https://homeassistant.local:8123/api/hikvision_nvr/devices
```

### Signed URLs — for `<img>`, `<video>` and native players

Media elements cannot send an `Authorization` header. Ask Home Assistant to sign
a path over the WebSocket API and use the result as a plain URL:

```json
{ "id": 7, "type": "auth/sign_path",
  "path": "/api/hikvision_nvr/DS-7608NI…WCVU/1/snapshot", "expires": 300 }
```

The reply contains `path` with a `?authSig=…` query appended. That URL is valid
for `expires` seconds and needs no header. **Use it exactly as returned** —
`sign_path` signs the path together with an empty parameter list, so appending
anything (a `?stream=`, a cache-busting `&_=`) invalidates the signature and the
request is rejected as a failed login. Sign a fresh path instead; each one is
unique — hand it straight to `ImageView`,
`AVPlayer`, ExoPlayer or an `<img>` tag.

### Errors

| Status | Meaning |
|---|---|
| `400` | Bad parameter (unparseable time, range too long, non-numeric channel). |
| `401` | Missing or invalid Home Assistant token. |
| `404` | Unknown NVR or channel. |
| `502` | The NVR refused, timed out, or rejected the stored credentials. |

The body carries a human-readable `message`.

---

## 2. Identifiers

A `{device_id}` is the NVR's **serial number**, as returned by `/devices`. For
convenience the API also accepts the NVR's **IP address** or its **config-entry
title**, so `…/192.168.1.10/1/snapshot` works too.

A `{channel}` is the channel number as shown on the NVR (1-8 on a DS-7608),
**not** the ISAPI stream id (101/102).

### Time parameters

`start` and `end` accept:

- ISO 8601 with offset — `2026-08-10T14:30:00+03:00`
- ISO 8601 without offset — interpreted in the Home Assistant timezone
- Unix epoch seconds — `1786439400`

Defaults: `start` = 24 hours ago, `end` = now. All timestamps in responses are
UTC ISO 8601.

---

## 3. Endpoints

### `GET /api/hikvision_nvr/devices`

Every configured NVR with its channels and disks. This is the discovery call an
app should make first.

```json
{
  "devices": [
    {
      "id": "DS-7608NI-K2-8P0000000000AAAA000000000AAAA",
      "name": "Home",
      "host": "192.168.1.10",
      "model": "DS-7608NI-K2/8P",
      "firmware": "V4.40.015",
      "mac": "00:11:22:33:44:55",
      "available": true,
      "storage": [
        { "id": 1, "name": "hdd1", "status": "ok",
          "capacity_mb": 2861588, "free_mb": 2836480,
          "used_percent": 0.9, "property": "Redund" }
      ],
      "channels": [
        { "id": 1, "name": "Garage entrance", "online": true, "ptz": false,
          "streams": [1, 2], "ip_address": "192.168.1.120",
          "entity_id": "camera.garage_entrance" }
      ]
    }
  ]
}
```

`entity_id` is a best-effort convenience for apps that also talk to
`/api/states`; treat `id` as the authoritative key.

---

### `GET /api/hikvision_nvr/{device_id}/channels`

The channel list on its own, with ready-made URLs.

```json
{ "channels": [
  { "id": 1, "name": "Garage entrance", "online": true, "ptz": false,
    "streams": [1, 2],
    "snapshot_url": "/api/hikvision_nvr/DS-7608…WCVU/1/snapshot",
    "live_url": "/api/hikvision_nvr/DS-7608…WCVU/1/live" } ] }
```

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/snapshot`

A JPEG, right now.

| Query | Default | Notes |
|---|---|---|
| `stream` | `2` | `1` = main stream (full resolution), `2` = sub stream. |

Returns `image/jpeg` with `Cache-Control: no-store`. On the test rig a sub-stream
snapshot is ~29 KB and returns in well under a second — cheap enough to poll
every few seconds for a thumbnail wall.

```bash
curl -H "Authorization: Bearer $HA_TOKEN" \
     -o front.jpg \
     "https://homeassistant.local:8123/api/hikvision_nvr/192.168.1.10/1/snapshot?stream=1"
```

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/live`

Starts (or reuses) a live HLS stream and returns where to play it.

```json
{
  "channel": 1,
  "stream": 1,
  "hls_url": "/api/hls/8f3c…/master_playlist.m3u8",
  "rtsp_url": "rtsp://192.168.1.10:554/Streaming/Channels/101"
}
```

- `hls_url` — relative to your Home Assistant host, already authorised via its
  own token in the path. Feed it to any HLS player.
- `rtsp_url` — the NVR direct, **without credentials**. For apps that ship their
  own RTSP player and hold the camera login themselves. Only reachable on the
  same LAN as the NVR.

| Query | Default | Notes |
|---|---|---|
| `stream` | `1` | Falls back to the channel's only stream if the requested one does not exist. |

Streams are reference-counted and torn down 5 minutes after the last request, so
calling this repeatedly for the same channel is free.

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/recordings`

Search recorded segments.

| Query | Default | Notes |
|---|---|---|
| `start`, `end` | last 24 h | Max span 31 days. |
| `limit` | `100` | Max 1000. |
| `offset` | `0` | For paging. |
| `event_type` | — | e.g. `motion`, `timing`. |

```json
{
  "channel": 1,
  "name": "Garage entrance",
  "total": 784,
  "count": 2,
  "offset": 0,
  "recordings": [
    {
      "channel": 1,
      "start": "2026-08-09T20:58:48+00:00",
      "end": "2026-08-09T21:37:10+00:00",
      "duration": 2302,
      "event_type": "motion",
      "size": 17581108,
      "playback_url": "/api/hikvision_nvr/…/1/playback?start=…&end=…",
      "download_url": "/api/hikvision_nvr/…/1/download?start=…&end=…"
    }
  ]
}
```

`total` is how many segments matched on the device; `count` is how many are in
this response. The NVR caps each internal reply at 40 segments — the integration
pages through that for you, so `limit=1000` returns 1000.

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/timeline`

The same recordings collapsed into contiguous blocks — what a scrubber actually
needs. A day of motion clips becomes a handful of bars instead of 800 rows.

| Query | Default | Notes |
|---|---|---|
| `start`, `end` | last 24 h | |
| `gap` | `60` | Segments closer together than this many seconds are merged. |

```json
{
  "channel": 1,
  "segments": 784,
  "blocks": [
    { "start": "2026-08-09T20:58:48+00:00",
      "end":   "2026-08-09T23:14:02+00:00",
      "duration": 8114, "segments": 37, "event_types": ["motion"] }
  ]
}
```

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/playback`

Plays back a time range from the NVR's disks over HLS.

| Query | Required | Notes |
|---|---|---|
| `start`, `end` | yes | Max span **6 hours**. |
| `hls` | no | `1` to also start an HLS stream and return `hls_url`. Off by default: HLS goes through Home Assistant's stream worker, which crashes on recordings carrying a G.722.1 audio track. |

```json
{
  "channel": 1,
  "start": "2026-08-10T00:00:24+00:00",
  "end": "2026-08-10T00:02:15+00:00",
  "duration": 111,
  "mp4_url": "/api/hikvision_nvr/…/1/clip/1786419624/1786419735.mp4",
  "download_url": "/api/hikvision_nvr/…/1/save/1786419624/1786419735.mp4",
  "rtsp_url": "rtsp://192.168.1.10:554/Streaming/tracks/101/?starttime=20260810T000024Z&endtime=20260810T000215Z"
}
```

`mp4_url` plays; `download_url` saves. They are different endpoints on purpose:
playing reads the NVR's playback RTSP, which starts at the exact second asked
for, while saving pulls whole segments from the NVR's bulk endpoint at network
speed and trims them. Playback wants precision, a download wants throughput.

**Prefer `mp4_url` for playback.** It is a fragmented MP4 remuxed live by ffmpeg: it plays in
a bare `<video>` tag, seeks natively, starts sooner than HLS, and is signable
with `auth/sign_path` (its times are in the path, not the query). `hls_url` is
kept for HLS-only clients, but note that Home Assistant's stream worker crashes
on recordings carrying a G.722.1 audio track — common on these NVRs — so HLS
playback can stall at 0:00 where `mp4_url` works. The MP4 path drops audio.

Seeking = calling this again with a new `start`. Asking for a range that is
already playing returns the same stream instead of starting a second one, which
is what makes scrubbing feel immediate.

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/download`

The recording as an MP4 file, as fast as the network allows.

| Query | Required | Notes |
|---|---|---|
| `start`, `end` | yes | Max span **2 hours**. |

Responds with `Content-Type: video/mp4`, a `Content-Disposition` attachment
filename like `Garage_entrance_20260810_000024.mp4`, and `404` if nothing was
recorded in that range.

The bytes come from `/ISAPI/ContentMgmt/download` rather than the playback
RTSP, which the NVR paces at real time — sixty seconds of video measured **4.5
seconds** against a 60 second floor. ffmpeg remuxes Hikvision's IMKH container
into MP4 without re-encoding, and trims to the range asked for: recordings come
off the device as whole segments, so a one minute request otherwise drags the
twenty minute segment containing it. `-c copy` snaps to a keyframe, so the
result is the requested window give or take a GOP.

### `GET /api/hikvision_nvr/{device_id}/{channel}/save/{start}/{end}.mp4`

The same download, with the times as **Unix seconds in the path** so the URL
can be signed with `auth/sign_path` and handed to an `<a download>` — which is
what the card's save button does. `auth/sign_path` signs an empty parameter
list, so a query-string URL cannot be signed.

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/clip/{start}/{end}.mp4`

The recording as a fragmented MP4, video only, streamed as ffmpeg remuxes it
from the playback RTSP. This is the *playback* path: it begins at the exact
second requested, where `/save` begins at a segment boundary. `start` and `end`
are **Unix seconds in the path** so the URL can be signed and handed straight
to a player. Max 2 hours.

```html
<video src="/api/hikvision_nvr/DS-7608…/1/clip/1786419624/1786419735.mp4?authSig=…"
       autoplay muted controls playsinline></video>
```

---

### `GET /api/hikvision_nvr/{device_id}/events`

The last 200 pushed events, newest last.

| Query | Notes |
|---|---|
| `since` | ISO 8601. Only events after this moment. |

```json
{
  "events": [
    { "type": "VMD", "state": "active", "channel": 4,
      "description": "motion alarm", "timestamp": "2026-08-10T21:28:45+00:00" }
  ],
  "active": [ { "channel": 4, "type": "VMD" } ]
}
```

For real-time push, prefer Home Assistant's WebSocket `subscribe_events` on
`state_changed` for the `binary_sensor.*` entities — same data, no polling.

---

### `POST /api/hikvision_nvr/{device_id}/{channel}/ptz`

Only valid on channels where `ptz` is `true`.

```jsonc
{ "pan": -40, "tilt": 0, "zoom": 0 }   // continuous move, -100…100
{ "preset": 3 }                        // go to preset instead
```

A continuous move runs until a `{"pan":0,"tilt":0,"zoom":0}` stop is sent.
Returns `{"ok": true}`.

---

## 4. Services (for automations and the HA service API)

Callable over `POST /api/services/hikvision_nvr/{service}` as well as from
automations.

### `hikvision_nvr.search_recordings` → returns a response

```yaml
action: hikvision_nvr.search_recordings
data:
  channel: 4
  start_time: "2026-08-10 00:00:00"
  end_time: "2026-08-10 23:59:59"
  event_type: motion
  limit: 50
response_variable: clips
```

### `hikvision_nvr.export_recording` → returns a response

Remuxes a range into `config/media/hikvision_nvr/` as a real MP4.

```yaml
action: hikvision_nvr.export_recording
data:
  channel: 4
  start_time: "2026-08-10 03:14:00"
  end_time: "2026-08-10 03:16:00"
  filename: back_gate_incident.mp4
response_variable: clip
# clip.path, clip.size, clip.media_content_id
```

Max range 2 hours. `filename` must be a bare file name — paths are rejected.

### `hikvision_nvr.ptz`

```yaml
action: hikvision_nvr.ptz
data: { channel: 2, pan: 30, duration: 1.5 }   # or: { channel: 2, preset: 1 }
```

### `hikvision_nvr.reboot`

Needs an admin account on the NVR.

Every service takes an optional `device_id` -- the slug, serial, host, title
or config-entry id; with a single NVR configured it can be omitted.

---

## 5. Mobile app integration recipe

1. **Discover** — `GET /devices`. Cache `id` per NVR and per channel.
2. **Wall** — for each channel, sign `…/{ch}/snapshot` and refresh every 5-10 s.
   Stop refreshing when the view is not visible.
3. **Tap a tile → live** — `GET /{ch}/live`, play `hls_url`. On iOS, `AVPlayer`
   plays it natively; on Android use ExoPlayer's HLS source.
4. **History** — `GET /{ch}/timeline?start=…&end=…` for the scrubber, then
   `GET /{ch}/playback?start=…&end=…` for whatever the user taps.
5. **Save a clip** — open the signed `download_url`, or call
   `export_recording` and fetch the resulting media-source path.
6. **Alerts** — subscribe over the Home Assistant WebSocket API to
   `state_changed` for `binary_sensor.*_motion`, or poll `GET /events?since=…`.

Everything is relative to the Home Assistant base URL, so an app that already
speaks to Home Assistant needs no new host, no new certificate and no new
credential store.
