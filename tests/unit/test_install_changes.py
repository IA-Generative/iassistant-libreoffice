"""Empreinte d'installation : une mise à jour garde tout sauf les caches dérivés ;
un nouvel environnement (transport) oblige à se ré-enrôler."""

from src.mirai import local_config
from tests.stubs.uno_stubs import install, make_job, read_user_config, seed_user_config

install()


def test_record_install_reports_changes(tmp_path):
    cfg = local_config.LocalConfig(str(tmp_path), [])
    assert cfg.record_install("1", "/pkg/a") == {"first_run"}
    assert cfg.record_install("1", "/pkg/a") == set()
    assert cfg.record_install("2", "/pkg/b") == {"version", "package"}
    cfg.set("bootstrap_url", "https://autre")
    assert cfg.record_install("2", "/pkg/b") == {"transport"}


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
