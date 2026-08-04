# Android Build

This module contains the Android 15 APK build of the Tacho Downloader app.

## Requirements
- Android Studio (current stable)
- Android SDK Platform 35 + Build-Tools 35.x
- JDK 17
- Android USB Host support on the phone (required for USB OTG)

## Build
1. Open `android/` in Android Studio.
2. Let Gradle sync and download dependencies.
3. Build a debug APK via **Build > Build APK(s)**.
4. Install the APK on the device.

## How It Works
- Plug in the USB CCID reader via OTG.
- The app waits for a card, reads it when inserted, extracts the report, and posts to the webhook.
- After extraction, the UI stays at 100% until the card is removed.
- When the card is removed, the app returns to “waiting for card”.

## File Retention
- The app stores only **one** `.ddd` file at a time.
- Each new download deletes any previous `.ddd` files in the app’s `downloads/` folder.

## USB Device
This build accepts any CCID (Smart Card class `0x0b`) reader, including:
- Zoweetek ZW-12026-1
- ACS ACR39U-NF PocketMate II

If you need to restrict devices later, update `app/src/main/res/xml/device_filter.xml`.

## Troubleshooting
- **No prompt to open the app**: Launch it from the app drawer once, then re‑plug the reader.
- **No card detected**: Make sure the reader is CCID and the card is fully inserted.
- **Reader disconnects**: Re‑plug the OTG cable; the app will resume waiting.
