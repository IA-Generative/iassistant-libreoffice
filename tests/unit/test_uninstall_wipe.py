"""Désinstallation depuis le Gestionnaire des extensions : tout est effacé ;
désactivation et mises à jour : rien n'est effacé."""

import json
from unittest.mock import MagicMock

from src.mirai import credentials, local_config
from tests.stubs.uno_stubs import install, make_job, seed_user_config

install()

from src.mirai.core.conversation import ConversationStore  # noqa: E402
from src.mirai.entrypoint import MainJob  # noqa: E402
from src.mirai.security_flow import FileJsonStore  # noqa: E402


def _populated(tmp_path):
    seed_user_config(str(tmp_path), {"plugin_uuid": "u", "refresh_token": "rt"})
    (tmp_path / "config.json").write_text(json.dumps({"plugin_uuid": "u"}), encoding="utf-8")
    job = make_job(config_dir=str(tmp_path))
    return job


def _manager(identifiers_by_repo=None, fail=False):
    def _deployed(repository, _abort, _env):
        if fail:
            raise RuntimeError("API indisponible")
        packages = []
        for ident in (identifiers_by_repo or {}).get(repository, ()):
            package = MagicMock()
            package.getIdentifier.return_value = MagicMock(Value=ident)
            packages.append(package)
        return packages
    manager = MagicMock()
    manager.getDeployedExtensions.side_effect = _deployed
    return manager


def test_uninstall_wipes_everything_and_freezes_writes(tmp_path):
    job = _populated(tmp_path)
    job._extension_still_deployed = lambda: False
    job._confirm_uninstalled()
    assert not (tmp_path / "mirai").exists()
    assert not (tmp_path / "config.json").exists()
    assert credentials.store().get("refresh_token") is None
    job.set_config("assistant_active_tab", "x")
    assert not (tmp_path / "mirai").exists()


def test_still_deployed_means_no_wipe(tmp_path):
    job = _populated(tmp_path)
    job._extension_still_deployed = lambda: True
    job._check_uninstalled()
    job._confirm_uninstalled()
    assert (tmp_path / "mirai" / "settings.json").exists()


def test_own_update_in_flight_means_no_wipe(tmp_path):
    job = _populated(tmp_path)
    job._extension_still_deployed = lambda: False
    MainJob._self_update_in_flight_cls = True
    job._check_uninstalled()
    job._confirm_uninstalled()
    assert (tmp_path / "mirai" / "settings.json").exists()


def test_deployment_lookup(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    ours = "fr.gouv.interieur.mirai"
    job.ctx.getValueByName = MagicMock(return_value=_manager({"user": [ours]}))
    assert job._extension_still_deployed() is True
    job.ctx.getValueByName = MagicMock(return_value=_manager({"user": ["autre.ext"]}))
    assert job._extension_still_deployed() is False
    job.ctx.getValueByName = MagicMock(return_value=_manager(fail=True))
    assert job._extension_still_deployed() is True        # inconnu : on n'efface pas


def test_foreign_config_json_is_kept(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"autre": 1}), encoding="utf-8")
    local_config.wipe(str(tmp_path))
    assert (tmp_path / "config.json").exists()


def test_install_and_restart_marks_the_update_in_flight(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    oxt = tmp_path / "mirai.oxt"
    oxt.write_bytes(b"x")
    seen = {}

    def _install(*_args):
        seen["flag"] = MainJob._self_update_in_flight_cls
        return False

    job._run_install_on_main_thread = _install
    job._make_silent_command_env = MagicMock(return_value=None)
    job._install_and_restart_in_process(str(oxt))
    assert seen["flag"] is True
    assert MainJob._self_update_in_flight_cls is False


def _wiped_job(tmp_path):
    job = _populated(tmp_path)
    job._wipe_all_data("test")
    return job


def test_nothing_is_written_after_the_wipe(tmp_path):
    job = _wiped_job(tmp_path)
    job._save_update_state({"campaign_id": 1, "target_version": "2"}, "downloaded")
    job._save_prompt_calc("x")
    FileJsonStore(str(tmp_path / "mirai" / "state.json")).write({"a": 1})
    ConversationStore(str(tmp_path / "mirai")).append("user", "salut")
    assert job._prompt_log_path() == ""
    assert not (tmp_path / "mirai").exists()


def test_credentials_refuse_writes_after_the_wipe(tmp_path):
    _wiped_job(tmp_path)
    assert credentials.set_secret("refresh_token", "rt", "scope") is False
    credentials.remember("refresh_token", "rt")
    assert credentials.store().get("refresh_token") is None
    assert credentials.recall("refresh_token") == ""


def test_wipe_uses_the_cached_user_config_dir(tmp_path):
    job = _populated(tmp_path)
    job._local_config()
    job._get_user_config_dir = MagicMock(side_effect=RuntimeError("UNO"))
    job._wipe_all_data("test")
    assert not (tmp_path / "mirai").exists()


def test_absence_must_be_confirmed_before_the_wipe(tmp_path):
    job = _populated(tmp_path)
    answers = iter([False, True])
    job._extension_still_deployed = lambda: next(answers)
    job._check_uninstalled()
    job._confirm_uninstalled()
    assert (tmp_path / "mirai" / "settings.json").exists()
    assert MainJob._wiped_cls is False


def test_absence_on_both_checks_wipes(tmp_path):
    job = _populated(tmp_path)
    job._extension_still_deployed = lambda: False
    job._check_uninstalled()
    job._confirm_uninstalled()
    assert not (tmp_path / "mirai").exists()


def test_skipped_listener_registration_is_logged(tmp_path, monkeypatch):
    job = make_job(config_dir=str(tmp_path))
    logged = []
    monkeypatch.setattr("src.mirai.entrypoint.log_to_file", logged.append)
    monkeypatch.setattr("src.mirai.entrypoint.MirAIUninstallListener", None)
    job._register_uninstall_listener()
    monkeypatch.setattr("src.mirai.entrypoint.MirAIUninstallListener", MagicMock())
    job.ctx.getValueByName = MagicMock(return_value=None)
    job._register_uninstall_listener()
    assert len([line for line in logged if line.startswith("[désinstallation]")]) == 2
    assert MainJob._uninstall_listener_cls is None


def test_failure_in_the_check_is_logged_not_raised(tmp_path, monkeypatch):
    job = _populated(tmp_path)
    logged = []
    monkeypatch.setattr("src.mirai.entrypoint.log_to_file", logged.append)
    job._extension_still_deployed = MagicMock(side_effect=RuntimeError("boom"))
    job._check_uninstalled()
    job._confirm_uninstalled()
    assert any("[désinstallation]" in line and "boom" in line for line in logged)
