#!/usr/bin/env bash
# Reset the LibreOffice plugin to a clean-install state.
#
# What this does:
#   1. Quit LibreOffice (if running)
#   2. Erase local data: config/mirai/, the config.json identity stub, legacy
#      files of older versions, and the macOS Keychain entries
#   3. Delete LibreOffice log files  (unopkg.log, GraphicsRenderTests.log)
#   4. Purge extension temp cache    (extensions/tmp/)
#   5. Uninstall the Mirai extension (optional, --uninstall flag)
#
# Usage:
#   scripts/00-clean-install.sh [--uninstall]

set -euo pipefail

SOFFICE="/Applications/LibreOffice.app/Contents/MacOS/soffice"
UNOPKG="/Applications/LibreOffice.app/Contents/MacOS/unopkg"
LO_USER_DIR="$HOME/Library/Application Support/LibreOffice/4/user"
KEYCHAIN_SERVICE="MIrAI-LibreOffice"
KEYCHAIN_ACCOUNTS=(relay_client_id relay_client_key relay_key_expires_at refresh_token proxy_password llm_api_tokens)
LEGACY_FILES=(config_cache.json assistant_conversation.json prompts_calc.txt prompt.txt telemetry_queue.json secure_bootstrap_state.json)

DO_UNINSTALL=false

log()  { printf '▶ %s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --uninstall)          DO_UNINSTALL=true; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
  esac
done

# ── 1. Quit LibreOffice ───────────────────────────────────────────────────────
if pgrep -x soffice >/dev/null 2>&1; then
  log "Closing LibreOffice..."
  osascript -e 'tell application "LibreOffice" to quit' 2>/dev/null || true
  for i in $(seq 1 10); do
    pgrep -x soffice >/dev/null 2>&1 || break
    sleep 1
  done
  ok "LibreOffice closed"
fi

# ── 2. Erase local data ───────────────────────────────────────────────────────
log "Erasing local data..."
rm -rf "$LO_USER_DIR/config/mirai"
rm -f "$LO_USER_DIR/config/config.json"
for f in "${LEGACY_FILES[@]}"; do
  rm -f "$LO_USER_DIR/config/$f"
done
rm -rf "$LO_USER_DIR/config/pending_update"
for account in "${KEYCHAIN_ACCOUNTS[@]}"; do
  security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "$account" >/dev/null 2>&1 || true
done
ok "Local data erased"

# ── 3. Delete log files ───────────────────────────────────────────────────────
log "Deleting log files..."
rm -f "$LO_USER_DIR/unopkg.log"
rm -f "$LO_USER_DIR/GraphicsRenderTests.log"
ok "Log files deleted"

# ── 4. Purge extension temp cache ─────────────────────────────────────────────
log "Purging extension temp cache..."
rm -rf "$LO_USER_DIR/extensions/tmp/"
ok "Extension temp cache purged"

# ── 5. Uninstall extension (optional) ────────────────────────────────────────
if [ "$DO_UNINSTALL" = true ]; then
  if [ -x "$UNOPKG" ]; then
    log "Uninstalling Mirai extension..."
    "$UNOPKG" remove "fr.gouv.interieur.mirai" 2>/dev/null && ok "Extension uninstalled" || ok "Extension was not installed"
  else
    printf 'WARN: unopkg not found at %s — skipping uninstall\n' "$UNOPKG" >&2
  fi
fi

printf '\n✓ Clean install ready. Run scripts/dev-launch.sh to reinstall.\n'
