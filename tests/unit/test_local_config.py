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


def _cfg(tmp_path, package=None):
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    if package is not None:
        (pkg / "config.default.json").write_text(json.dumps(package), encoding="utf-8")
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    return local_config.LocalConfig(str(user), [str(pkg / "config.default.json")])


def test_transport_comes_from_package_when_it_defines_any(tmp_path):
    cfg = _cfg(tmp_path, {"enabled": True, "bootstrap_urls": ["https://new"]})
    cfg.update({"bootstrap_urls": ["https://old"], "config_path": "/stale",
                "bootstrap_insecure_urls": ["old.example"]})
    assert cfg.get("bootstrap_urls") == ["https://new"]
    assert cfg.get("config_path", "/default") == "/default"
    assert cfg.get("bootstrap_insecure_urls") is None


def test_transport_falls_back_to_settings_without_package(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.update({"enabled": True, "bootstrap_url": "https://dev"})
    assert cfg.get("enabled") is True
    assert cfg.get("bootstrap_url") == "https://dev"


def test_precedence_settings_then_snapshot_then_package(tmp_path):
    cfg = _cfg(tmp_path, {"systemPrompt": "pkg", "doc_url": "pkg-doc",
                          "portal_url": "pkg-portal"})
    cfg.save_dm_snapshot({"config": {"systemPrompt": "dm", "doc_url": "dm-doc",
                                     "portal_url": ""}})
    cfg.set("systemPrompt", "user")
    assert cfg.get("systemPrompt") == "user"
    assert cfg.get("doc_url") == "dm-doc"
    assert cfg.get("portal_url") == "pkg-portal"


def test_local_only_and_secret_keys_never_come_from_dm(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.save_dm_snapshot({"config": {"proxy_allow_insecure_ssl": True,
                                     "ca_bundle_path": "/x", "llmToken": "SECRET"}})
    assert cfg.get("proxy_allow_insecure_ssl", False) is False
    assert cfg.get("ca_bundle_path", "") == ""
    assert cfg.get("llmToken", "") == ""


def test_snapshot_is_replaced_wholesale(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.save_dm_snapshot({"config": {"systemPrompt": "v1", "doc_url": "d"}})
    cfg.save_dm_snapshot({"config": {"doc_url": "d2"}})
    assert cfg.get("systemPrompt", "absent") == "absent"
    assert cfg.get("doc_url") == "d2"


def test_snapshot_has_no_secret_and_keeps_extra_settings(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.save_dm_snapshot({"config": {"llmToken": "SECRET-1"}},
                         extra_settings={"keycloakRealm": "mirai",
                                         "keycloak_client_secret": "SECRET-2"})
    raw = (tmp_path / "user" / "mirai" / "dm_snapshot.json").read_text(encoding="utf-8")
    assert "SECRET" not in raw
    assert cfg.get("keycloakRealm") == "mirai"
    assert "llmToken" in cfg.dm_settings()


def test_proxy_password_sent_by_the_dm_is_blank_on_disk(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.save_dm_snapshot({"config": {"proxy_url": "http://p:3128",
                                     "proxy_password": "SECRET-pw"}})
    raw = (tmp_path / "user" / "mirai" / "dm_snapshot.json").read_text(encoding="utf-8")
    assert "SECRET" not in raw
    assert cfg.dm_settings()["proxy_password"] == ""


def test_update_and_remove(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.update({"a": 1, "b": 2})
    cfg.update({"c": 3}, remove=("a",))
    assert cfg.settings() == {"b": 2, "c": 3}


def test_transport_scope_tracks_transport(tmp_path):
    cfg = _cfg(tmp_path, {"enabled": True, "bootstrap_urls": ["https://a"]})
    first = cfg.transport_scope()
    (tmp_path / "pkg" / "config.default.json").write_text(
        json.dumps({"enabled": True, "bootstrap_urls": ["https://b"]}), encoding="utf-8")
    assert cfg.transport_scope() != first
    assert len(first) == 16


def _scope(tmp_path, transport):
    return _cfg(tmp_path, transport).transport_scope()


BASE_TRANSPORT = {"enabled": True,
                  "bootstrap_urls": ["https://dm-a.example/mirai", "https://dm-b.example"],
                  "config_path": "/config/mirai/config.json?profile=prod"}


def test_transport_scope_ignores_spelling_order_and_duplicates(tmp_path):
    first = _scope(tmp_path, BASE_TRANSPORT)
    assert _scope(tmp_path, dict(BASE_TRANSPORT, bootstrap_urls=[
        "HTTPS://DM-B.example/", "https://dm-a.example/mirai/",
        "https://dm-b.example"])) == first
    assert _scope(tmp_path, dict(BASE_TRANSPORT, bootstrap_urls=["https://dm-b.example"],
                                 bootstrap_url="https://dm-a.example/mirai")) == first


def test_transport_scope_ignores_tls_exceptions_and_the_enabled_flag(tmp_path):
    first = _scope(tmp_path, BASE_TRANSPORT)
    assert _scope(tmp_path, dict(BASE_TRANSPORT,
                                 bootstrap_insecure_urls=["dm-a.example"])) == first
    assert _scope(tmp_path, dict(BASE_TRANSPORT, enabled=False)) == first


def test_transport_scope_changes_with_the_target_environment(tmp_path):
    first = _scope(tmp_path, BASE_TRANSPORT)
    assert _scope(tmp_path, dict(BASE_TRANSPORT, bootstrap_urls=[
        "https://dm-a.example/mirai", "https://dm-c.example"])) != first
    assert _scope(tmp_path, dict(BASE_TRANSPORT, bootstrap_urls=[
        "https://dm-a.example/autre", "https://dm-b.example"])) != first
    assert _scope(tmp_path, dict(
        BASE_TRANSPORT, config_path="/config/mirai/config.json?profile=int")) != first


def test_without_user_config_dir_nothing_is_written(tmp_path):
    cfg = local_config.LocalConfig("", [str(tmp_path / "config.default.json")])
    cfg.set("a", 1)
    cfg.save_dm_snapshot({"config": {"b": 2}})
    assert cfg.get("a", "none") == "none"
    assert os.listdir(tmp_path) == []
