# Plan — Tacho Card Reader in the Browser (WebUSB) + PWA Push, inside BCA-BTT

> **Implementation home:** this feature ships inside the BCA-BTT project. The canonical, BCA-relative
> copy of this plan (with the `ops.lan` connection steps + Android APK versioning) lives at
> `BCA-BTT/Moduals/architecture/tacho-webusb-plan.md`. This file is the tacho-app-side mirror —
> it documents the card-reading logic being ported (CCID/APDU/DDD) from `tacho/`.


**Goal.** Read EU digital tachograph driver cards **directly from a web browser** (no installed
desktop app) using a USB CCID reader via the **WebUSB API**, feed the parsed data into the
existing **BCA-BTT** dashboard's `tacho_daily` table, and deliver **native push notifications**
to installed clients via a **PWA + Web Push**. Ship an **Android APK** that loads the same web UI
in a WebView but uses native **USB OTG** for card reading (since Android WebView has no WebUSB).

**Decisions locked in (2026-06-19):**
1. **Feature lives inside the BCA-BTT web UI** (new tacho icon/widget), not a standalone app.
2. **Push = native Web Push (PWA only)** — service worker + VAPID, no n8n dependency.
3. **`ops.lan` already serves the BCA-BTT web UI on Unraid — keep it.** APK uses plain
   `http://ops.lan` (native USB, no cert). Desktop WebUSB needs a secure context on `ops.lan` only —
   Chrome flag (`unsafely-treat-insecure-origin-as-secure`) or a mkcert cert via arr-proxy nginx.
   No Tailscale, no public domain. (See `BCA-BTT/Moduals/architecture/tacho-webusb-plan.md` §6.)

---

## 1. Why this works — the core insight

The Android app already speaks raw **CCID** over USB bulk endpoints in
`tacho/android/.../UsbCcidTransport.kt`: build a 10-byte CCID header (`messageType`, LE length,
slot, seq, params), `bulkTransfer` OUT, `bulkTransfer` IN, handle the `0x80` "time-extension"
status, return the data block. On top of that sits the APDU sequence in `TachoReader.kt` and the
`.ddd` parse in `TachoParser.kt`.

**Every one of those USB primitives has a WebUSB equivalent**, so the whole stack can run in JS:

| CCID need | Kotlin (USB Host) | WebUSB (browser JS) |
|---|---|---|
| Open device | `usbManager.openDevice` | `await device.open()` |
| Select config | implicit | `await device.selectConfiguration(1)` |
| Claim CCID interface | `claimInterface(iface, true)` | `await device.claimInterface(ifaceNum)` |
| Bulk OUT (send CCID block) | `bulkTransfer(epOut, …)` | `await device.transferOut(epOutNum, data)` |
| Bulk IN (read response) | `bulkTransfer(epIn, …)` | `await device.transferIn(epInNum, len)` |
| Find CCID iface (class `0x0B`) | scan `device.getInterface` | scan `device.configuration.interfaces` |

So the **APDU + DDD logic becomes shared, transport-agnostic JavaScript**, with a single
`transport.transmit(apdu) → response` contract. Two transports implement it:

- **WebUSB transport** — desktop Chrome/Edge + Android Chrome. Pure JS, no install.
- **Native-bridge transport** — inside the Android APK's WebView; JS calls
  `window.AndroidTacho.transmit(hex)`, native Kotlin reuses the **existing** `UsbCcidTransport`.

This is the same "one core, two transports" pattern the project already uses — except now the
core lives once in JS instead of being duplicated across Python + Kotlin.

---

## 2. Hard constraints (read before building)

These are non-negotiable browser/OS facts that shape the whole design:

1. **WebUSB requires a secure context.** Only `https://…` or `http://localhost`/`127.0.0.1`.
   Plain `http://ops.lan` **will not expose `navigator.usb`** in a normal desktop tab.
   → Make `ops.lan` a secure context on the desktop (Chrome flag or mkcert, decision #3). The
   **APK is unaffected** (native USB). Page + API same-origin → no
   mixed-content blocking.
2. **Web Push also requires HTTPS + a Service Worker** (same secure-context rule). The HTTPS work
   does double duty for WebUSB and Push.
3. **Browser support for WebUSB:** Chrome/Edge/Opera (desktop), Chrome (Android). **Not** Firefox,
   **not** Safari, **not** iOS Safari. iOS cannot do USB at all — out of scope.
4. **Android WebView has no WebUSB.** The APK must bridge USB OTG natively (§6).
5. **Linux: `pcscd` claims the CCID interface.** While the PC/SC daemon holds the reader, WebUSB's
   `claimInterface()` fails with a device-busy error. Mitigations (§7): stop/disable `pcscd`, or a
   udev rule, or unbind. The current `pcscd`-based desktop scripts and the new WebUSB path are
   **mutually exclusive per reader session.**
6. **Windows: CCID readers bind to the OS smartcard (WinUSB needed).** WebUSB can only claim an
   interface bound to WinUSB; users may need Zadig or a matching driver. Document, don't solve now.
7. **`requestDevice()` needs a user gesture** and shows the browser's device chooser; permission is
   remembered per-origin per-device. First use is always an explicit click + pick.

> Net: the single biggest enabler **and** risk is HTTPS-on-LAN + the `pcscd` conflict. Both are
> solved in §7 and must be validated in a spike **before** building UI.

---

## 3. Target architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  BROWSER (desktop: ops.lan as secure context) / APK WEBVIEW (http://ops.lan) │
│                                                                        │
│  BCA-BTT web UI (existing vanilla-JS SPA)                              │
│   ├─ week board + existing tacho widget (unchanged)                   │
│   └─ NEW  🎴 "Read Card" icon → tacho-reader panel                     │
│                                                                        │
│  tacho-core.js   (NEW — shared, transport-agnostic)                    │
│   ├─ APDU sequence (port of TachoReader)                              │
│   ├─ .ddd builder + DDDParser (port of tacho.py / TachoParser.kt)     │
│   └─ transport contract:  transmit(apdu) → bytes,  powerOn()           │
│        ├─ WebUSBTransport      (Chrome desktop / Chrome Android)       │
│        └─ NativeBridgeTransport(window.AndroidTacho.*)  ← APK only     │
│                                                                        │
│  sw.js  (NEW — service worker: PWA install + Web Push receiver)        │
└─────────────┬──────────────────────────────────────────────┬─────────┘
              │ POST parsed report (same-origin HTTPS)         │ push subscribe
              ▼                                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  BCA-BTT  FastAPI  (Moduals/webui/app.py, :8099 behind HTTPS proxy)    │
│   NEW  POST /api/tacho-reader/ingest   → validate → upsert tacho_daily │
│   NEW  POST /api/push/subscribe        → store PushSubscription        │
│   NEW  GET  /api/push/vapid-public-key                                 │
│   NEW  push helper: on ingest, send Web Push (pywebpush + VAPID)       │
│        → existing GET /api/week/tacho + tacho widget auto-reflect data │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼  Postgres host:5432  →  tacho_daily (existing table)
```

**Data already flows the right way:** the existing widget renders from `tacho_daily` via
`GET /api/week/tacho`. If `/ingest` writes the same rows, **the widget updates with zero frontend
changes** to the board.

---

## 4. Component inventory (what to build / reuse)

### New — shared frontend core (`Moduals/webui/static/tacho/`)
- `ccid.js` — WebUSB CCID transport: open/claim, `sendCommand(type,payload)`, header build, seq
  counter, `0x80` retry loop. Direct port of `UsbCcidTransport.kt`.
- `tacho-core.js` — APDU file-read sequence + `.ddd` assembly + `DDDParser` (identity `0x0520`,
  vehicles `0x0505`, activity ring buffer `0x0504`). Port of `tacho.py` / `TachoParser.kt`.
- `transport.js` — picks `WebUSBTransport` if `navigator.usb`, else `NativeBridgeTransport` if
  `window.AndroidTacho`, else "unsupported" state.
- `reader-ui.js` — the panel: connect button, progress bar (reuse existing progress styling),
  driver card, result summary, "Send to dashboard" → `POST /api/tacho-reader/ingest`.

### New — PWA shell
- `manifest.webmanifest` — name, icons, `display: standalone`, start URL, theme color.
- `sw.js` — service worker: cache app shell (offline open), `push` event → `showNotification`,
  `notificationclick` → focus/open week board.
- `push.js` — request notification permission, subscribe via `PushManager`, POST subscription.

### New — BCA-BTT backend (`Moduals/webui/app.py` + a small module)
- `POST /api/tacho-reader/ingest` — body = parsed `DriverReport` (driver, card, country, expiry,
  per-day trips with regs/times/mileage). Validate, map to `tacho_daily` rows, **upsert** keyed on
  `(trip_date, vehicle_registration, driver/card)`. Reuse `db.py` pool.
- `Moduals/tacho_reader_module/` — `schema.py` (pydantic models), `ingest.py` (report→rows upsert),
  `push.py` (VAPID send via `pywebpush`).
- `POST /api/push/subscribe`, `DELETE /api/push/subscribe`, `GET /api/push/vapid-public-key`.
- `push_subscriptions` table (new): endpoint, p256dh, auth, label, created_at.
- On successful ingest → fire Web Push "Card downloaded: {driver} — {n} days, {km} km".
- `.env`: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT=mailto:…`.

### Reuse — Android APK (`tacho/android/`)
- Keep `UsbCcidTransport.kt` (USB OTG CCID — unchanged).
- **Strip** native UI: replace `MainActivity` content with a full-screen `WebView` pointing at the
  HTTPS BCA-BTT URL; JS enabled; DOM storage on.
- Add `TachoBridge` (`@JavascriptInterface`) exposing `connect()`, `transmit(hexApdu): String`,
  `powerOn(): String`, `isCardPresent(): Boolean` — delegating to `UsbCcidTransport`.
  Inject as `window.AndroidTacho`.
- Keep the `USB_DEVICE_ATTACHED` intent-filter + `device_filter.xml` so plugging the reader
  foregrounds the app. `TachoReader.kt`/`TachoParser.kt` become **optional** (logic now in JS) —
  the bridge only needs to ferry APDUs.

### Reuse — desktop scripts
- `tacho.py`, `tacho_report.py`, `inspect_ddd.py` stay as the offline/debug reference and the
  ground-truth oracle for the JS port (see §5 testing). No changes required.

---

## 5. Correctness strategy — port without regressions

The DDD/APDU logic is fiddly (codepage-prefixed text, BE-UTC timestamps, the `0x0504` ring
buffer). To port it to JS safely:

1. **Golden files.** Use the existing `downloads/*.ddd` as fixtures.
2. **Oracle.** Run `python3 tacho_report.py <file>.ddd` and `python3 inspect_ddd.py <file>.ddd`;
   capture canonical JSON (driver, vehicles, per-day trips, totals).
3. **Parity test.** Feed the same `.ddd` bytes to the new JS `DDDParser` (Node test harness) and
   assert byte-for-byte field parity against the oracle. This catches offset/endianness/codepage
   drift before any hardware is involved.
4. **Live APDU capture.** Log the exact APDU exchange from a real card (desktop `pcscd` path) once;
   replay against the JS layer to validate `tacho-core` independent of WebUSB.
5. Only then test end-to-end on real hardware via WebUSB.

---

## 6. Android APK specifics

- Android **Chrome** supports WebUSB, but a wrapped **WebView does not** — hence the native bridge.
- The web `transport.js` prefers `navigator.usb`; inside the APK that's absent, so it falls back to
  `window.AndroidTacho`. Same UI, same core, different transport — no forked frontend.
- USB permission flow stays native (`UsbManager.requestPermission` + the attach intent), exactly as
  the current app does. The bridge just exposes already-working calls to JS.
- Result: the APK is a thin shell. All tacho logic + UI is the shared web app; updates ship by
  updating the web UI, not rebuilding the APK.

---

## 7. The two spikes that de-risk everything (do first)

**Spike A — secure-context + WebUSB claim on Linux desktop.**
- Make `ops.lan` a secure context: whitelist `http://ops.lan` in
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, **or** serve `https://ops.lan` with a
  `mkcert` cert (install root CA on the laptop) via the existing **arr-proxy nginx**.
- Resolve the **`pcscd` conflict**: `sudo systemctl stop pcscd pcscd.socket` (interim), or a udev
  rule binding the reader's VID/PID away from the pcsc driver for WebUSB sessions. Confirm
  `device.claimInterface()` succeeds.
- Validate `navigator.usb.requestDevice({filters:[{classCode:0x0B}]})` lists the reader and a
  `powerOn` + first `SELECT` APDU returns `0x9000`.
- **Exit criterion:** one real APDU round-trips in Chrome. If this fails, the whole browser path is
  blocked — stop and reassess before building UI.

**Spike B — Web Push end-to-end.**
- Generate VAPID keys, register `sw.js`, subscribe, POST subscription, send one push from FastAPI
  via `pywebpush`, see the OS notification. Validates the secure-context + SW + VAPID chain.

Both spikes are small and independent; run them in parallel.

---

## 8. Phased delivery

| Phase | Outcome | Key items |
|---|---|---|
| **0. Spikes** | WebUSB claims reader over HTTPS; one push delivered | §7 A + B. Gate the project. |
| **1. JS core + parity** | `tacho-core.js` matches Python oracle on golden `.ddd` files | §5; Node parity tests. No hardware UI yet. |
| **2. WebUSB read panel** | Desktop Chrome: click icon → read card → see report | `ccid.js`, `reader-ui.js`, new tacho icon in header. |
| **3. Ingest + widget** | Parsed data POSTs to BCA-BTT → `tacho_daily` → existing widget shows it | `/api/tacho-reader/ingest`, upsert, validation. |
| **4. PWA + Web Push** | Installable app; push on card download | `manifest`, `sw.js`, push endpoints, VAPID, `pywebpush`. |
| **5. Android APK** | APK loads web UI; USB OTG card read via native bridge | WebView shell + `TachoBridge`; reuse `UsbCcidTransport.kt`. |
| **6. Hardening** | Multi-reader/codepage/expiry edge cases, error UX, docs | `pcscd` doc, browser-support notices, Windows note. |

---

## 9. Open items / risks to track

- **`pcscd` coexistence UX.** Decide the desktop story: ask users to stop `pcscd`, ship a
  udev rule, or a small "release reader" helper. Worst regression risk.
- **Reader compatibility.** Confirmed targets: Zoweetek ZW-12026-1, ACS ACR39U. Verify each
  enumerates a class-`0x0B` interface to WebUSB (some readers expose vendor-specific classes →
  need explicit VID/PID filters, not just `classCode`).
- **`tacho_daily` schema fit.** Confirm the table columns vs the parsed report fields
  (`card_in/out_time`, `start/end_mileage`, `distance_km`, `driver_name`, `vehicle_registration`)
  and the natural upsert key. The Explore map shows the read shape; verify the write path.
- **Cert distribution.** mkcert root CA must be trusted on every client device (laptop + Android).
  On Android, also trust the CA or the WebView/Chrome will reject the page.
- **Windows users** (if any) need WinUSB/Zadig — document as known limitation.
- **iOS / Firefox / Safari** cannot use the WebUSB path at all — show a graceful "use Chrome or the
  Android app" notice via the `transport.js` capability check.

---

## 10. First concrete steps

1. Run **Spike A** (HTTPS LAN page + stop `pcscd` + `requestDevice` + one APDU) and **Spike B**
   (VAPID + one push). These two gate everything.
2. In parallel, start **Phase 1**: scaffold `Moduals/webui/static/tacho/` and write the JS
   `DDDParser` against the golden `downloads/*.ddd` files with Node parity tests vs
   `tacho_report.py`.
3. Confirm the `tacho_daily` write schema + upsert key in `BCA-BTT/Moduals/webui/db.py`.
