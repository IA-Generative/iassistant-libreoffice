#!/usr/bin/env bash
# Numéro d'une pré-version : X.Y.Z-rc.N (candidate) ou X.Y.Z-dev.N (essai).
#
# X.Y.Z est la prochaine version de master :
#   - celle qu'annonce la PR release-please ouverte vers master, lue dans le
#     manifeste release-please de sa branche ;
#   - sans PR ouverte, la version du manifeste de master, mineure + 1.
#
# Usage : GITHUB_REPOSITORY=org/depot scripts/prerelease-version.sh rc|dev N
# Demande `gh` (jeton en lecture : contents, pull-requests) et python3.
# Le numéro part sur la sortie standard, l'origine de X.Y.Z sur la sortie d'erreur.
set -euo pipefail

USAGE="usage: prerelease-version.sh rc|dev N"
KIND="${1:?$USAGE}"
NUMBER="${2:?$USAGE}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY attendu (org/depot)}"
RELEASE_BRANCH="master"
MANIFEST=".release-please-manifest.json"
PR_BRANCH_PREFIX="release-please--branches--$RELEASE_BRANCH"

fail() { echo "$*" >&2; exit 1; }

case "$KIND" in rc|dev) ;; *) fail "Nature inconnue « $KIND » : rc ou dev." ;; esac
case "$NUMBER" in ''|*[!0-9]*) fail "N invalide « $NUMBER »." ;; esac

# Version du paquet racine (« . ») dans le manifeste release-please d'une branche.
manifest_version() {
  gh api -X GET "repos/$REPO/contents/$MANIFEST" -f ref="$1" \
      -H "Accept: application/vnd.github.raw" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["."])'
}

is_release_version() { printf '%s' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; }

# Seule une branche du dépôt compte : la PR d'un fork ne fixe pas la version.
PR_BRANCH="$(gh pr list --repo "$REPO" --state open --base "$RELEASE_BRANCH" --limit 100 \
  --json headRefName,isCrossRepository \
  --jq "[.[] | select(.isCrossRepository | not) | .headRefName
         | select(startswith(\"$PR_BRANCH_PREFIX\"))][0] // \"\"")"

if [ -n "$PR_BRANCH" ]; then
  NEXT="$(manifest_version "$PR_BRANCH")"
  echo "Prochaine version $NEXT : PR release-please ouverte ($PR_BRANCH)." >&2
else
  CURRENT="$(manifest_version "$RELEASE_BRANCH")"
  is_release_version "$CURRENT" || fail "Version de $RELEASE_BRANCH illisible : « $CURRENT »."
  MAJOR="${CURRENT%%.*}"
  REST="${CURRENT#*.}"
  NEXT="$MAJOR.$(( ${REST%%.*} + 1 )).0"
  echo "Prochaine version $NEXT : pas de PR release-please ouverte, $RELEASE_BRANCH est en $CURRENT (mineure + 1)." >&2
fi

is_release_version "$NEXT" || fail "Prochaine version illisible : « $NEXT »."
echo "$NEXT-$KIND.$NUMBER"
