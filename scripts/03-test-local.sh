#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[1/6] Shell syntax checks"
for f in "$ROOT_DIR"/scripts/*.sh; do
  bash -n "$f"
done

echo "[2/6] Python syntax checks"
find "$ROOT_DIR/src" "$ROOT_DIR/main.py" -name '*.py' -print0 | xargs -0 python3 -m py_compile

for profile in docker kubernetes local-llm dev; do
  f="$ROOT_DIR/config/profiles/config.default.$profile.json"
  [ -f "$f" ] && python3 -m json.tool "$f" >/dev/null
done
# Les profils portant une URL réelle sont gitignorés : on valide leur exemple.
for f in "$ROOT_DIR"/config/profiles/*.example.json; do
  [ -f "$f" ] && python3 -m json.tool "$f" >/dev/null
done

echo "[3/6] Lint (ruff)"
# Dégradation gracieuse : un poste sans ruff ne doit jamais être bloqué.
if python3 -m ruff --version >/dev/null 2>&1; then
  # Bloquant sur les erreurs franches (bugs, imports, style).
  python3 -m ruff check "$ROOT_DIR/src/mirai/core" "$ROOT_DIR/src/mirai/ui" \
    "$ROOT_DIR/tests" --ignore C901
  # La complexité est un budget en cours de résorption : on la MESURE et on
  # l'affiche, sans bloquer, pour que le chiffre reste sous les yeux.
  complex_count=$(python3 -m ruff check "$ROOT_DIR/src/mirai/core" "$ROOT_DIR/src/mirai/ui" \
    --select C901 --output-format concise 2>/dev/null | grep -c C901 || true)
  echo "    complexité > 10 : $complex_count fonction(s) — budget cible : 0"
else
  echo "    ruff absent — lint ignoré (installez-le avec : python3 -m pip install ruff)"
fi

echo "[4/6] Unit + integration tests"
python3 -m pytest -q "$ROOT_DIR/tests/unit" "$ROOT_DIR/tests/integration"

echo "[5/6] Build package"
"$ROOT_DIR/scripts/02-build-oxt.sh"

echo "[6/6] Verify archive content"
unzip -l "$ROOT_DIR/dist/mirai.oxt" | grep -E "config\.default\.json|src/mirai/entrypoint\.py|main\.py|META-INF/manifest\.xml" >/dev/null

echo "OK: local checks passed"
