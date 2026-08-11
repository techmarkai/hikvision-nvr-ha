/**
 * Hikvision NVR card.
 *
 * Live view + history playback in one card. No bundled dependencies: video is
 * played by Home Assistant's own <ha-hls-player>, which already ships hls.js
 * and handles browsers without native HLS.
 *
 * type: custom:hikvision-nvr-card
 */

const CARD_VERSION = "1.4.2";

console.info(
  `%c HIKVISION-NVR-CARD %c ${CARD_VERSION} `,
  "color:#fff;background:#03a9f4;font-weight:700",
  "color:#03a9f4;background:#fff"
);

const API = "hikvision_nvr";
const SNAPSHOT_INTERVAL = 10000;

/** Force Home Assistant to load its camera chunk, which defines ha-hls-player. */
let playerReady = null;
function ensurePlayer() {
  if (!playerReady) {
    playerReady = (async () => {
      if (customElements.get("ha-hls-player")) return;
      const helpers = await window.loadCardHelpers();
      // Creating a camera card pulls in the video-player chunk as a side effect.
      const card = await helpers.createCardElement({
        type: "picture-entity",
        entity: "camera.__hikvision_probe__",
        camera_view: "live",
      });
      card.remove?.();
      await customElements.whenDefined("ha-hls-player");
    })().catch(() => {
      /* fall back to a plain <video> below */
    });
  }
  return playerReady;
}

// Channel names and NVR error strings are device-controlled, so they never go
// into innerHTML raw.
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

/** Home Assistant's callApi rejects with an object, not an Error. */
const errText = (err) => {
  if (!err) return "unknown error";
  if (typeof err === "string") return err;
  const body = err.body?.message || err.body?.error || err.body;
  return (
    err.message ||
    [err.status_code, typeof body === "string" ? body : err.error]
      .filter(Boolean)
      .join(" ") ||
    JSON.stringify(err)
  );
};

const pad = (n) => String(n).padStart(2, "0");
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const hhmmss = (d) => `${hhmm(d)}:${pad(d.getSeconds())}`;
const isoDate = (d) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; display: flex; flex-direction: column; }
  .head {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 14px; border-bottom: 1px solid var(--divider-color);
  }
  .head .title { font-weight: 500; flex: 1; }
  .modes { display: flex; border: 1px solid var(--divider-color); border-radius: 999px; overflow: hidden; }
  .modes button {
    border: 0; background: none; color: var(--secondary-text-color);
    padding: 5px 14px; cursor: pointer; font: inherit; font-size: 13px;
  }
  .modes button[aria-pressed="true"] {
    background: var(--primary-color); color: var(--text-primary-color, #fff);
  }
  .grid { display: grid; gap: 2px; background: var(--divider-color); }
  .tile {
    position: relative; aspect-ratio: 16 / 9; background: #000;
    cursor: pointer; overflow: hidden;
  }
  .tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .tile.offline img { filter: grayscale(1) brightness(0.4); }
  .tile .label {
    position: absolute; inset: auto 0 0 0; padding: 4px 8px;
    font-size: 12px; color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.75));
    display: flex; align-items: center; gap: 6px;
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: #2ecc71; flex: none; }
  .tile.offline .dot { background: var(--error-color, #e74c3c); }
  .tile.active { outline: 2px solid var(--primary-color); outline-offset: -2px; }
  .stage { position: relative; background: #000; aspect-ratio: 16 / 9; }
  .stage > * { width: 100%; height: 100%; display: block; }
  .stage video { object-fit: contain; background: #000; }
  .overlay {
    position: absolute; inset: 0; display: grid; place-items: center;
    color: #fff; font-size: 13px; background: rgba(0,0,0,.5); text-align: center;
    padding: 12px;
  }
  .controls { display: flex; align-items: center; gap: 8px; padding: 8px 12px; flex-wrap: wrap; }
  .controls input[type="date"], .controls select {
    background: var(--card-background-color); color: var(--primary-text-color);
    border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 8px;
    font: inherit; font-size: 13px;
  }
  .controls .spacer { flex: 1; }
  .icon-btn {
    border: 0; background: none; cursor: pointer; color: var(--secondary-text-color);
    padding: 4px 8px; border-radius: 6px; font: inherit; font-size: 13px;
  }
  .icon-btn:hover { background: var(--secondary-background-color); }
  .icon-btn[disabled] { opacity: .4; cursor: default; }
  .timeline {
    position: relative; height: 34px; margin: 0 12px 12px;
    background: var(--secondary-background-color); border-radius: 6px;
    cursor: crosshair; overflow: hidden; touch-action: none;
  }
  .timeline .block { position: absolute; top: 0; bottom: 0; background: var(--primary-color); opacity: .75; }
  .timeline .block.motion { background: var(--warning-color, #ff9800); }
  .timeline .cursor { position: absolute; top: 0; bottom: 0; width: 2px; background: #fff; box-shadow: 0 0 3px #000; }
  .timeline .hours { position: absolute; inset: 0; pointer-events: none; }
  .timeline .hours span {
    position: absolute; top: 2px; font-size: 9px; color: var(--secondary-text-color);
    transform: translateX(-50%);
  }
  .timeline .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--divider-color); }
  .empty { padding: 24px; text-align: center; color: var(--secondary-text-color); font-size: 13px; }
  .err { padding: 12px; color: var(--error-color); font-size: 13px; }
`;

class HikvisionNvrCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("hikvision-nvr-card-editor");
  }

  static getStubConfig() {
    return { type: "custom:hikvision-nvr-card", columns: 3 };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._mode = "live";
    this._device = null;
    this._channels = [];
    this._selected = null;
    this._date = isoDate(new Date());
    this._blocks = [];
    this._snapshotUrls = new Map();
    this._timers = [];
    this._error = null;
    this._loading = true;
  }

  setConfig(config) {
    this._config = { columns: 3, default_mode: "live", live_stream: 1, ...config };
    this._mode = this._config.default_mode === "playback" ? "playback" : "live";
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
  }

  connectedCallback() {
    if (this._hass && !this._loading) this._startSnapshots();
    document.addEventListener("visibilitychange", this._onVisibility);
  }

  disconnectedCallback() {
    this._stopTimers();
    document.removeEventListener("visibilitychange", this._onVisibility);
  }

  _onVisibility = () => {
    if (document.hidden) this._stopTimers();
    else this._startSnapshots();
  };

  _stopTimers() {
    this._timers.forEach(clearInterval);
    this._timers = [];
  }

  // ------------------------------------------------------------------ data

  async _api(path, options) {
    return this._hass.callApi(options?.method || "GET", `${API}/${path}`, options?.body);
  }

  async _init() {
    try {
      const { devices } = await this._api("devices");
      if (!devices.length) throw new Error("No NVR is configured");
      const device =
        devices.find(
          (d) => d.id === this._config.device || d.host === this._config.device
        ) || devices[0];
      this._device = device;

      let channels = device.channels;
      if (this._config.channels?.length) {
        const wanted = this._config.channels.map(Number);
        channels = channels.filter((c) => wanted.includes(c.id));
      }
      this._channels = channels;
      this._selected = channels[0]?.id ?? null;
      await Promise.all(channels.map((c) => this._refreshSnapshot(c.id)));
      this._loading = false;
      this._render();
      this._startSnapshots();
      if (this._mode === "playback") this._loadTimeline();
    } catch (err) {
      this._error = errText(err);
      this._loading = false;
      this._render();
    }
  }

  /** Signed URL so <img> can load it without an Authorization header. */
  async _refreshSnapshot(channel) {
    try {
      const { path } = await this._hass.callWS({
        type: "auth/sign_path",
        path: `/api/${API}/${this._device.id}/${channel}/snapshot`,
        expires: 300,
      });
      // No cache-buster: auth/sign_path signs an EMPTY query-parameter list,
      // so any extra query arg invalidates the signature and Home Assistant
      // logs it as a failed login (and eventually IP-bans the browser). Each
      // signed path is unique anyway -- the JWT carries its own issued-at.
      this._snapshotUrls.set(channel, path);
      const img = this.shadowRoot.querySelector(`img[data-channel="${channel}"]`);
      if (img) img.src = this._snapshotUrls.get(channel);
    } catch (err) {
      /* a single failed thumbnail must not break the card */
    }
  }

  _startSnapshots() {
    this._stopTimers();
    if (!this._device || document.hidden) return;
    this._timers.push(
      setInterval(() => {
        // Only the tiles that are actually on screen.
        for (const channel of this._channels) {
          if (this._mode === "live" && channel.id === this._selected) continue;
          this._refreshSnapshot(channel.id);
        }
      }, SNAPSHOT_INTERVAL)
    );
  }

  async _loadTimeline() {
    const [y, m, d] = this._date.split("-").map(Number);
    const start = new Date(y, m - 1, d, 0, 0, 0);
    const end = new Date(y, m - 1, d, 23, 59, 59);
    this._dayStart = start;
    this._dayEnd = end;
    this._blocks = [];
    this._render();
    try {
      const data = await this._api(
        `${this._device.id}/${this._selected}/timeline` +
          `?start=${encodeURIComponent(start.toISOString())}` +
          `&end=${encodeURIComponent(end.toISOString())}&gap=120`
      );
      this._blocks = data.blocks.map((b) => ({
        start: new Date(b.start),
        end: new Date(b.end),
        motion: b.event_types.includes("motion"),
      }));
      this._error = null;
    } catch (err) {
      this._error = `Timeline failed: ${errText(err)}`;
    }
    this._render();
  }

  async _playFrom(when) {
    // Clamp to the block the user clicked, so we never ask the NVR for a range
    // that is mostly empty -- that is what makes seeking feel instant.
    const block =
      this._blocks.find((b) => when >= b.start && when <= b.end) ||
      this._blocks.find((b) => b.end > when);
    if (!block) return;
    const from = when > block.start ? when : block.start;
    const to = new Date(Math.min(block.end.getTime(), from.getTime() + 3600e3));
    this._cursor = from;
    this._render();
    try {
      const data = await this._api(
        `${this._device.id}/${this._selected}/playback` +
          `?start=${encodeURIComponent(from.toISOString())}` +
          `&end=${encodeURIComponent(to.toISOString())}`
      );
      this._playbackUrl = data.hls_url;
      this._downloadUrl = data.download_url;
      this._error = null;
    } catch (err) {
      this._error = `Playback failed: ${errText(err)}`;
      this._playbackUrl = null;
    }
    this._render();
  }

  async _liveUrl() {
    const stream = this._config.live_stream || 1;
    const data = await this._api(
      `${this._device.id}/${this._selected}/live?stream=${stream}`
    );
    return data.hls_url;
  }

  async _download() {
    if (!this._downloadUrl) return;
    const { path } = await this._hass.callWS({
      type: "auth/sign_path",
      path: this._downloadUrl,
      expires: 600,
    });
    window.open(path, "_blank");
  }

  // ---------------------------------------------------------------- render

  _render() {
    const root = this.shadowRoot;
    if (!root.firstChild) {
      root.innerHTML = `<style>${STYLE}</style><ha-card></ha-card>`;
    }
    const card = root.querySelector("ha-card");

    if (this._loading) {
      card.innerHTML = `<div class="empty">Loading NVR…</div>`;
      return;
    }
    if (!this._device) {
      card.innerHTML = `<div class="err">${esc(this._error || "No NVR available")}</div>`;
      return;
    }

    const channel = this._channels.find((c) => c.id === this._selected);
    card.innerHTML = `
      <div class="head">
        <div class="title">${esc(channel ? channel.name : this._device.name)}</div>
        <div class="modes">
          <button data-mode="live" aria-pressed="${this._mode === "live"}">Live</button>
          <button data-mode="playback" aria-pressed="${this._mode === "playback"}">History</button>
        </div>
      </div>
      <div class="stage" id="stage"></div>
      ${this._mode === "playback" ? this._playbackControls() : ""}
      ${this._error ? `<div class="err">${esc(this._error)}</div>` : ""}
      <div class="grid" style="grid-template-columns:repeat(${this._config.columns},1fr)">
        ${this._channels
          .map(
            (c) => `
          <div class="tile ${c.online ? "" : "offline"} ${
              c.id === this._selected ? "active" : ""
            }" data-channel="${c.id}">
            <img data-channel="${c.id}" src="${esc(this._snapshotUrls.get(c.id) || "")}" alt="${esc(c.name)}">
            <div class="label"><span class="dot"></span>${esc(c.name)}</div>
          </div>`
          )
          .join("")}
      </div>
    `;

    card.querySelectorAll(".modes button").forEach((button) =>
      button.addEventListener("click", () => {
        this._mode = button.dataset.mode;
        this._playbackUrl = null;
        this._render();
        if (this._mode === "playback") this._loadTimeline();
      })
    );
    card.querySelectorAll(".tile").forEach((tile) =>
      tile.addEventListener("click", () => {
        this._selected = Number(tile.dataset.channel);
        this._playbackUrl = null;
        this._render();
        if (this._mode === "playback") this._loadTimeline();
      })
    );

    if (this._mode === "playback") this._wirePlaybackControls(card);
    this._renderStage(card.querySelector("#stage"));
  }

  _playbackControls() {
    const width = (block) => {
      const span = this._dayEnd - this._dayStart;
      return {
        left: ((block.start - this._dayStart) / span) * 100,
        width: Math.max(((block.end - block.start) / span) * 100, 0.3),
      };
    };
    const blocks = this._blocks
      .map((block) => {
        const { left, width: w } = width(block);
        return `<div class="block ${block.motion ? "motion" : ""}" style="left:${left}%;width:${w}%"></div>`;
      })
      .join("");
    const ticks = Array.from({ length: 24 }, (_, hour) => {
      const left = (hour / 24) * 100;
      return (
        `<div class="tick" style="left:${left}%"></div>` +
        (hour % 6 === 0 ? `<span style="left:${left}%">${pad(hour)}</span>` : "")
      );
    }).join("");
    const cursor =
      this._cursor && this._cursor >= this._dayStart && this._cursor <= this._dayEnd
        ? `<div class="cursor" style="left:${
            ((this._cursor - this._dayStart) / (this._dayEnd - this._dayStart)) * 100
          }%"></div>`
        : "";

    return `
      <div class="controls">
        <input type="date" id="date" value="${this._date}" max="${isoDate(new Date())}">
        <button class="icon-btn" id="prev">‹ Prev</button>
        <button class="icon-btn" id="next">Next ›</button>
        <span class="spacer"></span>
        <span style="font-size:12px;color:var(--secondary-text-color)">
          ${this._cursor ? hhmmss(this._cursor) : `${this._blocks.length} recorded blocks`}
        </span>
        <button class="icon-btn" id="download" ${this._downloadUrl ? "" : "disabled"}>Download</button>
      </div>
      <div class="timeline" id="timeline">
        <div class="hours">${ticks}</div>
        ${blocks}${cursor}
      </div>
    `;
  }

  _wirePlaybackControls(card) {
    card.querySelector("#date").addEventListener("change", (event) => {
      this._date = event.target.value;
      this._cursor = null;
      this._playbackUrl = null;
      this._loadTimeline();
    });

    const seek = (event) => {
      const timeline = card.querySelector("#timeline");
      const rect = timeline.getBoundingClientRect();
      const ratio = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
      this._playFrom(
        new Date(this._dayStart.getTime() + ratio * (this._dayEnd - this._dayStart))
      );
    };
    card.querySelector("#timeline").addEventListener("click", seek);

    const step = (direction) => {
      if (!this._blocks.length) return;
      const now = this._cursor || this._blocks[0].start;
      const next =
        direction > 0
          ? this._blocks.find((b) => b.start > now)
          : [...this._blocks].reverse().find((b) => b.start < now);
      if (next) this._playFrom(next.start);
    };
    card.querySelector("#prev").addEventListener("click", () => step(-1));
    card.querySelector("#next").addEventListener("click", () => step(1));
    card.querySelector("#download").addEventListener("click", () => this._download());
  }

  async _renderStage(stage) {
    if (!stage || !this._selected) return;
    const url =
      this._mode === "live"
        ? await this._liveUrl().catch((err) => {
            this._error = `Live view failed: ${errText(err)}`;
            return null;
          })
        : this._playbackUrl;

    if (!url) {
      // An error here used to sit behind "Starting stream…" forever, because
      // only the stage re-renders -- show it where it happened.
      stage.innerHTML = `<div class="overlay">${
        this._error
          ? esc(this._error)
          : this._mode === "live"
            ? "Starting stream…"
            : "Pick a moment on the timeline to play"
      }</div>`;
      return;
    }
    if (stage.dataset.url === url) return;
    stage.dataset.url = url;

    // ha-hls-player only absolutises the URL when driven by `entityid`; given a
    // `url` it uses it verbatim and later does new URL(segment, url), which
    // throws "Invalid base URL" on a relative path.
    const absolute = this._hass.hassUrl
      ? this._hass.hassUrl(url)
      : new URL(url, window.location.origin).href;

    await ensurePlayer();
    stage.innerHTML = "";
    if (customElements.get("ha-hls-player")) {
      const player = document.createElement("ha-hls-player");
      player.hass = this._hass;
      player.url = absolute;
      player.autoPlay = true;
      player.controls = this._mode === "playback";
      player.muted = true;
      player.playsInline = true;
      stage.appendChild(player);
    } else {
      // Native HLS (Safari / iOS) when the HA player chunk is unavailable.
      const video = document.createElement("video");
      video.src = absolute;
      video.autoplay = true;
      video.muted = true;
      video.playsInline = true;
      video.controls = true;
      stage.appendChild(video);
    }
  }
}

/**
 * Visual editor. Everything is a dropdown, driven by <ha-form> so the widgets
 * are Home Assistant's own and stay consistent with the rest of the UI.
 * The NVR and camera lists are fetched live from the API, so there is nothing
 * to type and nothing to remember.
 */
class HikvisionNvrCardEditor extends HTMLElement {
  constructor() {
    super();
    this._devices = [];
  }

  setConfig(config) {
    this._config = { columns: 3, default_mode: "live", live_stream: 1, ...config };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    else this._render();
  }

  async _load() {
    try {
      const { devices } = await this._hass.callApi("GET", "hikvision_nvr/devices");
      this._devices = devices;
    } catch (err) {
      this._error = errText(err);
    }
    this._render();
  }

  get _device() {
    return (
      this._devices.find(
        (d) => d.id === this._config?.device || d.host === this._config?.device
      ) || this._devices[0]
    );
  }

  _schema() {
    const device = this._device;
    const channels = device?.channels || [];
    return [
      {
        name: "device",
        selector: {
          select: {
            mode: "dropdown",
            options: this._devices.map((d) => ({
              value: d.id,
              label: `${d.name} (${d.model || d.host})`,
            })),
          },
        },
      },
      {
        name: "channels",
        selector: {
          select: {
            multiple: true,
            mode: "list",
            options: channels.map((c) => ({
              value: String(c.id),
              label: `${c.id} · ${c.name}${c.online ? "" : " (offline)"}`,
            })),
          },
        },
      },
      {
        name: "default_mode",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "live", label: "Live view" },
              { value: "playback", label: "History" },
            ],
          },
        },
      },
      {
        name: "live_stream",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: 1, label: "Main stream — full resolution" },
              { value: 2, label: "Sub stream — lighter, starts faster" },
            ],
          },
        },
      },
      {
        name: "columns",
        selector: { number: { min: 1, max: 6, mode: "slider" } },
      },
    ];
  }

  _labels(schema) {
    return {
      device: "NVR",
      channels: "Cameras to show",
      default_mode: "Opens on",
      live_stream: "Live quality",
      columns: "Thumbnails per row",
    }[schema.name];
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.querySelector("ha-form")) {
      this.innerHTML = `<div class="hik-editor" style="padding:8px 0"></div>`;
      const form = document.createElement("ha-form");
      form.addEventListener("value-changed", (event) => {
        const config = { ...this._config, ...event.detail.value };
        // Selecting a different NVR invalidates the camera picks.
        if (config.device !== this._config.device) delete config.channels;
        this._config = config;
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: { type: "custom:hikvision-nvr-card", ...config } },
            bubbles: true,
            composed: true,
          })
        );
        this._render();
      });
      this.querySelector(".hik-editor").appendChild(form);
    }

    if (this._error) {
      this.querySelector(".hik-editor").insertAdjacentHTML(
        "afterbegin",
        `<div style="color:var(--error-color);font-size:13px;padding:8px">${esc(
          this._error
        )}</div>`
      );
      this._error = null;
    }

    const device = this._device;
    const form = this.querySelector("ha-form");
    form.hass = this._hass;
    form.schema = this._schema();
    form.computeLabel = (schema) => this._labels(schema);
    form.data = {
      ...this._config,
      device: device?.id ?? this._config.device,
      // Nothing selected means "all cameras" -- show that as all ticked.
      channels: (
        this._config.channels || (device?.channels || []).map((c) => c.id)
      ).map(String),
    };
  }
}

// The panel imports this module under a different URL than the global
// add_extra_js_url load, so it can execute twice. Defining twice throws.
if (!customElements.get("hikvision-nvr-card")) {
  customElements.define("hikvision-nvr-card", HikvisionNvrCard);
  customElements.define("hikvision-nvr-card-editor", HikvisionNvrCardEditor);
  // Alias: `type: custom:hikvision-card` resolves to the same card, so the
  // shorter name people reach for first does not fail.
  customElements.define("hikvision-card", class extends HikvisionNvrCard {});
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "hikvision-nvr-card"))
  window.customCards.push({
  type: "hikvision-nvr-card",
  name: "Hikvision",
  description: "Live view and history playback for a Hikvision NVR",
  preview: true,
  documentationURL: "https://github.com/techmarkai/hikvision-nvr-ha",
});
