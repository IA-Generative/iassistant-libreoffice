#!/usr/bin/env bash
# Bump the MIrAI extension version across all manifest files, build, and show deploy instructions.
#
# Usage:
#   scripts/bump-version.sh [new_version]
#
# If no version is given, the script proposes the next patch version.
# All manifest files are updated, the OXT is built, and deploy instructions are printed.
# Works on macOS, Linux and Windows (Git Bash / MSYS2).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DESC_XML="$ROOT_DIR/oxt/description.xml"
MANIFEST="$ROOT_DIR/dm-manifest.json"
LICENSE="$ROOT_DIR/oxt/registration/license.txt"

# Portable sed -i (macOS vs Linux)
_sed_i() {
  if sed --version >/dev/null 2>&1; then
    sed -i "$@"      # GNU sed (Linux)
  else
    sed -i '' "$@"    # BSD sed (macOS)
  fi
}

CURRENT=$(sed -n 's/.*<version value="\([^"]*\)".*/\1/p' "$DESC_XML" 2>/dev/null || echo "0.0.0")
echo "Version actuelle : $CURRENT"

if [ -n "${1:-}" ]; then
  PROPOSED="$1"
else
  # Increment last segment
  IFS='.' read -ra PARTS <<< "$CURRENT"
  LAST_IDX=$(( ${#PARTS[@]} - 1 ))
  PARTS[$LAST_IDX]=$(( ${PARTS[$LAST_IDX]} + 1 ))
  PROPOSED=$(IFS='.'; echo "${PARTS[*]}")
fi

printf "Nouvelle version proposée : %s\n" "$PROPOSED"
printf "Valider ? [O/n] "
read -r CONFIRM
case "${CONFIRM:-O}" in
  [nN]*) echo "Annulé."; exit 0 ;;
esac
NEW_VERSION="$PROPOSED"

echo "▶ Mise à jour des fichiers..."

# description.xml
_sed_i "s|<version value=\"[^\"]*\"|<version value=\"${NEW_VERSION}\"|" "$DESC_XML"
echo "  ✓ oxt/description.xml"

# dm-manifest.json — update first changelog entry version
python3 -c "
import json, sys
path = '$MANIFEST'
with open(path) as f: d = json.load(f)
if d.get('changelog') and len(d['changelog']) > 0:
    d['changelog'][0]['version'] = '$NEW_VERSION'
with open(path, 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
    f.write('\n')
"
echo "  ✓ dm-manifest.json"

# license.txt
_sed_i "s|version [0-9][0-9.]*|version ${NEW_VERSION}|" "$LICENSE"
echo "  ✓ oxt/registration/license.txt"

echo "▶ Build du package..."
"$ROOT_DIR/scripts/02-build-oxt.sh" --config config/profiles/config.default.integration.json 2>&1 | tail -2
echo "  ✓ dist/mirai.oxt (version $NEW_VERSION)"

CHECKSUM=$(shasum -a 256 "$ROOT_DIR/dist/mirai.oxt" 2>/dev/null || sha256sum "$ROOT_DIR/dist/mirai.oxt" 2>/dev/null)
CHECKSUM=$(echo "$CHECKSUM" | cut -d' ' -f1)
echo "  ✓ sha256:${CHECKSUM}"

cat <<INSTRUCTIONS

════════════════════════════════════════════════════════════
  MIrAI v${NEW_VERSION} — Instructions de déploiement
════════════════════════════════════════════════════════════

1. Commit & push :
   git add oxt/description.xml dm-manifest.json oxt/registration/license.txt
   git commit -m "release: v${NEW_VERSION}"
   git push

2. Déploiement canary (intégration) :
   ./scripts/deploy-release.sh \\
     --bootstrap-url https://bootstrap.fake-domain.name \\
     --strategy canary --profile int

3. Déploiement immédiat (production) :
   ./scripts/deploy-release.sh \\
     --bootstrap-url https://bootstrap.fake-domain.name \\
     --strategy immediate --profile prod

4. Suivi de campagne :
   curl -s -H "X-Admin-Token: \$DM_ADMIN_TOKEN" \\
     https://bootstrap.fake-domain.name/api/campaigns/latest/progress \\
     | python3 -m json.tool

5. Déploiement manuel (sans script) :
   Voir docs/DEPLOY.md

Checksum : sha256:${CHECKSUM}

════════════════════════════════════════════════════════════
INSTRUCTIONS
