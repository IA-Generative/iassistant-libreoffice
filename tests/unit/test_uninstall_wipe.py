"""Désinstallation depuis le Gestionnaire des extensions : tout est effacé ;
désactivation et mises à jour : rien n'est effacé."""

import json
import logging
import threading
from unittest.mock import MagicMock

import pytest

from src.mirai import credentials, local_config
from tests.stubs.uno_stubs import install, make_job, seed_user_config

install()

from src.mirai.core.conversation import ConversationStore
from src.mirai.security_flow import FileJsonStore
from src.mirai.shell import runtime as shell_runtime
from src.mirai.shell import uninstall
from src.mirai.shell.uninstall import UninstallWatch

IDENTIFIER = "fr.gouv.interieur.mirai"


@pytest.fixture(autouse=True)
def timers(monkeypatch):
    """Les minuteries de la vérification différée ne partent pas seules : le
    test déclenche celles qui sont armées, sans attendre et sans qu'elles
    tombent au milieu d'un autre test."""
    armed = []

    class _Timer:
        daemon = False

        def __init__(self, interval, function):
            self.function = function

        def start(self):
            armed.append(self.function)

    monkeypatch.setattr(threading, "Timer", _Timer)

    def fire():
        due = list(armed)
        armed.clear()
        for function in due:
            function()

    return fire


def _profile(tmp_path):
    seed_user_config(str(tmp_path), {"plugin_uuid": "u", "refresh_token": "rt"})
    (tmp_path / "config.json").write_text(json.dumps({"plugin_uuid": "u"}), encoding="utf-8")


def _populated(tmp_path):
    _profile(tmp_path)
    return make_job(config_dir=str(tmp_path))


def _manager(deployed, fail=False):
    """Gestionnaire des extensions qui liste, par dépôt, les identifiants de
    `deployed` au moment de chaque requête."""
    def _deployed(repository, _abort, _env):
        if fail:
            raise RuntimeError("API indisponible")
        packages = []
        for ident in deployed.get(repository, ()):
            package = MagicMock()
            package.getIdentifier.return_value = MagicMock(Value=ident)
            packages.append(package)
        return packages
    manager = MagicMock()
    manager.getDeployedExtensions.side_effect = _deployed
    return manager


def _watch(tmp_path, manager):
    ctx = MagicMock()
    ctx.getValueByName.return_value = manager
    return UninstallWatch(ctx, shell_runtime.get(), IDENTIFIER, str(tmp_path))


def _watched_job(tmp_path, monkeypatch):
    """MainJob sur un profil rempli, dont l'écoute est branchée ; rend aussi la
    notification que le Gestionnaire des extensions lui enverra."""
    monkeypatch.setattr(uninstall, "MirAIUninstallListener", MagicMock())
    job = _populated(tmp_path)
    return job, uninstall.MirAIUninstallListener.call_args.args[0]


def test_uninstall_wipes_everything_and_freezes_writes(tmp_path, monkeypatch, timers):
    job, extensions_changed = _watched_job(tmp_path, monkeypatch)
    job.ctx.getValueByName.return_value = _manager({})
    extensions_changed()
    timers()
    timers()
    assert not (tmp_path / "mirai").exists()
    assert not (tmp_path / "config.json").exists()
    assert credentials.store().get("refresh_token") is None
    job.set_config("assistant_active_tab", "x")
    assert not (tmp_path / "mirai").exists()


def test_still_deployed_means_no_wipe(tmp_path, monkeypatch, timers):
    job, extensions_changed = _watched_job(tmp_path, monkeypatch)
    job.ctx.getValueByName.return_value = _manager({"user": [IDENTIFIER]})
    extensions_changed()
    timers()
    timers()
    assert (tmp_path / "mirai" / "settings.json").exists()


def test_own_update_in_flight_means_no_wipe(tmp_path, timers):
    _profile(tmp_path)
    _watch(tmp_path, _manager({})).schedule_check()
    timers()
    shell_runtime.get().self_update_in_flight = True
    timers()
    assert (tmp_path / "mirai" / "settings.json").exists()


def test_deployment_lookup(tmp_path):
    assert _watch(tmp_path, _manager({"user": [IDENTIFIER]})).still_deployed() is True
    assert _watch(tmp_path, _manager({"user": ["autre.ext"]})).still_deployed() is False
    assert _watch(tmp_path, _manager({}, fail=True)).still_deployed() is True  # inconnu : on n'efface pas


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
        seen["flag"] = shell_runtime.get().self_update_in_flight
        return False

    job._run_install_on_main_thread = _install
    job._make_silent_command_env = MagicMock(return_value=None)
    job._install_and_restart_in_process(str(oxt))
    assert seen["flag"] is True
    assert shell_runtime.get().self_update_in_flight is False


def _wiped_job(tmp_path):
    job = _populated(tmp_path)
    _watch(tmp_path, None).wipe("test")
    return job


def test_nothing_is_written_after_the_wipe(tmp_path):
    job = _wiped_job(tmp_path)
    job._save_update_state({"campaign_id": 1, "target_version": "2"}, "downloaded")
    job._save_prompt_calc("x")
    FileJsonStore(str(tmp_path / "mirai" / "state.json")).write({"a": 1})
    ConversationStore(str(tmp_path / "mirai")).append("user", "salut", "writer")
    assert job._prompt_log_path() == ""
    assert not (tmp_path / "mirai").exists()


def test_credentials_refuse_writes_after_the_wipe(tmp_path):
    _wiped_job(tmp_path)
    assert credentials.set_secret("refresh_token", "rt", "scope") is False
    credentials.remember("refresh_token", "rt")
    assert credentials.store().get("refresh_token") is None
    assert credentials.recall("refresh_token") == ""


def test_wipe_needs_no_uno_lookup_of_the_profile(tmp_path, monkeypatch, timers):
    """L'effacement tourne sur une minuterie : il efface le profil que la
    coquille a préparé sans redemander son dossier à LibreOffice."""
    job, extensions_changed = _watched_job(tmp_path, monkeypatch)
    job.ctx.getValueByName.return_value = _manager({})
    job.sm.createInstanceWithContext.side_effect = RuntimeError("UNO")
    extensions_changed()
    timers()
    timers()
    assert not (tmp_path / "mirai").exists()


def test_absence_must_be_confirmed_before_the_wipe(tmp_path, timers):
    _profile(tmp_path)
    deployed = {}
    _watch(tmp_path, _manager(deployed)).schedule_check()
    timers()
    deployed["user"] = [IDENTIFIER]
    timers()
    assert (tmp_path / "mirai" / "settings.json").exists()
    assert shell_runtime.get().wiped is False


def test_absence_on_both_checks_wipes(tmp_path, timers):
    _profile(tmp_path)
    _watch(tmp_path, _manager({})).schedule_check()
    timers()
    timers()
    assert not (tmp_path / "mirai").exists()


def test_skipped_listener_registration_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(uninstall, "MirAIUninstallListener", None)
    with caplog.at_level(logging.INFO):
        _watch(tmp_path, MagicMock()).register()
        monkeypatch.setattr(uninstall, "MirAIUninstallListener", MagicMock())
        _watch(tmp_path, None).register()
    assert len([line for line in caplog.messages if line.startswith("[désinstallation]")]) == 2
    assert shell_runtime.get().uninstall_listener is None


def test_listener_that_failed_to_register_is_registered_by_the_next_job(tmp_path, monkeypatch):
    monkeypatch.setattr(uninstall, "MirAIUninstallListener",
                        MagicMock(side_effect=[RuntimeError("boom"), MagicMock()]))
    make_job(config_dir=str(tmp_path))
    job = make_job(config_dir=str(tmp_path))
    job.ctx.getValueByName.return_value.addModifyListener.assert_called_once()


def test_failure_in_the_check_is_logged_not_raised(tmp_path, monkeypatch, caplog, timers):
    _profile(tmp_path)
    monkeypatch.setattr(credentials, "wipe", MagicMock(side_effect=RuntimeError("boom")))
    with caplog.at_level(logging.INFO):
        _watch(tmp_path, _manager({})).schedule_check()
        timers()
        timers()
    assert any("[désinstallation]" in line and "boom" in line for line in caplog.messages)
