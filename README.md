# Tacho Card Tool

Simple Python tool to download tachograph card data and display activity reports.

## Tray service with direct PostgreSQL sync

On a new Linux desktop, clone the repository and run the installer. It enables a per-user
systemd tray service, detects a supported PC/SC reader, saves card downloads locally for seven
days, and upserts card trips directly to PostgreSQL.

```bash
git clone https://github.com/craigst/bttacho.git && cd bttacho
TACHO_POSTGRES_PASSWORD='your-tacho-writer-password' ./install.sh \
  --postgres-host your-postgres-lan-ip \
  --postgres-port 5432 \
  --postgres-database postgres \
  --postgres-user tacho_writer
```

The password is supplied through an environment variable rather than an argument, so it is not
stored in shell history. The resulting local configuration is mode `0600`.

To create or rotate the restricted PostgreSQL account from a machine with SSH administration of
the database host, add these arguments:

```bash
./install.sh --provision-postgres --postgres-admin-ssh root@your-postgres-host
```

The tray badge is green only when a real authenticated PostgreSQL health check succeeds. Amber
means reading/syncing, and red means the database or delivery is unavailable.

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
