"""Empreinte d'installation : une mise à jour garde tout sauf les caches dérivés ;
un nouvel environnement (transport) oblige à se ré-enrôler."""

import os

from src.mirai import credentials, local_config
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
                                     "bootstrap_url": "https://dm-a.example",
                                     "relay_client_id": "rc", "refresh_token": "rt",
                                     "relay_dm_base": "https://dm-a.example"})
    job = make_job(config_dir=str(tmp_path))
    job._local_config().update({"bootstrap_url": "https://dm-b.example"})
    job._apply_install_changes({"transport"})
    settings = read_user_config(str(tmp_path))
    assert settings["plugin_uuid"] == "u-1"
    assert "enrolled" not in settings
    assert credentials.store().get("relay_client_id") is None
    assert credentials.store().get("refresh_token") is None
    assert credentials.store().get("relay_dm_base") is None


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


def test_a_retried_switch_erases_only_the_old_environment_credentials(tmp_path, monkeypatch):
    """Empreinte non enregistrée (écriture refusée) : la session s'enrôle sur le
    nouvel environnement, la reprise n'efface du coffre que ce qui venait de l'ancien."""
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "bootstrap_url": "https://dm-a.example",
                                     "refresh_token": "rt-a"})
    make_job(config_dir=str(tmp_path))
    # Nouveau processus visant un autre DM : sa première préparation est refusée.
    runtime.reset_for_tests()
    local_config.LocalConfig(str(tmp_path), []).set("bootstrap_url", "https://dm-b.example")
    refuse_settings_writes(monkeypatch, refusals=1)
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_id", "rc-b")
    make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("relay_client_id", "") == "rc-b"
    assert credentials.store().get("refresh_token") is None


def test_a_switch_drops_the_secrets_an_older_version_left_in_settings(tmp_path):
    """Un secret laissé dans settings.json par une version antérieure vient d'un
    environnement inconnu : il ne passe pas dans le coffre du nouveau."""
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "bootstrap_url": "https://dm-a.example"})
    make_job(config_dir=str(tmp_path))
    runtime.reset_for_tests()
    local_config.LocalConfig(str(tmp_path), []).update({"bootstrap_url": "https://dm-b.example",
                                                        "relay_client_key": "rk-a"})
    job = make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("relay_client_key", "") == ""
    assert "relay_client_key" not in read_user_config(str(tmp_path))


def _older_version_left_in_config_json(tmp_path, pair_id, pair_key):
    local_config.write_json_atomic(os.path.join(str(tmp_path), "config.json"),
                                   {"plugin_uuid": "u-1", "enrolled": True,
                                    "relay_client_id": pair_id, "relay_client_key": pair_key})


def test_a_retried_upgrade_keeps_the_pair_the_session_obtained_meanwhile(tmp_path, monkeypatch,
                                                                         fast_clock):
    """Retour arrière puis remise à niveau : la paire laissée par la version
    antérieure est rangée, la préparation échoue ensuite ; la paire que la session
    obtient alors n'est pas écrasée par la reprise."""
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "bootstrap_url": "https://dm.example",
                                     "relay_client_id": "A-id", "relay_client_key": "A-key"})
    make_job(config_dir=str(tmp_path))
    state_path = os.path.join(local_config.data_dir(str(tmp_path)), local_config.INSTALL_STATE_FILE)
    local_config.write_json_atomic(state_path, {**local_config.read_json(state_path),
                                                "version": "0.0.1"})
    _older_version_left_in_config_json(tmp_path, "B-id", "B-key")
    runtime.reset_for_tests()
    held = _snapshot_removal_refused(monkeypatch)
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_id", "C-id")
    job.set_config("relay_client_key", "C-key")
    held[0] = False
    make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("relay_client_id", "") == "C-id"
    assert job._get_config_from_file("relay_client_key", "") == "C-key"


def test_a_retried_switch_never_brings_back_the_old_pair_left_in_config_json(tmp_path,
                                                                             monkeypatch):
    """La version antérieure a laissé dans config.json la paire de l'ancien
    environnement ; la première préparation échoue avant de la reprendre et la
    session s'enrôle sur le nouveau : la reprise garde la nouvelle paire."""
    seed_user_config(str(tmp_path), {"plugin_uuid": "u-1", "bootstrap_url": "https://dm-a.example"})
    make_job(config_dir=str(tmp_path))
    _older_version_left_in_config_json(tmp_path, "A-id", "A-key")
    runtime.reset_for_tests()
    local_config.LocalConfig(str(tmp_path), []).set("bootstrap_url", "https://dm-b.example")
    refuse_settings_writes(monkeypatch, refusals=1)
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_id", "B-id")
    job.set_config("relay_client_key", "B-key")
    make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("relay_client_id", "") == "B-id"
    assert job._get_config_from_file("relay_client_key", "") == "B-key"
    assert not set(read_user_config(str(tmp_path))) & set(credentials.STORED_KEYS)


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


def test_a_persisted_winner_outside_the_baked_list_stays_the_active_dm(tmp_path):
    """Après une passation, le DM qui a enrôlé le poste n'est pas dans l'OXT : il
    reste le DM actif tant qu'un autre n'a pas répondu."""
    seed_user_config(str(tmp_path), {"bootstrap_urls": ["https://new"],
                                     "last_bootstrap_url": "https://old"})
    job = make_job(config_dir=str(tmp_path))
    assert job._active_bootstrap_url() == "https://old"
