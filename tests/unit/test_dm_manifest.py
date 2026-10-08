"""Le dm-manifest.json embarqué : changelog dérivé de CHANGELOG.md (release-please),
historique d'avant release-please conservé, version de description.xml."""

import importlib.util
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_spec = importlib.util.spec_from_file_location(
    "dm_manifest", os.path.join(ROOT, "scripts", "dm_manifest.py"))
dm_manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm_manifest)

CHANGELOG = """# Changelog

## [0.4.0](https://github.com/IA-Generative/iassistant-libreoffice/compare/v0.3.0...v0.4.0) (2026-11-02)


### Features

* **palette:** chips Correct and Translate ([#81](https://github.com/IA-Generative/iassistant-libreoffice/issues/81)) ([1a2b3c4](https://github.com/IA-Generative/iassistant-libreoffice/commit/1a2b3c4d))


### Bug Fixes

* cancel is no longer reported as a network error ([5d6e7f8](https://github.com/IA-Generative/iassistant-libreoffice/commit/5d6e7f80)), closes [#73](https://github.com/IA-Generative/iassistant-libreoffice/issues/73)

## 0.3.0 (2026-10-10)


### Features

* releases with release-please ([9a8b7c6](https://github.com/IA-Generative/iassistant-libreoffice/commit/9a8b7c6d))
"""


def test_each_release_becomes_an_entry_newest_first():
    entries = dm_manifest.changelog_from_markdown(CHANGELOG)
    assert [(e["version"], e["date"]) for e in entries] == [
        ("0.4.0", "2026-11-02"), ("0.3.0", "2026-10-10")]


def test_changes_are_plain_sentences():
    entries = dm_manifest.changelog_from_markdown(CHANGELOG)
    assert entries[0]["changes"] == [
        "chips Correct and Translate",
        "cancel is no longer reported as a network error",
    ]
    assert entries[1]["changes"] == ["releases with release-please"]


def test_history_before_release_please_follows_the_derived_entries():
    manifest = {"slug": "mirai-libreoffice",
                "changelog": [{"version": "0.0.1.0.32", "date": "2026-10-02", "changes": ["x"]}]}
    derived = dm_manifest.changelog_from_markdown(CHANGELOG)
    out = dm_manifest.build_manifest(manifest, "0.4.0", "fr.gouv.interieur.mirai", derived)
    assert [e["version"] for e in out["changelog"]] == ["0.4.0", "0.3.0", "0.0.1.0.32"]
    assert out["version"] == "0.4.0"
    assert out["identifier"] == "fr.gouv.interieur.mirai"


def test_without_changelog_the_history_is_kept(tmp_path):
    source = tmp_path / "dm-manifest.json"
    source.write_text(json.dumps({"changelog": [{"version": "0.0.1.0.32", "changes": ["x"]}]}))
    target = tmp_path / "out.json"
    dm_manifest.main([str(source), str(target), "0.0.1.0.32", "", str(tmp_path / "absent.md")])
    out = json.loads(target.read_text())
    assert [e["version"] for e in out["changelog"]] == ["0.0.1.0.32"]
    assert "identifier" not in out
