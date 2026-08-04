# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A toolset for downloading EU **digital tachograph driver card** data over a PC/SC smart card
reader, producing a standard `.ddd` download file, and parsing it into a human-readable activity
report (vehicles used, trips, distances). The primary frontend is **`tacho_service`** — a
tray-resident background service that detects card insertion, downloads, previews the report, and
delivers it to any number of configured HTTP endpoints.

## Commands

```bash
# Setup (Arch/CachyOS)
sudo pacman -S pcsclite ccid python-pyscard python-pyqt6
sudo systemctl enable --now pcscd.socket
./install.sh                          # deps check, systemd --user unit, starter config

# Preflight / diagnostics -- run this FIRST when anything misbehaves
python3 scripts/tacho-check.py                 # environment only
python3 scripts/tacho-check.py --card          # + live card read test
python3 scripts/tacho-check.py --card --show-identity   # reveals PII; don't paste publicly

# The service
python3 -m tacho_service                       # run in the foreground
systemctl --user start|stop|status tacho
journalctl --user -u tacho -f

# Report only, from an existing file (no reader needed)
python3 tacho_report.py <file.ddd>
python3 inspect_ddd.py <file.ddd>              # raw block structure
```

No linter or CI. Verification is `scripts/tacho-check.py` plus manual runs against a real reader.

## Architecture

Two packages, one clear boundary:

- **`tacho_core/`** — all card and format logic, no UI, no I/O beyond the card. This is the
  single source of truth; it replaced five copy-pasted implementations.
  - `reader.py` `TachoReader` — the APDU sequence. Selects the Tachograph DF by name
    (`FF 54 41 43 48 4F`), then `SELECT`/`READ BINARY` each EF, with `PERFORM HASH` +
    `COMPUTE SIGNATURE` on signed files. Reports **byte-level** progress.
  - `ddd.py` — the `.ddd` container: `pack_block` / `read_files`. Writer and reader must agree.
  - `parser.py` `DDDParser` — takes **bytes** (`from_file()` for a path).
  - `codec.py` — timestamps (4-byte BE Unix seconds, UTC) and codepage-prefixed text.
  - `report.py` `build_report(parser, window_days)` — `None` means all data.
- **`tacho_service/`** — the tray service. `app.py` orchestrates; `cardwatch.py` (PC/SC events),
  `outbox.py` (SQLite delivery queue), `dispatch.py` (HTTP worker), `config.py`, `ui/`.

### The `.ddd` container format

A flat concatenation of blocks: `[file_id: 2][rec_type: 1][length: 2][data]`. `rec_type 0` is file
content, `rec_type 1` its signature. Only `rec_type 0` is parsed back. Defined once in
`tacho_core/ddd.py` — change both sides together.

### Threading

Four threads, one hard rule: **only the Qt main thread touches widgets.**

| Thread | Role |
|---|---|
| Qt main | tray, window, settings |
| pyscard observer | card events only — never card I/O, or it delays the next event |
| download worker | the APDU sequence, spawned per insertion |
| dispatcher | drains the outbox over HTTP |

Worker → UI communication is always via `pyqtSignal`.

### Delivery

One download fans out to **one outbox row per enabled destination**, each retried independently.
The payload is **frozen at enqueue** — retries POST the exact bytes built when the card was read,
so a settings change cannot alter what a pending item sends. Backoff 5s→15s→60s→5m→15m→30m ceiling
until a 24h deadline, then terminal `failed`. 5xx/408/429 retry; other 4xx fail immediately.

### Android

`android/` is a separate native Kotlin implementation (`UsbCcidTransport`, `TachoReader`,
`TachoParser`) mirroring the same APDU sequence and DDD parsing by hand. **It does not pick up
`tacho_core` changes** — a protocol, file ID, record offset, or container fix must be applied in
Python *and* Kotlin.

## Conventions & gotchas

- **Never commit `.ddd` files.** They contain driver names, card numbers, vehicle registrations
  and movement history — personal data under GDPR. `.gitignore` excludes `*.ddd` and `downloads/`.
  This repo is public.
- **No private endpoint hostnames in source.** Destinations live only in
  `~/.config/tacho/config.json` (mode 0600, contains auth tokens). `Config.kt` ships blank.
- Downloads go to `~/.local/share/tacho/downloads`, retention-capped (default 20).
- Card timestamps are 4-byte BE Unix seconds, decoded as UTC.
- Text fields are codepage-prefixed: first byte selects `iso-8859-<n>`.
- `report_period_days` is the **observed span of returned trips**, not the requested window.
  Preserved deliberately — downstream workflows already read it that way.
- A full card is ~21KB over ~110 `READ BINARY` round trips, and the activity + vehicles EFs are
  ~95% of it. Progress must be byte-driven; a per-file step counter appears frozen.
- `cardpeek_download.sh` + `cardpeek_ddd_to_xml.lua` are an unrelated alternative path.

## Still duplicated

`tacho.py`, `tacho_download.py` and `tacho_report.py` predate `tacho_core` and still carry their
own copies of the reader/parser. Port them to `tacho_core` rather than editing their copies.
