"""Empreinte d'installation : une mise à jour garde tout sauf les caches dérivés ;
un nouvel environnement (transport) oblige à se ré-enrôler."""

import os

from src.mirai import local_config
from tests.stubs.helpers import refuse_settings_writes
from tests.stubs.uno_stubs import install, make_job, read_user_config, seed_user_config

install()

from src.mirai.shell import runtime


def _snapshot_removal_refused(monkeypatch):
    """Suppression de dm_snapshot.json refusée tant que `held[0]` est vrai,
    comme sous Windows quand un autre programme tient le fichier."""
    real = os.remove
    held = [True]

    def remove(path):
        if held[0] and os.path.basename(path) == local_config.SNAPSHOT_FILE:
            raise PermissionError("[WinError 5] Accès refusé")
        return real(path)

    monkeypatch.setattr(os, "remove", remove)
    return held


def _record(cfg, version, package):
    applied = []
    cfg.record_install(version, package, applied.append)
    return applied[0]


def test_record_install_reports_changes(tmp_path):
    cfg = local_config.LocalConfig(str(tmp_path), [])
    assert _record(cfg, "1", "/pkg/a") == {"first_run"}
    assert _record(cfg, "1", "/pkg/a") == set()
    assert _record(cfg, "2", "/pkg/b") == {"version", "package"}
    cfg.set("bootstrap_url", "https://autre")
    assert _record(cfg, "2", "/pkg/b") == {"transport"}


def test_upgrade_keeps_settings_identity_and_credentials_drops_caches(tmp_path):
    seed_user_config(str(tmp_path), {
        "plugin_uuid": "u-1", "llm_default_models": "m", "relay_client_key": "rk",
        "assistant_model_capabilities": "{}", "llm_tool_mode_detected": "json"})
    job = make_job(config_dir=str(tmp_path))
    job._local_config().save_dm_snapshot({"config": {"doc_url": "d"}})
    job._apply_install_changes({"version", "package"})
    settings = read_user_config(str(tmp_path))
    assert settings["plugin_uuid"] == "u-1"
    assert settings["llm_default_models"] == "m"
    assert job._get_config_from_file("relay_client_key", "") == "rk"
    assert "assistant_model_capabilities" not in settings
    assert "llm_tool_mode_detected" not in settings
    assert job._local_config().snapshot() == {}


def test_new_environment_drops_bound_credentials(tmp_path):
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "enrolled": True,
                                     "relay_client_id": "rc", "refresh_token": "rt"})
    job = make_job(config_dir=str(tmp_path))
    job._apply_install_changes({"transport"})
    settings = read_user_config(str(tmp_path))
    assert settings["plugin_uuid"] == "u-1"
    assert "enrolled" not in settings
    assert job._get_config_from_file("relay_client_id", "") == ""
    assert job._get_config_from_file("refresh_token", "") == ""


def test_refused_purge_after_an_environment_change_is_retried(tmp_path, monkeypatch):
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "enrolled": True,
                                     "bootstrap_url": "https://dm-a.example"})
    make_job(config_dir=str(tmp_path))
    # Nouveau processus visant un autre DM : sa première purge est refusée.
    runtime.reset_for_tests()
    local_config.LocalConfig(str(tmp_path), []).set("bootstrap_url", "https://dm-b.example")
    refuse_settings_writes(monkeypatch, refusals=1)
    make_job(config_dir=str(tmp_path))
    make_job(config_dir=str(tmp_path))
    assert "enrolled" not in read_user_config(str(tmp_path))


def test_refused_snapshot_removal_after_an_environment_change_is_retried(tmp_path, monkeypatch,
                                                                         fast_clock):
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "bootstrap_url": "https://dm-a.example"})
    make_job(config_dir=str(tmp_path))
    local_config.LocalConfig(str(tmp_path), []).save_dm_snapshot({"config": {"doc_url": "a"}})
    # Nouveau processus visant un autre DM : l'instantané ne peut pas être supprimé.
    runtime.reset_for_tests()
    local_config.LocalConfig(str(tmp_path), []).set("bootstrap_url", "https://dm-b.example")
    held = _snapshot_removal_refused(monkeypatch)
    make_job(config_dir=str(tmp_path))
    held[0] = False
    make_job(config_dir=str(tmp_path))
    assert local_config.LocalConfig(str(tmp_path), []).snapshot() == {}


def test_first_run_changes_nothing(tmp_path):
    seed_user_config(str(tmp_path), {"llm_tool_mode_detected": "json"})
    job = make_job(config_dir=str(tmp_path))
    job._apply_install_changes({"first_run"})
    assert read_user_config(str(tmp_path))["llm_tool_mode_detected"] == "json"


def test_stale_last_bootstrap_url_is_ignored(tmp_path):
    seed_user_config(str(tmp_path), {"bootstrap_urls": ["https://new"],
                                     "last_bootstrap_url": "https://old"})
    job = make_job(config_dir=str(tmp_path))
    assert job._active_bootstrap_url() == "https://new"
