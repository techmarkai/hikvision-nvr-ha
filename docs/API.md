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
title**, so `…/192.168.1.222/1/snapshot` works too.

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
      "id": "DS-7608NI-K2-8P08201711…WCVU",
      "name": "Home",
      "host": "192.168.1.222",
      "model": "DS-7608NI-K2/8P",
      "firmware": "V4.40.015",
      "mac": "b4:a3:82:xx:xx:xx",
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
     "https://homeassistant.local:8123/api/hikvision_nvr/192.168.1.222/1/snapshot?stream=1"
```

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/live`

Starts (or reuses) a live HLS stream and returns where to play it.

```json
{
  "channel": 1,
  "stream": 1,
  "hls_url": "/api/hls/8f3c…/master_playlist.m3u8",
  "rtsp_url": "rtsp://192.168.1.222:554/Streaming/Channels/101"
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

```json
{
  "channel": 1,
  "start": "2026-08-10T00:00:24+00:00",
  "end": "2026-08-10T00:02:15+00:00",
  "duration": 111,
  "hls_url": "/api/hls/1a2b…/master_playlist.m3u8",
  "download_url": "/api/hikvision_nvr/…/1/download?start=…&end=…",
  "rtsp_url": "rtsp://192.168.1.222:554/Streaming/tracks/101/?starttime=20260810T000024Z&endtime=20260810T000215Z"
}
```

Seeking = calling this again with a new `start`. Asking for a range that is
already playing returns the same stream instead of starting a second one, which
is what makes scrubbing feel immediate.

---

### `GET /api/hikvision_nvr/{device_id}/{channel}/download`

The recording as a file, streamed straight off the NVR — Home Assistant never
buffers it to disk.

| Query | Required | Notes |
|---|---|---|
| `start`, `end` | yes | Max span **2 hours**. |

Responds with `Content-Type: video/mp4` and a `Content-Disposition` filename
like `Garage_entrance_20260810_000024-000215.mp4`.

> The NVR emits its own MP4 muxing here. If a target player rejects it, use the
> `hikvision_nvr.export_recording` service instead — that remuxes through ffmpeg
> and produces a strictly conformant, fast-start MP4.

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

All four take an optional `device_id` (serial, host, or config-entry id); with a
single NVR configured it can be omitted.

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
