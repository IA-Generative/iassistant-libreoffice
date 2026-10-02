"""MainJob lit et écrit dans <UserConfig>/mirai/ ; les jetons du DM restent en
mémoire ; une clé retirée par le DM cesse de s'appliquer."""

import json
import os
from unittest.mock import MagicMock

from src.mirai import credentials, local_config
from tests.stubs.uno_stubs import install, make_job, read_user_config, seed_user_config

install()


def _response(config):
    resp = MagicMock()
    resp.read.return_value = json.dumps(config).encode("utf-8")
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _fetch(job, config):
    job._failover_ordered_urls = lambda: ["https://dm.example"]
    job._urlopen = MagicMock(return_value=_response(config))
    return job._fetch_config(force=True)


def test_set_config_writes_settings_file_not_config_json(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job.set_config("assistant_active_tab", "actions")
    assert read_user_config(str(tmp_path))["assistant_active_tab"] == "actions"
    legacy = tmp_path / "config.json"
    assert not legacy.exists() or "assistant_active_tab" not in legacy.read_text()


def test_dm_token_lives_in_memory_only(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    _fetch(job, {"config": {"llmToken": "SECRET-tok-123", "llm_api_tokens": "SECRET-tok-123"}})
    other = make_job(config_dir=str(tmp_path))
    other.config_cache = None
    other._hydrate_config_cache()
    assert other.get_config("llm_api_tokens", "") == "SECRET-tok-123"
    for folder, _dirs, files in os.walk(tmp_path):
        for name in files:
            assert "SECRET" not in open(os.path.join(folder, name), errors="ignore").read()


def test_key_removed_by_dm_stops_applying(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    _fetch(job, {"config": {"systemPrompt": "ancien"}})
    assert job._get_config_from_file("systemPrompt", "") == "ancien"
    _fetch(job, {"config": {"doc_url": "https://doc"}})
    assert job._get_config_from_file("systemPrompt", "") == ""


def test_keycloak_aliases_are_normalised_into_the_snapshot(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    _fetch(job, {"config": {"keycloak": {"issuerUrl": "https://sso", "realm": "r",
                                         "client_id": "c"}}})
    assert job._get_config_from_file("keycloakIssuerUrl", "") == "https://sso"
    assert job._get_config_from_file("keycloakRealm", "") == "r"
    assert "keycloakIssuerUrl" not in read_user_config(str(tmp_path))


def test_seeded_transport_still_drives_tests(tmp_path):
    seed_user_config(str(tmp_path), {"enabled": True, "bootstrap_url": "https://dm"})
    job = make_job(config_dir=str(tmp_path))
    assert job._device_management_enabled() is True
    assert job._bootstrap_urls() == ["https://dm"]


def test_revoked_dm_token_is_forgotten(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    _fetch(job, {"config": {"llmToken": "tok-123456"}})
    _fetch(job, {"config": {"llmToken": ""}})
    assert credentials.recall(credentials.DM_LLM_TOKEN) == ""


def _package(tmp_path, model):
    path = tmp_path / "config.default.json"
    path.write_text(json.dumps({"llm_default_models": model}), encoding="utf-8")
    return str(path)


def _models(job, models):
    job.config_cache = None
    job._get_cached_models = MagicMock(return_value=models)


def test_default_model_falls_back_to_the_dm_snapshot(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._local_config().save_dm_snapshot({"config": {"llm_default_models": "modele-dm"}})
    _models(job, [])
    assert job.get_config("llm_default_models", "defaut-appelant") == "modele-dm"


def test_default_model_falls_back_to_the_package_config(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._local_cfg = local_config.LocalConfig(
        str(tmp_path), [_package(tmp_path, "llama3.2:latest")])
    _models(job, ["aaa-other:latest", "llama3.2:latest"])
    assert job.get_config("llm_default_models", "") == "llama3.2:latest"


def test_user_model_choice_wins_over_dm_and_package(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._local_cfg = local_config.LocalConfig(
        str(tmp_path), [_package(tmp_path, "llama3.2:latest")])
    job._local_config().save_dm_snapshot({"config": {"llm_default_models": "modele-dm"}})
    job.set_config("llm_default_models", "choix-utilisateur")
    _models(job, ["llama3.2:latest", "modele-dm", "choix-utilisateur"])
    assert job.get_config("llm_default_models", "") == "choix-utilisateur"


def _hydrate_new_instance(tmp_path, snapshot):
    local_config.LocalConfig(str(tmp_path), []).save_dm_snapshot(snapshot)
    job = make_job(config_dir=str(tmp_path))
    job.config_cache = None
    job._hydrate_config_cache()
    return job.config_cache


def test_snapshot_with_token_is_not_hydrated_in_a_new_process(tmp_path):
    credentials.forget_all()
    assert _hydrate_new_instance(tmp_path, {"config": {"llmToken": "tok-123456"}}) is None


def test_snapshot_with_token_hydrates_when_memory_holds_the_token(tmp_path):
    credentials.remember(credentials.DM_LLM_TOKEN, "tok-123456")
    cache = _hydrate_new_instance(tmp_path, {"config": {"llmToken": "tok-123456"}})
    assert cache == {"config": {"llmToken": ""}}


def test_snapshot_without_token_hydrates_with_empty_memory(tmp_path):
    credentials.forget_all()
    cache = _hydrate_new_instance(tmp_path, {"config": {"doc_url": "d"}})
    assert cache == {"config": {"doc_url": "d"}}
