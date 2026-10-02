"""Fichiers locaux : écritures atomiques, aucune valeur secrète sur disque."""

import json
import os
import stat

import pytest

from src.mirai import local_config


def test_redact_blanks_secrets_at_any_depth_and_keeps_keys():
    data = {"config": {"llmToken": "s1", "llm_base_urls": "https://x",
                       "keycloak": {"client_secret": "s2", "realm": "r"}},
            "meta": {"schema_version": 2}}
    redacted = local_config.redact_dm_config(data)
    assert redacted["config"]["llmToken"] == ""
    assert redacted["config"]["keycloak"]["client_secret"] == ""
    assert redacted["config"]["llm_base_urls"] == "https://x"
    assert redacted["config"]["keycloak"]["realm"] == "r"
    assert data["config"]["llmToken"] == "s1"          # l'original n'est pas modifié


def test_write_json_atomic_roundtrip_and_no_leftover(tmp_path):
    path = tmp_path / "sub" / "f.json"
    local_config.write_json_atomic(str(path), {"a": "é"})
    assert local_config.read_json(str(path)) == {"a": "é"}
    assert [n for n in os.listdir(path.parent) if n.startswith(".tmp-")] == []


@pytest.mark.skipif(os.name != "posix", reason="droits POSIX")
def test_write_json_atomic_is_private(tmp_path):
    path = tmp_path / "sub" / "f.json"
    local_config.write_json_atomic(str(path), {})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_read_json_tolerates_garbage(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert local_config.read_json(str(bad)) == {}
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert local_config.read_json(str(listing)) == {}
    assert local_config.read_json(str(tmp_path / "absent.json")) == {}
