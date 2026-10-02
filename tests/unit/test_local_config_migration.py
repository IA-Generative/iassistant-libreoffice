"""Migration de l'ancienne disposition (config.json + fichiers épars)."""

import json

from src.mirai import local_config


def _setup(tmp_path, legacy, package=None):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.json").write_text(json.dumps(legacy), encoding="utf-8")
    pkg = tmp_path / "config.default.json"
    if package is not None:
        pkg.write_text(json.dumps(package), encoding="utf-8")
    return local_config.LocalConfig(str(user), [str(pkg)]), user


LEGACY = {
    "extensionUUID": "u-1", "plugin_uuid": "u-1", "enrolled": True,
    "relay_client_id": "rc", "relay_client_key": "rk", "relay_key_expires_at": 9,
    "refresh_token": "rt", "access_token": "at", "llm_api_tokens": "tok",
    "llmTokenExpiresAt": 5, "telemetryKey": "tk",
    "bootstrap_urls": ["https://old"], "config_path": "/old", "enabled": True,
    "systemPrompt": "dm", "keycloakRealm": "r", "llm_default_models": "m",
    "assistant_active_tab": "actions", "proxy_url": "http://p:3128",
    "proxy_allow_insecure_ssl": True,
}


def test_keeps_identity_prefs_and_long_lived_credentials_only(tmp_path):
    cfg, user = _setup(tmp_path, LEGACY, {"enabled": True, "bootstrap_urls": ["https://new"]})
    cfg.migrate_legacy(str(tmp_path))
    settings = cfg.settings()
    for key in ("extensionUUID", "plugin_uuid", "enrolled", "relay_client_id",
                "relay_client_key", "relay_key_expires_at", "refresh_token",
                "llm_default_models", "assistant_active_tab", "proxy_url",
                "proxy_allow_insecure_ssl"):
        assert key in settings, key
    for key in ("access_token", "llm_api_tokens", "llmTokenExpiresAt", "telemetryKey",
                "bootstrap_urls", "config_path", "enabled", "systemPrompt", "keycloakRealm"):
        assert key not in settings, key
    stub = json.loads((user / "config.json").read_text(encoding="utf-8"))
    assert stub == {"extensionUUID": "u-1", "plugin_uuid": "u-1"}


def test_offline_tier_keeps_the_llm_endpoint_and_key(tmp_path):
    cfg, _user = _setup(tmp_path, dict(LEGACY, enabled=False,
                                       llm_base_urls="http://localhost:11434/v1"),
                        {"enabled": False, "bootstrap_urls": []})
    cfg.migrate_legacy(str(tmp_path))
    assert cfg.settings()["llm_base_urls"] == "http://localhost:11434/v1"
    assert cfg.settings()["llm_api_tokens"] == "tok"


def test_reload_footprint_drops_the_insecure_tls_flag(tmp_path):
    cfg, _user = _setup(tmp_path, dict(LEGACY, api_type="chat", is_openwebui=True),
                        {"enabled": True})
    cfg.migrate_legacy(str(tmp_path))
    assert "proxy_allow_insecure_ssl" not in cfg.settings()


def test_newer_settings_win_on_re_migration(tmp_path):
    cfg, user = _setup(tmp_path, LEGACY, {"enabled": True})
    cfg.migrate_legacy(str(tmp_path))
    cfg.set("llm_default_models", "choix-recent")
    (user / "config.json").write_text(json.dumps(dict(LEGACY, llm_default_models="vieux")),
                                      encoding="utf-8")
    cfg.migrate_legacy(str(tmp_path))
    assert cfg.settings()["llm_default_models"] == "choix-recent"


def test_moves_scattered_files_and_pending_update(tmp_path):
    cfg, user = _setup(tmp_path, {"plugin_uuid": "u"}, {"enabled": True})
    (user / "prompts_calc.txt").write_text("p\n", encoding="utf-8")
    (user / "assistant_conversation.json").write_text("{}", encoding="utf-8")
    (user / "config_cache.json").write_text("{}", encoding="utf-8")
    (user / "pending_update").mkdir()
    (user / "pending_update" / "update_state.json").write_text('{"stage": "staged"}',
                                                               encoding="utf-8")
    cfg.migrate_legacy(str(tmp_path))
    data = user / "mirai"
    assert (data / "prompts_calc.txt").read_text(encoding="utf-8") == "p\n"
    assert (data / "assistant_conversation.json").exists()
    assert (data / "pending_update" / "update_state.json").exists()
    assert not (user / "prompts_calc.txt").exists()
    assert not (user / "config_cache.json").exists()
    assert not (user / "pending_update").exists()


def test_legacy_copy_written_after_a_rollback_replaces_the_moved_one(tmp_path):
    # Retour arrière du DM : la version antérieure écrit à l'ancien emplacement
    # après la migration (file de télémétrie, nouvelle mise à jour stagée).
    cfg, user = _setup(tmp_path, {"plugin_uuid": "u"}, {"enabled": True})
    data = user / "mirai"
    (data / "pending_update").mkdir(parents=True)
    (data / "pending_update" / "update_state.json").write_text('{"target": "1"}',
                                                               encoding="utf-8")
    (data / "pending_update" / "ancien.oxt").write_bytes(b"x")
    (data / "telemetry_queue.json").write_text('["avant"]', encoding="utf-8")
    (user / "telemetry_queue.json").write_text('["rollback"]', encoding="utf-8")
    (user / "pending_update").mkdir()
    (user / "pending_update" / "update_state.json").write_text('{"target": "2"}',
                                                               encoding="utf-8")
    cfg.migrate_legacy(str(tmp_path))
    assert (data / "telemetry_queue.json").read_text(encoding="utf-8") == '["rollback"]'
    state = data / "pending_update" / "update_state.json"
    assert json.loads(state.read_text(encoding="utf-8")) == {"target": "2"}
    assert not (data / "pending_update" / "ancien.oxt").exists()
    assert not (user / "telemetry_queue.json").exists()
    assert not (user / "pending_update").exists()


def test_home_log_removed_only_when_it_is_ours(tmp_path):
    cfg, _user = _setup(tmp_path, {"plugin_uuid": "u"}, {"enabled": True})
    (tmp_path / "log.txt").write_text("2026 - DM config fetch attempt\n", encoding="utf-8")
    cfg.migrate_legacy(str(tmp_path))
    assert not (tmp_path / "log.txt").exists()
    (tmp_path / "log.txt").write_text("journal d'une autre application\n", encoding="utf-8")
    cfg.migrate_legacy(str(tmp_path))
    assert (tmp_path / "log.txt").exists()


def test_second_run_is_a_no_op(tmp_path):
    cfg, _user = _setup(tmp_path, LEGACY, {"enabled": True})
    cfg.migrate_legacy(str(tmp_path))
    assert cfg.migrate_legacy(str(tmp_path)) == []


def test_rollback_credentials_win_other_settings_do_not(tmp_path):
    cfg, user = _setup(tmp_path, {"plugin_uuid": "u"}, {"enabled": True})
    cfg.update({"relay_client_key": "ancienne", "llm_default_models": "choix-recent"})
    (user / "config.json").write_text(
        json.dumps({"plugin_uuid": "u", "relay_client_key": "nouvelle",
                    "llm_default_models": "vieux"}), encoding="utf-8")
    cfg.migrate_legacy(str(tmp_path))
    assert cfg.settings()["relay_client_key"] == "nouvelle"
    assert cfg.settings()["llm_default_models"] == "choix-recent"


def test_identity_stub_written_when_config_json_is_absent(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    cfg = local_config.LocalConfig(str(user), [])
    cfg.update({"extensionUUID": "u-9", "plugin_uuid": "u-9"})
    assert cfg.migrate_legacy(str(tmp_path)) != []
    assert json.loads((user / "config.json").read_text(encoding="utf-8")) == {
        "extensionUUID": "u-9", "plugin_uuid": "u-9"}
    assert cfg.migrate_legacy(str(tmp_path)) == []
