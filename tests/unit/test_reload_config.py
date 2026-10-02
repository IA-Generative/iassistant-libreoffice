"""« Recharger la configuration » ne recopie plus toutes les clés du DM.

La recopie écrivait aussi les jetons et `proxy_allow_insecure_ssl: true` (valeur
du gabarit DM), qui coupait la vérification TLS de tous les appels.
"""

from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()


def test_reload_returns_settings_without_writing_them(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    dm_settings = {"proxy_allow_insecure_ssl": True, "embdToken": "SECRET-embd",
                   "api_type": "chat", "llmToken": "SECRET-llm"}
    job._fetch_config = MagicMock(return_value={"config": dm_settings})

    settings = job._refresh_config_to_local()

    assert settings == dm_settings
    assert settings is not dm_settings
    assert job._get_config_from_file("proxy_allow_insecure_ssl", False) is False
    assert job._get_config_from_file("embdToken", "") == ""
    assert job._get_config_from_file("api_type", "") == ""


def test_reload_cancelled_before_fetch_returns_empty(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._fetch_config = MagicMock()
    assert job._refresh_config_to_local(cancel_flag={"cancel": True}) == {}
    job._fetch_config.assert_not_called()
