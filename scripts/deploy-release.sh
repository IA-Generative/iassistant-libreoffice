#!/usr/bin/env bash
# Deploy a new MIrAI LibreOffice release via device-management.
#
# Uses the unified endpoint: POST /api/plugins/{slug}/deploy
# which handles everything in one call:
#   - artifact upload (upsert)
#   - version creation (upsert + deprecate old versions)
#   - dm-config.json + dm-manifest.json extraction
#   - campaign creation (auto-complete old campaigns)
#
# Usage:
#   scripts/deploy-release.sh --bootstrap-url <url> [options]
#
# Options:
#   --bootstrap-url <url>    Bootstrap server URL (required)
#   --slug <slug>            Plugin slug (default: mirai-libreoffice)
#   --version <ver>          Version string (default: auto-detected from package)
#   --strategy <s>           Rollout strategy: immediate|canary (default: canary)
#   --urgency <u>            Urgency: low|normal|critical (default: normal)
#   --admin-token <tok>      Admin API token (default: $DM_ADMIN_TOKEN env var)
#   --cohort-id <id>         Target cohort ID (default: none = all devices)
#   --config <path>          Config file for OXT build (optional)
#   --dry-run                Build and show plan without deploying
#   -h, --help               Show this help

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OXT_PATH="$ROOT_DIR/dist/mirai.oxt"

# Defaults
BOOTSTRAP_URL=""
SLUG="mirai-libreoffice"
VERSION=""
STRATEGY="canary"
URGENCY="normal"
ADMIN_TOKEN="${DM_ADMIN_TOKEN:-}"
COHORT_ID=""
BUILD_CONFIG=""
DRY_RUN=false

log()  { printf '▶ %s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }
err()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,25p' "$0"
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bootstrap-url) BOOTSTRAP_URL="${2:-}"; shift 2 ;;
    --slug)          SLUG="${2:-}"; shift 2 ;;
    --version)       VERSION="${2:-}"; shift 2 ;;
    --strategy)      STRATEGY="${2:-}"; shift 2 ;;
    --urgency)       URGENCY="${2:-}"; shift 2 ;;
    --admin-token)   ADMIN_TOKEN="${2:-}"; shift 2 ;;
    --cohort-id)     COHORT_ID="${2:-}"; shift 2 ;;
    --config)        BUILD_CONFIG="${2:-}"; shift 2 ;;
    --dry-run)       DRY_RUN=true; shift ;;
    -h|--help)       usage ;;
    *)               err "Unknown option: $1" ;;
  esac
done

[ -n "$BOOTSTRAP_URL" ] || err "Missing --bootstrap-url"
[ -n "$ADMIN_TOKEN" ]   || err "Missing --admin-token or DM_ADMIN_TOKEN env var"

if [ -z "$VERSION" ]; then
  VERSION=$(sed -n 's/.*<version value="\([^"]*\)".*/\1/p' "$ROOT_DIR/oxt/description.xml" 2>/dev/null || echo "")
fi
log "Version: ${VERSION:-auto-detect from package}"

log "Building OXT..."
BUILD_ARGS=()
if [ -n "$BUILD_CONFIG" ]; then
  BUILD_ARGS+=(--config "$BUILD_CONFIG")
fi
"$ROOT_DIR/scripts/02-build-oxt.sh" "${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}"
ok "OXT built: $OXT_PATH"

CHECKSUM="sha256:$(shasum -a 256 "$OXT_PATH" | awk '{print $1}')"
FILE_SIZE=$(stat -f%z "$OXT_PATH" 2>/dev/null || stat --printf=%s "$OXT_PATH")
log "Checksum: $CHECKSUM"
log "Size: $((FILE_SIZE / 1024)) KB"

printf '\n'
printf '┌─────────────────────────────────────────┐\n'
printf '│  MIrAI Release Deployment Plan          │\n'
printf '├─────────────────────────────────────────┤\n'
printf '│  Plugin:     %-26s │\n' "$SLUG"
printf '│  Version:    %-26s │\n' "${VERSION:-auto}"
printf '│  Strategy:   %-26s │\n' "$STRATEGY"
printf '│  Urgency:    %-26s │\n' "$URGENCY"
printf '│  Bootstrap:  %-26s │\n' "$BOOTSTRAP_URL"
printf '│  Checksum:   %-26s │\n' "${CHECKSUM:0:20}..."
if [ -n "$COHORT_ID" ]; then
  printf '│  Cohort:     %-26s │\n' "$COHORT_ID"
fi
printf '│  Endpoint:   %-26s │\n' "POST /api/plugins/$SLUG/deploy"
printf '└─────────────────────────────────────────┘\n'
printf '\n'

if [ "$DRY_RUN" = true ]; then
  log "Dry run — stopping here."
  exit 0
fi

log "Deploying to ${BOOTSTRAP_URL}..."

DEPLOY_ARGS=(
  -s -X POST
  "${BOOTSTRAP_URL}/api/plugins/${SLUG}/deploy"
  -H "X-Admin-Token: ${ADMIN_TOKEN}"
  -F "binary=@${OXT_PATH}"
  -F "strategy=${STRATEGY}"
  -F "urgency=${URGENCY}"
)
if [ -n "$VERSION" ]; then
  DEPLOY_ARGS+=(-F "version=${VERSION}")
fi
if [ -n "$COHORT_ID" ]; then
  DEPLOY_ARGS+=(-F "cohort_id=${COHORT_ID}")
fi

RESPONSE=$(curl "${DEPLOY_ARGS[@]}" 2>&1)

# Parse response
DEPLOY_OK=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null || echo "")
if [ "$DEPLOY_OK" != "True" ]; then
  printf 'Deploy response: %s\n' "$RESPONSE" >&2
  err "Deployment failed"
fi

CAMPAIGN_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('campaign_id',''))" 2>/dev/null)
ARTIFACT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('artifact_id',''))" 2>/dev/null)
VERSION_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version_id',''))" 2>/dev/null)
DEPLOYED_VER=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null)
DEPLOYED_CHECKSUM=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('checksum',''))" 2>/dev/null)

ok "Deployed successfully!"
printf '\n'
printf '  Version:     %s\n' "$DEPLOYED_VER"
printf '  Artifact:    ID=%s\n' "$ARTIFACT_ID"
printf '  Version:     ID=%s\n' "$VERSION_ID"
printf '  Campaign:    ID=%s\n' "$CAMPAIGN_ID"
printf '  Checksum:    %s\n' "$DEPLOYED_CHECKSUM"
printf '  Strategy:    %s\n' "$STRATEGY"

printf '\n'
printf '  Track progress:\n'
printf '    curl -s -H "X-Admin-Token: $DM_ADMIN_TOKEN" \\\n'
printf '      %s/api/campaigns/%s/progress | python3 -m json.tool\n' "$BOOTSTRAP_URL" "$CAMPAIGN_ID"
printf '\n'
printf '  Admin UI:\n'
printf '    %s/admin/deploy/%s\n' "$BOOTSTRAP_URL" "$CAMPAIGN_ID"
printf '\n'
printf '  Pause rollout:\n'
printf '    curl -s -X PATCH -H "X-Admin-Token: $DM_ADMIN_TOKEN" \\\n'
printf '      %s/api/campaigns/%s/pause\n' "$BOOTSTRAP_URL" "$CAMPAIGN_ID"
printf '\n'
printf '  Abort and rollback:\n'
printf '    curl -s -X PATCH -H "X-Admin-Token: $DM_ADMIN_TOKEN" \\\n'
printf '      %s/api/campaigns/%s/abort\n' "$BOOTSTRAP_URL" "$CAMPAIGN_ID"
printf '\n'
