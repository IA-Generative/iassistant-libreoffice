#!/usr/bin/env python3
"""Produit le dm-manifest.json embarqué dans l'OXT, sans toucher au fichier suivi.

Le changelog affiché par le Device Management vient de CHANGELOG.md, tenu à
jour par release-please. Les entrées d'avant release-please restent dans le
dm-manifest.json suivi, comme historique figé, à la suite des entrées dérivées.
La version et l'identifiant viennent de description.xml.

Usage : dm_manifest.py <dm-manifest.json> <sortie> <version> <identifiant> <CHANGELOG.md>
"""
import json
import os
import re
import sys

# « ## [0.3.0](lien de comparaison) (2026-10-10) », ou « ## 0.3.0 (2026-10-10) »
# pour la première release, qui n'a pas de version précédente à comparer.
_HEADING = re.compile(r"^\[?([0-9][^\]\s()]*)\]?(?:\([^)]*\))?\s*\((\d{4}-\d{2}-\d{2})\)")
_SCOPE = re.compile(r"^\*\*[^*]+\*\*:?\s*")
_TRAILING_LINK = re.compile(r",?\s*(?:closes\s+)?\(?\[[^\]]+\]\([^)]+\)\)?\s*$")


def changelog_from_markdown(text):
    entries = []
    for block in re.split(r"\n## ", "\n" + text)[1:]:
        heading = _HEADING.match(block)
        if not heading:
            continue
        changes = []
        for line in block.splitlines()[1:]:
            if not line.startswith("* "):
                continue
            change = _SCOPE.sub("", line[2:])
            while _TRAILING_LINK.search(change):
                change = _TRAILING_LINK.sub("", change)
            if change.strip():
                changes.append(change.strip())
        if changes:
            entries.append({"version": heading.group(1), "date": heading.group(2),
                            "changes": changes})
    return entries


def build_manifest(manifest, version, identifier, derived):
    out = dict(manifest)
    out["version"] = version
    if identifier:
        out["identifier"] = identifier
    known = {entry["version"] for entry in derived}
    history = [entry for entry in manifest.get("changelog", [])
               if entry.get("version") not in known]
    out["changelog"] = derived + history
    return out


def main(argv):
    source, target, version, identifier, changelog_md = argv
    with open(source, encoding="utf-8") as fh:
        manifest = json.load(fh)
    derived = []
    if os.path.isfile(changelog_md):
        with open(changelog_md, encoding="utf-8") as fh:
            derived = changelog_from_markdown(fh.read())
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(build_manifest(manifest, version, identifier, derived), fh,
                  indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"dm-manifest.json : version {version}, {len(derived)} entrée(s) depuis CHANGELOG.md")


if __name__ == "__main__":
    main(sys.argv[1:])
