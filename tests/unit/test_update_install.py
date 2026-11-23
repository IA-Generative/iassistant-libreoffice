"""Voie in-process de la mise à jour : installation sur le thread principal via
ExtensionManager, réconciliation après redémarrage.
"""
import json
import os
import tempfile
import time
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint
from src.mirai.shell import runtime as shell_runtime

# La voie du Gestionnaire des extensions remplace atomiquement une extension
# de même identifiant : pas de remove-avant-add.

def _job_with_sync_async_callback():
    """Job dont l'AsyncCallback exécute le callback immédiatement (synchro),
    comme si le main thread était disponible tout de suite."""
    job = make_job()
    async_cb = MagicMock(name="AsyncCallback")
    async_cb.addCallback.side_effect = lambda cb, data: cb.notify(data)
    job.ctx.getServiceManager.return_value.createInstanceWithContext.return_value = async_cb
    return job, async_cb


def test_main_thread_install_uses_extension_manager_add_only():
    """addExtension(repo 'user') via le singleton ExtensionManager, sans
    removeExtension/removePackage préalable."""
    job, async_cb = _job_with_sync_async_callback()
    mgr = MagicMock(name="ExtensionManager")
    job.ctx.getValueByName.return_value = mgr

    assert job._run_install_on_main_thread("file:///x.oxt", (), None, timeout=2) is True
    async_cb.addCallback.assert_called_once()
    job.ctx.getValueByName.assert_called_with(
        "/singletons/com.sun.star.deployment.ExtensionManager")
    mgr.addExtension.assert_called_once()
    args = mgr.addExtension.call_args.args
    assert args[0] == "file:///x.oxt"
    assert args[2] == "user"
    mgr.removeExtension.assert_not_called()
    mgr.removePackage.assert_not_called()


def test_main_thread_install_times_out_to_false():
    """Main thread indisponible (callback jamais exécuté) → False sous le
    timeout, l'appelant dégrade — et le callback tardif devient no-op."""
    job = make_job()
    async_cb = MagicMock(name="AsyncCallback")   # addCallback n'exécute rien
    job.ctx.getServiceManager.return_value.createInstanceWithContext.return_value = async_cb
    mgr = MagicMock(name="ExtensionManager")
    job.ctx.getValueByName.return_value = mgr

    start = time.time()
    assert job._run_install_on_main_thread("file:///x.oxt", (), None, timeout=0.2) is False
    assert time.time() - start < 5
    # le callback livré après coup ne doit PAS installer (anti double-install)
    cb = async_cb.addCallback.call_args.args[0]
    cb.notify(None)
    mgr.addExtension.assert_not_called()


def test_main_thread_install_reports_manager_failure():
    """addExtension lève (ex. refus de policy) → False, pour dégradation."""
    job, _ = _job_with_sync_async_callback()
    mgr = MagicMock(name="ExtensionManager")
    mgr.addExtension.side_effect = RuntimeError("denied")
    job.ctx.getValueByName.return_value = mgr

    assert job._run_install_on_main_thread("file:///x.oxt", (), None, timeout=2) is False


def test_install_and_restart_closes_after_success():
    """Installation réussie sur le thread principal → LO est fermé proprement."""
    fd, path = tempfile.mkstemp(suffix=".oxt")
    os.close(fd)
    try:
        job = make_job()
        job._run_install_on_main_thread = MagicMock(return_value=True)
        job._close_after_inprocess_update = MagicMock()

        assert job._install_and_restart_in_process(path) is True
        job._close_after_inprocess_update.assert_called_once()
    finally:
        os.remove(path)


def _job_with_state(tmp_path, state, current_version="0.0.1.0.31"):
    job = make_job(config_dir=str(tmp_path))
    pend = job._pending_update_dir()
    os.makedirs(pend, exist_ok=True)
    with open(os.path.join(pend, "update_state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    open(os.path.join(pend, "mirai_update.oxt"), "w").close()
    job._get_extension_version = MagicMock(return_value=current_version)
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    return job, pend


def test_reconcile_reports_installed_and_purges_on_version_match(tmp_path):
    """Version active == target → « installed » (véridique) rapporté au DM,
    pending_update purgé, anti-boucle levée."""
    shell_runtime.get().update_launch_blocked.add("0.0.1.0.31")
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 7, "target_version": "0.0.1.0.31",
        "version_before": "0.0.1.0.30", "stage": "user_accepted",
        "ts": time.time(),
    })
    job._reconcile_update_state()

    job._report_update_status.assert_called_once_with(
        7, "installed", "0.0.1.0.30", "0.0.1.0.31")
    assert not os.path.isdir(pend), "pending_update doit être purgé"
    assert "0.0.1.0.31" not in shell_runtime.get().update_launch_blocked


def test_reconcile_keeps_fresh_pending_state(tmp_path):
    """MAJ pas encore appliquée (version ≠ target, état récent) → no-op :
    l'état et l'OXT stagé restent en place pour le fallback manuel."""
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "staged",
        "ts": time.time(),
    })
    job._reconcile_update_state()

    job._report_update_status.assert_not_called()
    assert os.path.isfile(os.path.join(pend, "update_state.json"))
    assert os.path.isfile(os.path.join(pend, "mirai_update.oxt"))


def test_reconcile_reports_failed_at_startup_when_target_never_became_active(monkeypatch, tmp_path):
    """Installation annulée par LibreOffice : l'étape dit installed_inprocess,
    mais la cible n'est pas active au redémarrage. Le DM doit l'apprendre
    (failed) et l'état être purgé — sinon le poste ne retente rien pendant
    14 jours et la campagne reste bloquée sur deferred."""
    monkeypatch.setattr(entrypoint, "_RECONCILE_RECHECK_SECONDS", 0.01)
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_inprocess",
        "route": "directed", "ts": time.time(),
    }, current_version="0.0.1.0.31")

    job._reconcile_update_state(at_startup=True)

    job._report_update_status.assert_called_once_with(
        7, "failed", "0.0.1.0.31", "0.0.1.0.31", "installation non active au redémarrage")
    failed = [c for c in job._send_telemetry.call_args_list if c.args[0] == "UpdateInstallFailed"]
    assert len(failed) == 1
    assert failed[0].args[1]["route"] == "directed"
    assert not os.path.isdir(pend)


def test_reconcile_at_startup_rechecks_registry_before_failing(monkeypatch, tmp_path):
    """Juste après le démarrage, le registre peut encore se consolider : une
    première lecture périmée ne doit pas produire un « failed » — la seconde
    lecture confirme l'installation."""
    monkeypatch.setattr(entrypoint, "_RECONCILE_RECHECK_SECONDS", 0.01)
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_inprocess",
        "route": "directed", "ts": time.time(),
    }, current_version="0.0.1.0.31")
    job._get_extension_version = MagicMock(side_effect=["0.0.1.0.31", "0.0.1.0.32"])

    job._reconcile_update_state(at_startup=True)

    job._report_update_status.assert_called_once_with(
        7, "installed", "0.0.1.0.31", "0.0.1.0.32")
    assert not os.path.isdir(pend)


def test_reconcile_keeps_installed_state_in_session(tmp_path):
    """En session, LibreOffice garde l'ancien paquet enregistré jusqu'au
    redémarrage : la réconciliation du worker ne conclut rien sur une étape
    installed_*, elle ne rapporte surtout pas un échec."""
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_inprocess",
        "route": "directed", "ts": time.time(),
    }, current_version="0.0.1.0.31")

    job._reconcile_update_state()

    job._report_update_status.assert_not_called()
    job._send_telemetry.assert_not_called()
    assert os.path.isfile(os.path.join(pend, "update_state.json"))


def _state_left_by_libreoffice_dialog():
    """État tel que l'écrivaient les versions qui installaient aussi par le
    dialogue « Mise à jour des extensions » de LibreOffice."""
    return {
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_native",
        "ts": time.time(), "route": "native", "postponed_until": 0.0,
        "native_attempts": 1,
    }


def test_startup_reports_installed_for_an_update_left_by_the_libreoffice_dialog(tmp_path):
    """La version installée par le dialogue de LibreOffice démarre : elle clôt
    la campagne de sa devancière (« installed » au DM, télémétrie, purge)."""
    job, pend = _job_with_state(
        tmp_path, _state_left_by_libreoffice_dialog(), current_version="0.0.1.0.32")

    job._reconcile_update_state(at_startup=True)

    job._report_update_status.assert_called_once_with(
        7, "installed", "0.0.1.0.31", "0.0.1.0.32")
    updated = [c.args[1] for c in job._send_telemetry.call_args_list
               if c.args[0] == "ExtensionUpdated"]
    assert updated == [{"version_after": "0.0.1.0.32", "campaign_id": "7",
                        "confirmed": "true", "route": "native"}]
    assert not os.path.isdir(pend)


def test_startup_reports_failed_for_an_inactive_update_left_by_the_libreoffice_dialog(
        monkeypatch, tmp_path):
    """Le dialogue de LibreOffice avait installé la cible, mais elle n'est pas
    active au redémarrage : le DM apprend l'échec et l'état est purgé."""
    monkeypatch.setattr(entrypoint, "_RECONCILE_RECHECK_SECONDS", 0.01)
    job, pend = _job_with_state(
        tmp_path, _state_left_by_libreoffice_dialog(), current_version="0.0.1.0.31")

    job._reconcile_update_state(at_startup=True)

    job._report_update_status.assert_called_once_with(
        7, "failed", "0.0.1.0.31", "0.0.1.0.31", "installation non active au redémarrage")
    failed = [c.args[1] for c in job._send_telemetry.call_args_list
              if c.args[0] == "UpdateInstallFailed"]
    assert failed == [{"version_after": "0.0.1.0.32", "campaign_id": "7", "route": "native"}]
    assert not os.path.isdir(pend)


def test_reconcile_purges_stale_state(tmp_path):
    """État périmé (> 14 jours) → purge silencieuse, aucun rapport."""
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "staged",
        "ts": time.time() - 15 * 24 * 3600,
    })
    job._reconcile_update_state()

    job._report_update_status.assert_not_called()
    assert not os.path.isdir(pend)


def test_reconcile_discards_corrupt_state_file(tmp_path):
    """update_state.json illisible → supprimé, jamais d'exception."""
    job = make_job(config_dir=str(tmp_path))
    pend = job._pending_update_dir()
    os.makedirs(pend, exist_ok=True)
    state_path = os.path.join(pend, "update_state.json")
    with open(state_path, "w") as fh:
        fh.write("{not json")
    job._report_update_status = MagicMock()

    job._reconcile_update_state()

    assert not os.path.isfile(state_path)
    job._report_update_status.assert_not_called()


def test_save_update_state_roundtrip(tmp_path):
    """_save_update_state écrit un état relisible par la réconciliation."""
    job = make_job(config_dir=str(tmp_path))
    job._get_extension_version = MagicMock(return_value="0.0.1.0.31")
    job._save_update_state(
        {"campaign_id": 3, "target_version": "0.0.1.0.32"}, "staged")

    with open(job._update_state_path(), encoding="utf-8") as fh:
        state = json.load(fh)
    assert state["campaign_id"] == 3
    assert state["target_version"] == "0.0.1.0.32"
    assert state["version_before"] == "0.0.1.0.31"
    assert state["stage"] == "staged"
    assert state["ts"] > 0


def test_installed_version_falls_back_to_the_package_description_when_registry_is_unreadable(
        monkeypatch, tmp_path):
    """Registre des extensions illisible : la version installée est lue dans le
    description.xml à la racine du paquet (<paquet>/src/mirai/entrypoint.py),
    celle que la réconciliation compare à la cible."""
    job = make_job(config_dir=str(tmp_path / "profil"))
    pkg = tmp_path / "uno_packages" / "lu42.tmp_" / "mirai.oxt"
    (pkg / "src" / "mirai").mkdir(parents=True)
    (pkg / "description.xml").write_text(
        '<description><identifier value="fr.gouv.interieur.mirai"/>'
        '<version value="0.0.1.0.32"/></description>', encoding="utf-8")
    monkeypatch.setattr(entrypoint, "__file__", str(pkg / "src" / "mirai" / "entrypoint.py"))
    smgr = job.ctx.getServiceManager.return_value
    smgr.createInstanceWithContext.side_effect = RuntimeError("no registry")

    assert job._get_extension_version() == "0.0.1.0.32"


def test_schedule_update_runs_for_rollback_action():
    """Une directive action=rollback démarre le worker comme un update."""
    import threading as _threading
    job = make_job()
    done = _threading.Event()
    job._perform_update = lambda directive: done.set()

    job._schedule_update({"action": "rollback", "target_version": "0.0.1.0.30-rb"})

    assert done.wait(2), "le worker de rollback aurait dû tourner"


def test_reconcile_confirms_rollback_to_older_version(tmp_path):
    """Après un rollback (target < version_before), la réconciliation confirme
    « installed » dès que la version ACTIVE == target — la comparaison est une
    égalité stricte, pas un « plus récent que »."""
    job, pend = _job_with_state(tmp_path, {
        "campaign_id": 11, "target_version": "0.0.1.0.30",
        "version_before": "0.0.1.0.31", "stage": "user_accepted",
        "ts": time.time(),
    }, current_version="0.0.1.0.30")

    job._reconcile_update_state()

    job._report_update_status.assert_called_once_with(
        11, "installed", "0.0.1.0.31", "0.0.1.0.30")
    assert not os.path.isdir(pend)
