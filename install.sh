#!/usr/bin/env bash
# Install the tacho tray service as a systemd --user unit.
# Idempotent: safe to re-run to upgrade. ./install.sh --uninstall reverses it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=$(command -v python3 || true)
[[ -n "$PY" ]] || { echo "python3 not found" >&2; exit 1; }

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/tacho.service"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tacho"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/tacho.desktop"
RELEASE_CURRENT="${XDG_DATA_HOME:-$HOME/.local/share}/tacho/releases/current"
POSTGRES_HOST=""
POSTGRES_PORT="5432"
POSTGRES_DATABASE="postgres"
POSTGRES_USER="tacho_writer"
POSTGRES_ADMIN_SSH=""
PROVISION_POSTGRES=false

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[92m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[93m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[91m✗\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- uninstall ---
if [[ "${1:-}" == "--uninstall" ]]; then
    bold "Uninstalling tacho"
    systemctl --user disable --now tacho.service 2>/dev/null || true
    rm -f "$UNIT" "$DESKTOP"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "service removed"
    warn "config kept at $CONFIG_DIR"
    warn "downloads kept at ${XDG_DATA_HOME:-$HOME/.local/share}/tacho"
    exit 0
fi

# Connection settings deliberately stay out of the repository.  The password
# comes from TACHO_POSTGRES_PASSWORD (not an argument) so it is not recorded
# in shell history or process listings.
while (( $# )); do
    case "$1" in
        --postgres-host) POSTGRES_HOST="${2:?--postgres-host needs a value}"; shift 2 ;;
        --postgres-port) POSTGRES_PORT="${2:?--postgres-port needs a value}"; shift 2 ;;
        --postgres-database) POSTGRES_DATABASE="${2:?--postgres-database needs a value}"; shift 2 ;;
        --postgres-user) POSTGRES_USER="${2:?--postgres-user needs a value}"; shift 2 ;;
        --postgres-admin-ssh) POSTGRES_ADMIN_SSH="${2:?--postgres-admin-ssh needs a value}"; shift 2 ;;
        --help|-h)
            cat <<'EOF'
Usage: ./install.sh [--postgres-host HOST] [--postgres-port PORT]
                    [--postgres-database DB] [--postgres-user USER]
                    [--postgres-admin-ssh USER@HOST --provision-postgres]

Set TACHO_POSTGRES_PASSWORD in the environment with --postgres-host to configure
direct PostgreSQL sync. The password is never accepted as a command argument.
EOF
            exit 0 ;;
        --provision-postgres) PROVISION_POSTGRES=true; shift ;;
        *) die "unknown option: $1" ;;
    esac
done

# -------------------------------------------------------- provision postgres --
provision_postgres() {
    [[ -n "$POSTGRES_ADMIN_SSH" ]] || die "--provision-postgres needs --postgres-admin-ssh USER@HOST"
    [[ -n "$POSTGRES_HOST" ]] || die "--provision-postgres also needs --postgres-host HOST"
    PG_DB="$POSTGRES_DATABASE"
    PG_ROLE="$POSTGRES_USER"
    PG_TABLE="tacho_daily"
    PW=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c32)

    bold "Provisioning PostgreSQL $PG_ROLE"

    bold "SSH to $POSTGRES_ADMIN_SSH"
    ssh "$POSTGRES_ADMIN_SSH" bash -s <<REMOTE_SCRIPT
set -euo pipefail
PG_CONTAINER="postgresql"
if docker exec \$PG_CONTAINER psql -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_ROLE'" | grep -q 1; then
  echo "role $PG_ROLE already exists; choose a unique --postgres-user" >&2
  exit 1
fi
docker exec \$PG_CONTAINER psql -U postgres -c "CREATE ROLE $PG_ROLE LOGIN PASSWORD '$PW';"
echo "  created role $PG_ROLE"
docker exec \$PG_CONTAINER psql -U postgres -c "GRANT CONNECT ON DATABASE $PG_DB TO $PG_ROLE;"
docker exec \$PG_CONTAINER psql -U postgres -c "GRANT USAGE ON SCHEMA public TO $PG_ROLE;"
docker exec \$PG_CONTAINER psql -U postgres -c "GRANT SELECT, INSERT ON public.$PG_TABLE TO $PG_ROLE;"
docker exec \$PG_CONTAINER psql -U postgres -c "GRANT UPDATE (trip_date, day_of_week, vehicle_registration, card_in_time, card_out_time, shift_duration, driving_hours, start_mileage, end_mileage, distance_km, driver_name, card_number, download_timestamp, country, card_expiry, updated_at) ON public.$PG_TABLE TO $PG_ROLE;"
docker exec \$PG_CONTAINER psql -U postgres -c "GRANT USAGE ON SEQUENCE public.tacho_daily_id_seq TO $PG_ROLE;" 2>/dev/null || true
echo "  privileges granted"
REMOTE_SCRIPT
    export TACHO_POSTGRES_PASSWORD="$PW"
    ok "database account created; configuring this service next"
}

bold "Installing tacho from $REPO"

# ------------------------------------------------------------ dependencies ---
ok "python3 — $PY ($("$PY" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])'))"

missing=()
"$PY" -c 'import smartcard' 2>/dev/null || missing+=("python-pyscard")
"$PY" -c 'import PyQt6'     2>/dev/null || missing+=("python-pyqt6")
command -v pcscd >/dev/null 2>&1        || missing+=("pcsclite")

if (( ${#missing[@]} )); then
    warn "missing: ${missing[*]}"
    if command -v pacman >/dev/null 2>&1; then
        read -rp "  Install with pacman? [Y/n] " reply
        if [[ ! "$reply" =~ ^[Nn] ]]; then
            sudo pacman -S --needed "${missing[@]}" ccid
        else
            die "cannot continue without: ${missing[*]}"
        fi
    else
        die "install these for your distro, then re-run: ${missing[*]}"
    fi
else
    ok "dependencies present"
fi

# psycopg (PostgreSQL driver) -- not in Arch repos, install via venv
if ! "$PY" -c 'import psycopg, cryptography' 2>/dev/null; then
    warn "psycopg/cryptography missing — setting up project venv"
    VENV="$REPO/venv"
    if [[ ! -d "$VENV" ]]; then
        "$PY" -m venv --system-site-packages "$VENV"
        ok "venv created at $VENV"
    fi
    # The service intentionally reuses distro packages for PC/SC and Qt while
    # installing only psycopg in this private venv.
    sed -i 's/^include-system-site-packages = .*/include-system-site-packages = true/' "$VENV/pyvenv.cfg"
    "$VENV/bin/pip" install --quiet "psycopg[binary]>=3.2.0" "cryptography>=42.0.0"
    ok "psycopg and cryptography installed in venv"
    PY="$VENV/bin/python3"
fi

# --------------------------------------------------------------- pcscd ------
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet pcscd.socket; then
        warn "pcscd.socket not active — enabling"
        sudo systemctl enable --now pcscd.socket
    fi
    ok "pcscd.socket active"
fi

# ------------------------------------------------------------- preflight ----
bold "Preflight"
if ! "$PY" "$REPO/scripts/tacho-check.py" --allow-no-reader; then
    die "preflight failed — fix the above before installing"
fi

# ---------------------------------------------------------------- config ----
mkdir -p "$CONFIG_DIR"
if [[ -f "$CONFIG_DIR/config.json" ]]; then
    ok "config exists — left untouched"
else
    "$PY" -c "
import sys; sys.path.insert(0, '$REPO')
from tacho_service.config import Config
Config.load()
"
    chmod 600 "$CONFIG_DIR/config.json"
    ok "config written to $CONFIG_DIR/config.json (0600)"
fi

if [[ "$PROVISION_POSTGRES" == true ]]; then
    provision_postgres
fi

if [[ -n "$POSTGRES_HOST" ]]; then
    [[ -n "${TACHO_POSTGRES_PASSWORD:-}" ]] || die "set TACHO_POSTGRES_PASSWORD to configure PostgreSQL"
    TACHO_POSTGRES_HOST="$POSTGRES_HOST" \
    TACHO_POSTGRES_PORT="$POSTGRES_PORT" \
    TACHO_POSTGRES_DATABASE="$POSTGRES_DATABASE" \
    TACHO_POSTGRES_USER="$POSTGRES_USER" \
    "$PY" - <<'PY'
import os
from tacho_service.config import Config, Destination

config = Config.load()
destination = next((d for d in config.destinations if d.type == "postgres"), None)
if destination is None:
    destination = Destination(id="postgres", name="PostgreSQL", type="postgres")
    config.destinations.insert(0, destination)
destination.name = "PostgreSQL"
destination.host = os.environ["TACHO_POSTGRES_HOST"]
destination.port = int(os.environ["TACHO_POSTGRES_PORT"])
destination.database = os.environ["TACHO_POSTGRES_DATABASE"]
destination.username = os.environ["TACHO_POSTGRES_USER"]
destination.password = os.environ["TACHO_POSTGRES_PASSWORD"]
destination.enabled = True
config.save()
PY
    ok "PostgreSQL direct sync configured (credentials saved 0600)"
fi

# ------------------------------------------------------------ systemd unit --
mkdir -p "$UNIT_DIR"
# Preserve an updater-managed release pointer when re-running the installer.
SERVICE_ROOT="$REPO"
if [[ -e "$RELEASE_CURRENT" || -L "$RELEASE_CURRENT" ]]; then
    SERVICE_ROOT="$RELEASE_CURRENT"
fi
cat > "$UNIT" <<EOF
[Unit]
Description=Tacho driver card service
Documentation=https://github.com/craigst/bttacho
# The tray needs a graphical session; starting earlier means no tray to register with.
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$SERVICE_ROOT
ExecStart=$PY -m tacho_service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
ok "unit written to $UNIT"

systemctl --user daemon-reload
systemctl --user enable tacho.service >/dev/null 2>&1
ok "enabled at login"

# -------------------------------------------------------------- desktop -----
mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Tacho
Comment=Tachograph driver card service
Exec=$PY -m tacho_service
Terminal=false
Categories=Utility;
EOF

# ---------------------------------------------------------------- restart ---
if systemctl --user is-active --quiet tacho.service; then
    systemctl --user restart tacho.service
    ok "service restarted"
else
    systemctl --user start tacho.service || warn "start failed — see: journalctl --user -u tacho -n 40"
fi

echo
bold "Done"
cat <<EOF

  Status    systemctl --user status tacho
  Logs      journalctl --user -u tacho -f
  Check     python3 scripts/tacho-check.py --card

  Add a destination from the tray icon: Settings > Destinations.
  Nothing is sent until you configure one.
EOF
