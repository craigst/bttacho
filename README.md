# Tacho Card Tool

Simple Python tool to download tachograph card data and display activity reports.

## Tray service with direct PostgreSQL sync

On a new Linux desktop, clone the repository and run the installer. It enables a per-user
systemd tray service, detects a supported PC/SC reader, saves card downloads locally for seven
days, and upserts card trips directly to PostgreSQL.

For Craig's zues deployment, a fresh reader computer needs only:

```bash
git clone https://github.com/craigst/bttacho.git && cd bttacho && ./onboard-zues.sh
```

The onboarding script retrieves the single shared `tacho_writer` password over authenticated
root SSH, then writes the local configuration mode `0600`. The password never appears in shell
history or terminal output.

For another PostgreSQL server, use the generic installer instead:

```bash
TACHO_POSTGRES_PASSWORD='your-tacho-writer-password' ./install.sh \
  --postgres-host your-postgres-lan-ip \
  --postgres-port 5432 --postgres-database postgres --postgres-user tacho_writer
```

To create an isolated account for another PostgreSQL deployment, use a unique database user and
add the administration arguments in the same command:

```bash
./install.sh --provision-postgres \
  --postgres-admin-ssh root@your-postgres-host \
  --postgres-host your-postgres-lan-ip \
  --postgres-user tacho_writer_laptop
```

Provisioning never rotates an existing account: use a unique `--postgres-user` per computer.

The tray badge is green only when a real authenticated PostgreSQL health check succeeds. Amber
means reading/syncing, and red means the database or delivery is unavailable.

## Signed app updates

Installed trays can poll GitHub Releases for signed application-code updates. Updates are staged
outside the active process, wait until the reader and delivery queue are idle, then switch an
atomic release pointer and restart `tacho.service`. A failed startup/SQL health check rolls back
to the previous release. Credentials, card policy, `.ddd` downloads, and the SQLite outbox are
never replaced.

The updater remains disabled until an Ed25519 public key is entered in Settings. To publish a
release, keep the private key off GitHub and generate a manifest for the release asset:

```bash
python3 scripts/sign-release.py tacho-1.0.1.tar.gz 1.0.1 \
  https://github.com/craigst/bttacho/releases/download/v1.0.1/tacho-1.0.1.tar.gz \
  --private-key ~/.config/tacho/release-signing-key.pem \
  --output update-manifest.json
```

Publish the signed `update-manifest.json` on the repository's `main` branch and attach the exact
archive at the signed GitHub Release URL. Then enter the printed base64url public key in the tray
Settings. The tray reports `CHECKING`, `VERIFIED`, `STAGED`, `APPLIED`, or `ROLLED BACK` separately
from card-sync status.

## Card trust on a new laptop

Card trust is intentionally local to each laptop and is stored as a SHA-256 fingerprint,
never as the card number. On a new installation the first card read may show `Card not trusted`.
Keep that card inserted and choose **Trust this card** in the report window, then confirm. The
same physical card can be enrolled independently on each laptop; existing fingerprints remain
compatible across updates.

## Requirements

- `pcscd` (PC/SC daemon)
- `python-pyscard`

On CachyOS/Arch:
```
sudo pacman -S pcsclite python-pyscard
```

Enable PC/SC daemon:
```
sudo systemctl enable --now pcscd
```

Create virtual environment and install dependencies:
```
python3 -m venv venv
source venv/bin/activate
pip install pyscard
```

## Usage

### Via Desktop Menu/Shortcut (Recommended)
- Click "Download Tacho" from your menu
- Script will automatically detect card, download, show 14-day report
- **Press Enter to close** when done reading the report

### Via Command Line
#### Download card and show 2-week report
```
python3 tacho_download.py
```

#### Customize timeout (default: 120 seconds)
```
python3 tacho_download.py -t 60
```

#### Show report for different number of days
```
python3 tacho_download.py -d 7
```

#### Save to specific filename
```
python3 tacho_download.py -o mycard.ddd
```

#### Show report for existing file only (skip download)
```
python3 tacho.py -r downloads/driver_xxx.ddd
```

### Help
```
python3 tacho_download.py --help
```

## Options

- `-t, --timeout` - Wait timeout in seconds (default: 120)
- `-d, --days` - Number of days to show in report (default: 14)
- `-o, --output` - Output filename (default: auto-generated)
- `-r, --report-only` - Path to existing .ddd file to show report only

## Features

- Automatic card reader detection
- Animated download progress with scanning effect
- Nice ASCII art UI with card visualization
- 2-week activity report by default
- Driver information display (name, card number, expiry, country)
- Vehicle usage summary with odometer readings
- Waits for Enter before closing (for desktop shortcuts)

## Android (APK)

An Android 15 native build lives in `android/`. It supports USB OTG CCID readers and posts to the same webhook.

See `android/README.md` for build steps and usage.
