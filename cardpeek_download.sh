#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: cardpeek_download.sh --reader NAME --output PATH [--signed] [--cardpeek PATH]

Options:
  --reader NAME     PC/SC reader name (required)
  --output PATH     Output .ddd path (required)
  --signed          Use signed download option (default: unsigned)
  --cardpeek PATH   cardpeek binary path (default: auto-detect)
  -h, --help        Show this help
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

reader=""
output=""
signed="0"
cardpeek_bin=""
cardpeek_dir=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reader|-r)
      if [[ $# -lt 2 ]]; then
        die "Missing value for --reader"
      fi
      reader="$2"
      shift 2
      ;;
    --output|-o)
      if [[ $# -lt 2 ]]; then
        die "Missing value for --output"
      fi
      output="$2"
      shift 2
      ;;
    --signed)
      signed="1"
      shift
      ;;
    --cardpeek)
      if [[ $# -lt 2 ]]; then
        die "Missing value for --cardpeek"
      fi
      cardpeek_bin="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

if [[ -z "$reader" || -z "$output" ]]; then
  usage
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cardpeek_dir="${CARDPEEK_DIR:-"$script_dir/.cardpeek"}"
if [[ "$cardpeek_dir" != /* ]]; then
  cardpeek_dir="$(pwd -P)/$cardpeek_dir"
fi

if [[ "$reader" != *"://"* ]]; then
  reader="pcsc://$reader"
fi

if [[ -z "$cardpeek_bin" ]]; then
  if command -v cardpeek >/dev/null 2>&1; then
    cardpeek_bin="$(command -v cardpeek)"
  elif [[ -x "$script_dir/cardpeek-0.8.4/cardpeek" ]]; then
    cardpeek_bin="$script_dir/cardpeek-0.8.4/cardpeek"
  else
    die "cardpeek binary not found. Build or install cardpeek and pass --cardpeek."
  fi
fi

if [[ "$output" != /* ]]; then
  output="$(pwd -P)/$output"
fi

out_dir="$(dirname "$output")"
if [[ "$out_dir" != "." && ! -d "$out_dir" ]]; then
  mkdir -p "$out_dir"
fi

if [[ ! -d "$cardpeek_dir" ]]; then
  mkdir -p "$cardpeek_dir"
fi

dot_dir="$script_dir/cardpeek-0.8.4/dot_cardpeek_dir"
if [[ -d "$dot_dir" ]]; then
  if [[ ! -f "$cardpeek_dir/version" ]]; then
    cp -a "$dot_dir/." "$cardpeek_dir/"
  fi
fi

tacho=""
for p in \
  "$script_dir/cardpeek-0.8.4/dot_cardpeek_dir/scripts/tachograph.lua" \
  /usr/share/cardpeek/scripts/tachograph.lua \
  /usr/share/cardpeek/tachograph.lua \
  "$HOME/.cardpeek/scripts/tachograph.lua"
do
  if [[ -f "$p" ]]; then
    tacho="$p"
    break
  fi
done

if [[ -z "$tacho" ]]; then
  die "tachograph.lua not found. Install cardpeek scripts or point to a local copy."
fi

tmp="$(mktemp)"
cat >"$tmp" <<'LUA'
local out = os.getenv("DDD_OUT")
local signed = (os.getenv("DDD_SIGNED") == "1")

ui.question = function(prompt, opts)
  if #opts == 3 then
    return signed and 1 or 2
  end
  return 1
end

ui.select_file = function(...)
  return nil, out
end

dofile(os.getenv("TACHO_LUA"))
LUA

log "Using cardpeek: $cardpeek_bin"
log "Using script:  $tacho"
log "Using config:  $cardpeek_dir"

CARDPEEK_DIR="$cardpeek_dir" CARDPEEK_SCRIPT="$tmp" DDD_OUT="$output" DDD_SIGNED="$signed" TACHO_LUA="$tacho" \
  "$cardpeek_bin" --console --reader "$reader" --exec "dofile(os.getenv('CARDPEEK_SCRIPT')); os.exit()"

rm -f "$tmp"
log "Output: $output"
