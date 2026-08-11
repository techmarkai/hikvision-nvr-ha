# Contributing

## The one structural rule

`custom_components/hikvision_nvr/isapi.py` **must not import Home Assistant.**
It is a plain aiohttp ISAPI client, which is why it can be run and debugged
against a real NVR with no Home Assistant anywhere near it. Anything
device-specific belongs there; anything Home Assistant-specific belongs in the
layers above it.

## Before you open a pull request

```bash
pip install -r requirements-dev.txt
ruff check custom_components tests
python -m compileall -q custom_components tests
python tests/test_services.py
python tests/test_translations.py
node --check custom_components/hikvision_nvr/frontend/hikvision-nvr-card.js
```

CI runs exactly these, plus `hassfest` and the HACS validator.

If you have hardware, also run the end-to-end check. It asserts, so it fails
loudly:

```bash
python tests/live_check.py <nvr-ip> <username> '<password>'
```

## Changes that touch the device

Firmwares disagree about which ISAPI endpoints exist and what they return, so:

- **Detect, do not assume.** Ask the device what it supports and create only
  what can actually fire. `/ISAPI/Event/triggers` is the device's own
  declaration and is cheaper than probing.
- **Degrade, do not crash.** A missing endpoint is normal. `403 notSupport` is
  an answer, not an error.
- **Say what you measured.** "Playback starts faster" is not reviewable;
  "DESCRIBE on a playback URL takes 2.1-2.9 s against 92 ms live, measured on
  V4.40.015" is.

## Performance claims

Live view and playback latency are the point of this integration, so a change
that claims to improve them needs before/after numbers from real hardware in the
pull request. A plausible-sounding optimisation that was never measured has been
reverted here before.

## Frontend

The card is a single dependency-free file that talks to the REST API in
`docs/API.md`. No build step, no bundler — edit it and reload. Home Assistant
caches it by the manifest version, so bump `manifest.json` when you change it or
users will keep the old one.

## Style

Match what is already there. Comments explain *why* something is the way it is,
particularly where a device quirk forced it — those are the comments that save
the next person a day.
