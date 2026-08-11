# Security

## Reporting a vulnerability

Report privately through
[GitHub security advisories](https://github.com/techmarkai/hikvision-nvr-ha/security/advisories/new).
Please do not open a public issue for anything exploitable.

## What this integration handles

- **NVR credentials.** Stored by Home Assistant in its config entry, the same
  place every other integration keeps them. They are sent to the device over
  Digest (or Basic, if that is all the firmware accepts) — so put the NVR on a
  trusted network, or enable HTTPS on it and tick **Use SSL** during setup.
- **Home Assistant tokens.** The REST API under `/api/hikvision_nvr/…` uses
  ordinary Home Assistant authentication. Endpoints that a `<video>` or `<img>`
  tag has to reach are given short-lived signed URLs instead, because browsers
  cannot attach an `Authorization` header to those.
- **Diagnostics** are redacted before download: no host, serial, MAC, username
  or password. Check the file anyway before attaching it to an issue.

## When reporting a bug

Strip hosts, serial numbers, MAC addresses and tokens from logs and screenshots.
A Home Assistant long-lived token in a public issue gives away the whole
instance; if one leaks, revoke it under **Profile → Security → Long-lived access
tokens**.
