/**
 * Hikvision NVR sidebar panel.
 *
 * A full-page shell around <hikvision-nvr-card>: pick an NVR, add one, or jump
 * to its settings. The card itself does all the video work -- this is only the
 * page around it.
 */

import "./hikvision-nvr-card.js";

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

class HikvisionNvrPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._devices = [];
    this._device = null;
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._built = true;
      this._build();
      this._load();
    }
    const card = this.shadowRoot.querySelector("hikvision-nvr-card");
    if (card) card.hass = hass;
  }

  set narrow(value) {
    this._narrow = value;
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block; height: 100%;
          background: var(--primary-background-color);
          color: var(--primary-text-color);
        }
        .toolbar {
          display: flex; align-items: center; gap: 12px;
          height: 56px; padding: 0 16px;
          background: var(--app-header-background-color, var(--primary-color));
          color: var(--app-header-text-color, #fff);
          box-sizing: border-box;
        }
        .toolbar .title { font-size: 20px; font-weight: 400; flex: 1; }
        select {
          background: rgba(255,255,255,.15); color: inherit; font: inherit;
          border: 0; border-radius: 6px; padding: 6px 10px; cursor: pointer;
        }
        select option { color: #000; }
        button {
          background: rgba(255,255,255,.15); color: inherit; font: inherit;
          border: 0; border-radius: 6px; padding: 6px 12px; cursor: pointer;
        }
        button:hover { background: rgba(255,255,255,.28); }
        .body { padding: 12px; max-width: 1400px; margin: 0 auto; }
        .empty {
          margin: 48px auto; max-width: 460px; text-align: center;
          background: var(--card-background-color); border-radius: 12px;
          padding: 32px 24px; box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.15));
        }
        .empty h2 { margin: 0 0 8px; font-size: 20px; font-weight: 500; }
        .empty p { margin: 0 0 20px; color: var(--secondary-text-color); font-size: 14px; }
        .empty button {
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          padding: 10px 20px; font-size: 14px;
        }
        ha-icon-button { --mdc-icon-button-size: 40px; }
      </style>
      <div class="toolbar">
        <div class="title">Cameras</div>
        <select id="picker" hidden></select>
        <button id="add">Add NVR</button>
        <button id="settings" hidden>Settings</button>
      </div>
      <div class="body" id="body"></div>
    `;

    // Deep links into the Home Assistant config UI -- the config flow dialog
    // opens on top of this page, so adding an NVR never leaves the panel.
    this.shadowRoot.getElementById("add").addEventListener("click", () =>
      this._navigate("/config/integrations/dashboard/add?domain=hikvision_nvr")
    );
    this.shadowRoot.getElementById("settings").addEventListener("click", () =>
      this._navigate("/config/integrations/integration/hikvision_nvr")
    );
    this.shadowRoot.getElementById("picker").addEventListener("change", (event) => {
      this._device = event.target.value;
      this._renderCard();
    });
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
  }

  async _load() {
    const body = this.shadowRoot.getElementById("body");
    try {
      const { devices } = await this._hass.callApi("GET", "hikvision_nvr/devices");
      this._devices = devices;
    } catch (err) {
      body.innerHTML = `<div class="empty"><h2>Could not reach the API</h2><p>${esc(
        err.message || err
      )}</p></div>`;
      return;
    }

    if (!this._devices.length) {
      this.shadowRoot.getElementById("add").hidden = false;
      body.innerHTML = `
        <div class="empty">
          <h2>No NVR yet</h2>
          <p>Add your Hikvision NVR with its IP address, username and password.
             Every channel becomes a camera automatically.</p>
          <button id="add2">Add your NVR</button>
        </div>`;
      body.querySelector("#add2").addEventListener("click", () =>
        this._navigate("/config/integrations/dashboard/add?domain=hikvision_nvr")
      );
      return;
    }

    this._device = this._devices[0].id;
    const picker = this.shadowRoot.getElementById("picker");
    picker.hidden = this._devices.length < 2;
    picker.innerHTML = this._devices
      .map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`)
      .join("");
    this.shadowRoot.getElementById("settings").hidden = false;
    this._renderCard();
  }

  _renderCard() {
    const body = this.shadowRoot.getElementById("body");
    body.innerHTML = "";
    const card = document.createElement("hikvision-nvr-card");
    card.setConfig({
      type: "custom:hikvision-nvr-card",
      device: this._device,
      columns: this._narrow ? 2 : 4,
    });
    card.hass = this._hass;
    body.appendChild(card);
  }
}

customElements.define("hikvision-nvr-panel", HikvisionNvrPanel);
