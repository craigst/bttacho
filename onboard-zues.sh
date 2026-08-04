#!/usr/bin/env bash
# Configure this computer as a Tacho reader on Craig's local PostgreSQL service.
# The shared database password never appears in this script, command history,
# or terminal output: it is retrieved over authenticated root SSH from zues.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: ./onboard-zues.sh

Installs the Tacho tray service for the local zues PostgreSQL deployment.
Requires root SSH access to 10.10.254.13 and the shared password file created
by the administrator. Override defaults with TACHO_POSTGRES_HOST or
TACHO_ADMIN_SSH if the server moves.
EOF
    exit 0
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POSTGRES_HOST="${TACHO_POSTGRES_HOST:-10.10.254.13}"
POSTGRES_PORT="${TACHO_POSTGRES_PORT:-5432}"
POSTGRES_DATABASE="${TACHO_POSTGRES_DATABASE:-postgres}"
POSTGRES_USER="${TACHO_POSTGRES_USER:-tacho_writer}"
ADMIN_SSH="${TACHO_ADMIN_SSH:-root@10.10.254.13}"
SECRET_PATH="/mnt/user/appdata/tacho/tacho_writer.password"

echo "Retrieving shared Tacho database credentials from ${ADMIN_SSH}…"
password="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$ADMIN_SSH" "cat '$SECRET_PATH'")"
[[ -n "$password" ]] || { echo "Shared Tacho password is empty" >&2; exit 1; }

exec env TACHO_POSTGRES_PASSWORD="$password" "$REPO/install.sh" \
  --postgres-host "$POSTGRES_HOST" \
  --postgres-port "$POSTGRES_PORT" \
  --postgres-database "$POSTGRES_DATABASE" \
  --postgres-user "$POSTGRES_USER"
