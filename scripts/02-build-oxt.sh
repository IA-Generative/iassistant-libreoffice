#!/usr/bin/env bash
# Build LibreOffice OXT package for mirai

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXTENSION_NAME="mirai"

INSTALL_AFTER_BUILD=false
RESTART_LIBREOFFICE=false
OUTPUT_PATH="$ROOT_DIR/dist/${EXTENSION_NAME}.oxt"
CONFIG_PATH=""

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Options:
  --install                 Install extension after build (unopkg)
  --restart                 Restart LibreOffice after install
  --config <path>           Config file to embed as config.default.json
  --output <path>           Output OXT file path (default: ./dist/mirai.oxt)
  -h, --help                Show this help
USAGE
}

log() { printf '%s\n' "$*"; }
# stderr : warn() est appelé depuis resolve_config_path, dont la sortie stdout
# est capturée par $(...) — un warning sur stdout corromprait le chemin résolu.
warn() { printf 'WARNING: %s\n' "$*" >&2; }
err() { printf 'ERROR: %s\n' "$*" >&2; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    err "Command not found: $1"
    exit 1
  }
}

resolve_config_path() {
  if [ -n "$CONFIG_PATH" ]; then
    [ -f "$CONFIG_PATH" ] || { err "Config file not found: $CONFIG_PATH"; exit 1; }
    printf '%s' "$CONFIG_PATH"
    return
  fi

  local prod="$ROOT_DIR/config/config.default.json"
  local example="$ROOT_DIR/config/config.default.example.json"

  if [ -f "$prod" ]; then
    printf '%s' "$prod"
  elif [ -f "$example" ]; then
    warn "config/config.default.json missing, using config.default.example.json"
    printf '%s' "$example"
  else
    err "No config.default found (config/config.default.json or config/config.default.example.json)"
    exit 1
  fi
}

find_unopkg() {
  local os_name="$1"
  local unopkg_bin
  unopkg_bin="$(command -v unopkg || true)"
  if [ -n "$unopkg_bin" ]; then
    printf '%s' "$unopkg_bin"
    return
  fi

  if [ "$os_name" = "Darwin" ] && [ -x "/Applications/LibreOffice.app/Contents/MacOS/unopkg" ]; then
    printf '%s' "/Applications/LibreOffice.app/Contents/MacOS/unopkg"
    return
  fi

  if [ "$os_name" = "Linux" ]; then
    for candidate in /usr/lib/libreoffice/program/unopkg /usr/bin/unopkg /snap/bin/unopkg; do
      [ -x "$candidate" ] && { printf '%s' "$candidate"; return; }
    done
  fi

  for candidate in \
    "/c/Program Files/LibreOffice/program/unopkg.com" \
    "/c/Program Files/LibreOffice/program/unopkg.exe" \
    "/c/Program Files (x86)/LibreOffice/program/unopkg.com" \
    "/c/Program Files (x86)/LibreOffice/program/unopkg.exe"; do
    [ -x "$candidate" ] && { printf '%s' "$candidate"; return; }
  done

  printf ''
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install)
      INSTALL_AFTER_BUILD=true
      shift
      ;;
    --restart)
      RESTART_LIBREOFFICE=true
      shift
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      [ -n "$CONFIG_PATH" ] || { err "Missing value for --config"; exit 1; }
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      [ -n "$OUTPUT_PATH" ] || { err "Missing value for --output"; exit 1; }
      # Normalize to absolute: the zip step runs after `cd "$STAGE_DIR"`, so a
      # relative path would land in the temp dir and be lost on cleanup.
      case "$OUTPUT_PATH" in
        /*) : ;;
        *)  OUTPUT_PATH="$PWD/$OUTPUT_PATH" ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

require_cmd zip

[ -d "$ROOT_DIR/oxt" ] || { err "Missing directory: oxt"; exit 1; }
[ -d "$ROOT_DIR/src" ] || { err "Missing directory: src"; exit 1; }
[ -f "$ROOT_DIR/main.py" ] || { err "Missing file: main.py"; exit 1; }

CONFIG_IN_USE="$(resolve_config_path)"

mkdir -p "$(dirname "$OUTPUT_PATH")"
if [ -f "$OUTPUT_PATH" ]; then
  log "Removing previous package: $OUTPUT_PATH"
  rm -f "$OUTPUT_PATH"
fi

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

cp -R "$ROOT_DIR/oxt/." "$STAGE_DIR/"
cp "$ROOT_DIR/main.py" "$STAGE_DIR/main.py"
cp -R "$ROOT_DIR/src" "$STAGE_DIR/src"
cp "$CONFIG_IN_USE" "$STAGE_DIR/config.default.json"
# ── Anti-fuite : la config embarquée d'un profil ONLINE doit être transport-only ──
# (bootstrap_urls/config_path/enabled). Aucun SSO/keycloak/LLM baké : ils sont servis
# par le DM au runtime. Un profil offline (enabled:false) est exempté (pas de DM).
python3 - "$STAGE_DIR/config.default.json" <<'PY' || { err "Embedded config.default.json leaks non-transport keys (see above)"; exit 1; }
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
allowed = {"configVersion", "enabled", "bootstrap_urls", "bootstrap_url", "bootstrap_insecure_urls", "config_path", "_note", "_description"}
if cfg.get("enabled") is False:
    sys.exit(0)  # offline tier: baking local config is legitimate
leaked = sorted(k for k in cfg if k not in allowed)
if leaked:
    sys.stderr.write("LEAK: clés non-transport bakées dans config.default.json: %s\n" % ", ".join(leaked))
    sys.exit(1)
print("✓ embedded config.default.json: transport-only (no SSO/keycloak/LLM leak)")
PY

# ── Anti-fuite (2/2) : aucun nom d'hôte d'infrastructure interne dans ce qui est
# VERSIONNÉ. Le contrôle ci-dessus n'inspecte que les clés du config embarqué ;
# il ne voyait donc pas les URLs internes présentes dans config/profiles/, docs/,
# tests/ ou prompts/ — trois d'entre elles étaient parties sur GitHub.
python3 - "$ROOT_DIR" <<'PY' || { err "Des noms d'hôtes internes sont présents dans des fichiers suivis par git"; exit 1; }
import re, subprocess, sys

root = sys.argv[1]
# Domaines internes qui ne doivent JAMAIS être committés. Les placeholders
# (.example, .internal.example, change-me, <HOTE...>) restent autorisés.
FORBIDDEN = re.compile(r"\b[a-z0-9.-]+\.(minint|interieur)\.(fr|gouv\.fr)\b", re.I)
ALLOWED_SUBSTRINGS = ("mirai.interieur.gouv.fr",)   # portail public, documenté

tracked = subprocess.run(["git", "-C", root, "ls-files"],
                         capture_output=True, text=True).stdout.split()
offenders = []
for rel in tracked:
    if rel.startswith(("prompts/plan-", "docs/QUALIFICATION-")):
        continue          # documents de travail : décrivent le problème, sans le reproduire
    try:
        with open(f"{root}/{rel}", encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeDecodeError):
        continue
    for match in FORBIDDEN.finditer(content):
        host = match.group(0)
        if any(allowed in host for allowed in ALLOWED_SUBSTRINGS):
            continue
        line = content[:match.start()].count("\n") + 1
        offenders.append(f"{rel}:{line} → {host}")

if offenders:
    sys.stderr.write("LEAK: noms d'hôtes internes dans des fichiers versionnés :\n")
    for item in sorted(set(offenders)):
        sys.stderr.write(f"  {item}\n")
    sys.stderr.write("Remplacez-les par des placeholders (.internal.example, <HOTE_...>)\n")
    sys.exit(1)
print("✓ aucun nom d'hôte interne dans les fichiers versionnés")
PY
# Calc functions reference for formula generation
mkdir -p "$STAGE_DIR/config"
if [ -f "$ROOT_DIR/config/calc-functions.json" ]; then
  cp "$ROOT_DIR/config/calc-functions.json" "$STAGE_DIR/config/calc-functions.json"
fi
# ── Device Management packaging ──────────────────────────────────────────
# Sync version from description.xml into dm-manifest.json
OXT_VERSION=$(sed -n 's/.*<version value="\([^"]*\)".*/\1/p' "$STAGE_DIR/description.xml" 2>/dev/null || echo "")
OXT_IDENTIFIER=$(sed -n 's/.*<identifier value="\([^"]*\)".*/\1/p' "$STAGE_DIR/description.xml" 2>/dev/null || echo "")

# dm-manifest.json — plugin metadata for DM auto-registration
if [ -f "$ROOT_DIR/dm-manifest.json" ]; then
  if [ -n "$OXT_VERSION" ]; then
    # Inject current version into manifest changelog[0].version if it differs
    python3 -c "
import json, sys
with open('$ROOT_DIR/dm-manifest.json') as f:
    m = json.load(f)
m['version'] = '$OXT_VERSION'
if '$OXT_IDENTIFIER':
    m['identifier'] = '$OXT_IDENTIFIER'
with open('$STAGE_DIR/dm-manifest.json', 'w') as f:
    json.dump(m, f, indent=2, ensure_ascii=False)
    f.write('\n')
" 2>/dev/null || cp "$ROOT_DIR/dm-manifest.json" "$STAGE_DIR/dm-manifest.json"
  else
    cp "$ROOT_DIR/dm-manifest.json" "$STAGE_DIR/dm-manifest.json"
  fi
fi

# dm-config.json — config template for DM (default + profiles)
if [ -f "$ROOT_DIR/dm-config.json" ]; then
  cp "$ROOT_DIR/dm-config.json" "$STAGE_DIR/dm-config.json"
fi

# ── Mécanisme natif de MAJ LibreOffice (<update-information>, issue #5) ──
# Bake l'URL du feed update.xml (par tier, dérivée du profil bootstrap embarqué)
# dans description.xml : le bouton « Vérifier les mises à jour » du Gestionnaire
# des extensions détecte/télécharge/installe alors en pur in-process (pas de
# cmd.exe — immunisé WinError 5). Override : MIRAI_UPDATE_FEED_URL.
python3 "$ROOT_DIR/scripts/inject_update_feed.py" \
  "$STAGE_DIR/description.xml" "$STAGE_DIR/config.default.json" \
  || { err "injection <update-information> dans description.xml échouée"; exit 1; }

# Documentation — README, notice, licence
mkdir -p "$STAGE_DIR/docs"
[ -f "$ROOT_DIR/README.md" ] && cp "$ROOT_DIR/README.md" "$STAGE_DIR/docs/README.md"
[ -f "$ROOT_DIR/docs/notice-utilisateur.md" ] && cp "$ROOT_DIR/docs/notice-utilisateur.md" "$STAGE_DIR/docs/notice-utilisateur.md"
[ -f "$STAGE_DIR/registration/license.txt" ] && cp "$STAGE_DIR/registration/license.txt" "$STAGE_DIR/docs/license.txt"

find "$STAGE_DIR" -name ".DS_Store" -delete
find "$STAGE_DIR" -name "*.pyc" -delete
find "$STAGE_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} +

log "Creating package: $OUTPUT_PATH"
(
  cd "$STAGE_DIR"
  zip -r "$OUTPUT_PATH" . \
    -x "*.git*" -x "*.DS_Store" -x "*.pyc" -x "*__pycache__*"
) >/dev/null

log "OK: Package created: $OUTPUT_PATH"

# Verify DM-required files are present
_dm_ok=true
_oxt_files=$(unzip -l "$OUTPUT_PATH" 2>/dev/null)
for _check in "dm-manifest.json" "dm-config.json" "description.xml" "assets/logo.png" "docs/README.md" "docs/notice-utilisateur.md" "docs/license.txt" "registration/license.txt" "config/calc-functions.json"; do
  if ! echo "$_oxt_files" | grep -qF "$_check"; then
    warn "Missing in package: $_check"
    _dm_ok=false
  fi
done
if [ "$_dm_ok" = true ]; then
  log "DM packaging: all required files present"
fi
[ -n "$OXT_VERSION" ] && log "Version: $OXT_VERSION"

if [ "$INSTALL_AFTER_BUILD" != true ]; then
  exit 0
fi

OS_NAME="$(uname -s)"
if [ "$OS_NAME" = "Darwin" ]; then
  osascript -e 'tell application "LibreOffice" to quit' >/dev/null 2>&1 || true
elif [ "$OS_NAME" = "Linux" ]; then
  pkill -f soffice.bin >/dev/null 2>&1 || true
  pkill -f soffice >/dev/null 2>&1 || true
else
  taskkill //IM soffice.bin //F >/dev/null 2>&1 || true
  taskkill //IM soffice.exe //F >/dev/null 2>&1 || true
fi

UNOPKG_BIN="$(find_unopkg "$OS_NAME")"
[ -n "$UNOPKG_BIN" ] || { err "unopkg not found"; exit 1; }

# Lock résiduel : si aucun LibreOffice ne tourne mais qu'un .lock traîne
# (session précédente crashée), unopkg refuse de démarrer. On le retire.
for LOCK_FILE in \
  "$HOME/Library/Application Support/LibreOffice/4/.lock" \
  "$HOME/.config/libreoffice/4/.lock"; do
  if [ -f "$LOCK_FILE" ] && ! pgrep -f "soffice" >/dev/null 2>&1; then
    warn "stale LibreOffice lock removed: $LOCK_FILE"
    rm -f "$LOCK_FILE"
  fi
done

log "Installing extension via unopkg..."
# NB: certaines versions d'unopkg ne connaissent pas --replace ; -f (force) écrase.
if ! "$UNOPKG_BIN" add -f "$OUTPUT_PATH" >/dev/null 2>&1; then
  warn "add -f failed, fallback to remove + add"
  "$UNOPKG_BIN" remove "fr.gouv.interieur.mirai" >/dev/null 2>&1 || true
  printf "yes\n" | "$UNOPKG_BIN" add "$OUTPUT_PATH"
fi

log "OK: Extension installed"
if [ "$RESTART_LIBREOFFICE" = true ] && [ "$OS_NAME" = "Darwin" ]; then
  log "Restarting LibreOffice..."
  open -a "LibreOffice"
fi
