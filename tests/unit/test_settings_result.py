"""Paramètres validés : chaque champ rempli part dans sa clé de configuration."""

from types import SimpleNamespace

from tests.stubs.uno_stubs import install

install()

from src.mirai.shell.settings_result import apply_settings_result


def _job():
    saved = {}
    job = SimpleNamespace(set_config=saved.__setitem__, _log=lambda _message: None)
    return job, saved


def test_validated_fields_are_saved_to_their_config_keys():
    job, saved = _job()
    apply_settings_result(job, {"endpoint": "https://llm.example/v1",
                                "api_key": "sk-test", "model": "chat"})
    assert saved == {"llm_base_urls": "https://llm.example/v1",
                     "llm_api_tokens": "sk-test",
                     "llm_default_models": "chat"}


def test_an_endpoint_that_is_not_a_url_is_not_saved():
    job, saved = _job()
    apply_settings_result(job, {"endpoint": "llm.example/v1", "model": "chat"})
    assert saved == {"llm_default_models": "chat"}


def test_an_emptied_key_is_saved_empty():
    job, saved = _job()
    apply_settings_result(job, {"api_key": ""})
    assert saved == {"llm_api_tokens": ""}


def test_a_cancelled_dialog_saves_nothing():
    job, saved = _job()
    apply_settings_result(job, {})
    assert saved == {}
