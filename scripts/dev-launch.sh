#!/usr/bin/env bash
# Build OXT, install extension, and open LibreOffice with a test document.
#
# Usage:
#   scripts/dev-launch.sh [--doc <path>] [--no-build] [--config <path>]
#
# Options:
#   --doc <path>     Document to open (default: tests/fixtures/sample.odt)
#   --no-build       Skip OXT build (use existing dist/mirai.oxt)
#   --config <path>  Config file passed to 02-build-oxt.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOFFICE="/Applications/LibreOffice.app/Contents/MacOS/soffice"
UNOPKG="/Applications/LibreOffice.app/Contents/MacOS/unopkg"
OXT_PATH="$ROOT_DIR/dist/mirai.oxt"
TEST_DOC="$ROOT_DIR/tests/fixtures/sample.odt"
DO_BUILD=true
BUILD_EXTRA_ARGS=()
CONFIG_PROFILE=""

log()  { printf '▶ %s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }
err()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --doc)
      TEST_DOC="${2:-}"; [ -n "$TEST_DOC" ] || err "Missing value for --doc"; shift 2 ;;
    --no-build)
      DO_BUILD=false; shift ;;
    --config)
      CONFIG_PROFILE="${2:-}"
      BUILD_EXTRA_ARGS+=(--config "$CONFIG_PROFILE"); shift 2 ;;
    -h|--help)
      sed -n '2,10p' "$0"; exit 0 ;;
    *)
      err "Unknown option: $1" ;;
  esac
done

[ -x "$SOFFICE" ] || err "LibreOffice not found at $SOFFICE"
[ -x "$UNOPKG"  ] || err "unopkg not found at $UNOPKG"

: > "$HOME/Library/Application Support/LibreOffice/4/user/config/mirai/mirai.log" 2>/dev/null || true

if pgrep -x soffice >/dev/null 2>&1; then
  log "Closing LibreOffice..."
  osascript -e 'tell application "LibreOffice" to quit' 2>/dev/null || true
  # Wait until process is gone (max 10 s)
  for i in $(seq 1 10); do
    pgrep -x soffice >/dev/null 2>&1 || break
    sleep 1
  done
fi

if [ "$DO_BUILD" = true ]; then
  log "Building OXT..."
  "$ROOT_DIR/scripts/02-build-oxt.sh" "${BUILD_EXTRA_ARGS[@]+"${BUILD_EXTRA_ARGS[@]}"}"
  ok "OXT built: $OXT_PATH"
else
  [ -f "$OXT_PATH" ] || err "OXT not found: $OXT_PATH (run without --no-build first)"
  log "Skipping build, using: $OXT_PATH"
fi

if [ -n "$CONFIG_PROFILE" ] && [ -f "$CONFIG_PROFILE" ]; then
  LO_CONFIG_DIR="$HOME/Library/Application Support/LibreOffice/4/user/config"
  LO_CONFIG_FILE="$LO_CONFIG_DIR/config.json"
  mkdir -p "$LO_CONFIG_DIR"
  if [ -f "$LO_CONFIG_FILE" ]; then
    # Merge: profile values as base, preserve local-only keys (extensionUUID, plugin_uuid)
    python3 - "$CONFIG_PROFILE" "$LO_CONFIG_FILE" <<'PY'
import json, sys
profile_path, local_path = sys.argv[1], sys.argv[2]
with open(profile_path, "r") as f:
    profile = json.load(f)
try:
    with open(local_path, "r") as f:
        local = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    local = {}
# Keep local-only runtime keys
for key in ("extensionUUID", "plugin_uuid"):
    if key in local and key not in profile:
        profile[key] = local[key]
with open(local_path, "w", encoding="utf-8") as f:
    json.dump(profile, f, indent=4, ensure_ascii=False)
    f.write("\n")
PY
  else
    cp "$CONFIG_PROFILE" "$LO_CONFIG_FILE"
  fi
  log "Config profile applied: $CONFIG_PROFILE"
fi

log "Installing extension..."
"$UNOPKG" remove "fr.gouv.interieur.mirai" 2>/dev/null || true
"$UNOPKG" add --force --suppress-license "$OXT_PATH"
ok "Extension installed"

if [ ! -f "$TEST_DOC" ]; then
  log "Creating test document: $TEST_DOC"
  python3 "$ROOT_DIR/scripts/_make_sample_odt.py" "$TEST_DOC"
  ok "Test document created"
fi

log "Opening: $TEST_DOC"
open -a LibreOffice "$TEST_DOC"
ok "LibreOffice launched"
