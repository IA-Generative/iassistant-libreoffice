#!/bin/bash
# Test du cycle de mise à jour du plugin MIrAI
# Usage : ./scripts/test-update.sh
# Prérequis : LibreOffice ouvert avec un document Writer

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$HOME/Library/Application Support/LibreOffice/4/user/config/mirai/mirai.log"
mkdir -p "$(dirname "$LOG")"
UNOPKG="/Applications/LibreOffice.app/Contents/MacOS/unopkg"
SOFFICE="/Applications/LibreOffice.app/Contents/MacOS/soffice"
OXT_TMP="/tmp/mirai_update.oxt"
EXT_ID="fr.gouv.interieur.mirai"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - [TEST-UPDATE] $1" | tee -a "$LOG"; }

# 1. Build
log "Building OXT..."
"$SCRIPT_DIR/02-build-oxt.sh"
cp "$PROJECT_DIR/dist/mirai.oxt" "$OXT_TMP"
log "OXT copied to $OXT_TMP"

# 2. Quit LO
log "Quitting LibreOffice..."
osascript -e 'tell application "LibreOffice" to quit' 2>/dev/null || true

log "Waiting for soffice + oosplash to exit..."
while pgrep -x soffice >/dev/null 2>&1 || pgrep -x oosplash >/dev/null 2>&1; do
    sleep 1
done
log "LO quit detected"

# 3. Wait (same as update script)
sleep 5
log "Post-quit delay done"

# 4. Reinstall
log "Removing old extension..."
"$UNOPKG" remove "$EXT_ID" 2>/dev/null || true

log "Installing new extension..."
"$UNOPKG" add --force --suppress-license "$OXT_TMP"
RC=$?
if [ "$RC" -eq 0 ]; then
    log "unopkg add OK"
else
    log "unopkg add FAILED rc=$RC"
    exit 1
fi

# 5. Sync + wait
sync
sleep 3
log "Filesystem synced"

# 6. Relaunch
log "Launching LibreOffice Writer..."
"$SOFFICE" --writer &

log "Done — check that MIrAI menus are visible in Writer"
log "Logs: grep '[TEST-UPDATE]\\|MainJob' \"$LOG\" | tail -20"

# Cleanup
rm -f "$OXT_TMP"
