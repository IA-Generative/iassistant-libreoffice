"""Mécanisme natif de MAJ (<update-information>) + fiabilité de la voie
in-process : install sur le MAIN thread via ExtensionManager, scripts cmd.exe
désactivés par défaut, réconciliation post-redémarrage.
"""
import importlib.util
import json
import os
import tempfile
import time
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint
from src.mirai.entrypoint import MainJob

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_spec = importlib.util.spec_from_file_location(
    "inject_update_feed", os.path.join(ROOT, "scripts", "inject_update_feed.py"))
inject_update_feed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inject_update_feed)

MINIMAL_DESCRIPTION = """<?xml version='1.0' encoding='UTF-8'?>
<description
  xmlns="http://openoffice.org/extensions/description/2006"
  xmlns:xlink="http://www.w3.org/1999/xlink">
    <identifier value="fr.gouv.interieur.mirai"/>
    <version value="0.0.1.0.31"/>
</description>
"""


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def test_inject_bakes_one_src_per_bootstrap_url():
    """Chaque bootstrap_url du profil donne un <src> (failover natif LO),
    avec le chemin de feed conventionnel du DM."""
    d = tempfile.mkdtemp()
    desc = _write(d, "description.xml", MINIMAL_DESCRIPTION)
    cfg = _write(d, "config.json", json.dumps({
        "enabled": True,
        "bootstrap_urls": ["https://dm-a.example/", "https://dm-b.example"],
    }))
    msg = inject_update_feed.inject(desc, cfg)
    assert "2 feed URL(s)" in msg
    out = open(desc, encoding="utf-8").read()
    assert "<update-information>" in out
    assert 'xlink:href="https://dm-a.example/catalog/mirai-libreoffice/update.xml"' in out
    assert 'xlink:href="https://dm-b.example/catalog/mirai-libreoffice/update.xml"' in out
    # ordre préservé (LibreOffice essaie les <src> dans l'ordre)
    assert out.index("dm-a.example") < out.index("dm-b.example")
    # toujours bien formé
    import xml.etree.ElementTree as ET
    ET.fromstring(out.encode("utf-8"))


def test_inject_skips_offline_profile():
    """Profil offline (enabled:false) : pas de bloc — le bouton natif répond
    simplement « aucune mise à jour »."""
    d = tempfile.mkdtemp()
    desc = _write(d, "description.xml", MINIMAL_DESCRIPTION)
    cfg = _write(d, "config.json", json.dumps({
        "enabled": False, "bootstrap_urls": ["https://dm.example"],
    }))
    msg = inject_update_feed.inject(desc, cfg)
    assert "skipped" in msg
    assert "<update-information>" not in open(desc, encoding="utf-8").read()


def test_inject_is_idempotent():
    """Un description.xml déjà équipé n'est pas modifié (double build, repack)."""
    d = tempfile.mkdtemp()
    desc = _write(d, "description.xml", MINIMAL_DESCRIPTION)
    cfg = _write(d, "config.json", json.dumps({
        "enabled": True, "bootstrap_urls": ["https://dm.example"],
    }))
    inject_update_feed.inject(desc, cfg)
    first = open(desc, encoding="utf-8").read()
    msg = inject_update_feed.inject(desc, cfg)
    assert "already present" in msg
    assert open(desc, encoding="utf-8").read() == first


def test_inject_env_override_wins(monkeypatch):
    """MIRAI_UPDATE_FEED_URL force une URL de feed unique (builds spéciaux)."""
    d = tempfile.mkdtemp()
    desc = _write(d, "description.xml", MINIMAL_DESCRIPTION)
    cfg = _write(d, "config.json", json.dumps({
        "enabled": True, "bootstrap_urls": ["https://dm.example"],
    }))
    monkeypatch.setenv("MIRAI_UPDATE_FEED_URL", "https://override.example/update.xml")
    inject_update_feed.inject(desc, cfg)
    out = open(desc, encoding="utf-8").read()
    assert 'xlink:href="https://override.example/update.xml"' in out
    assert "dm.example" not in out


# La voie du Gestionnaire des extensions — remplace atomiquement une extension
# de même identifiant, PAS de remove-avant-add (le cycle removePackage/addPackage
# worker est ce qui laissait des entrées fantômes dans registrymodifications.xcu).

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


def test_install_and_restart_prefers_main_thread_over_legacy():
    """Main thread OK → le chemin worker legacy (removePackage/addPackage,
    vecteur de corruption) n'est PAS invoqué ; LO est fermé proprement."""
    fd, path = tempfile.mkstemp(suffix=".oxt")
    os.close(fd)
    try:
        job = make_job()
        job._run_install_on_main_thread = MagicMock(return_value=True)
        job._install_oxt_inprocess = MagicMock()
        job._close_after_inprocess_update = MagicMock()

        assert job._install_and_restart_in_process(path) is True
        job._install_oxt_inprocess.assert_not_called()
        job._close_after_inprocess_update.assert_called_once()
    finally:
        os.remove(path)


def test_install_and_restart_falls_back_to_legacy_worker_path():
    """Main thread KO → dernier recours worker (comportement historique)."""
    fd, path = tempfile.mkstemp(suffix=".oxt")
    os.close(fd)
    try:
        job = make_job()
        job._run_install_on_main_thread = MagicMock(return_value=False)
        job._install_oxt_inprocess = MagicMock(return_value=True)
        job._close_after_inprocess_update = MagicMock()

        assert job._install_and_restart_in_process(path) is True
        job._install_oxt_inprocess.assert_called_once()
        job._close_after_inprocess_update.assert_called_once()
    finally:
        os.remove(path)


def _job_with_state(state, current_version="0.0.1.0.31"):
    job = make_job(config_dir=tempfile.mkdtemp())
    pend = os.path.join(job._get_user_config_dir(), "pending_update")
    os.makedirs(pend, exist_ok=True)
    with open(os.path.join(pend, "update_state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    open(os.path.join(pend, "mirai_update.oxt"), "w").close()
    job._get_extension_version = MagicMock(return_value=current_version)
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    return job, pend


def test_reconcile_reports_installed_and_purges_on_version_match():
    """Version active == target → « installed » (véridique) rapporté au DM,
    pending_update purgé, anti-boucle levée."""
    MainJob._update_launch_blocked_cls.add("0.0.1.0.31")
    job, pend = _job_with_state({
        "campaign_id": 7, "target_version": "0.0.1.0.31",
        "version_before": "0.0.1.0.30", "stage": "user_accepted",
        "ts": time.time(),
    })
    job._reconcile_update_state()

    job._report_update_status.assert_called_once_with(
        7, "installed", "0.0.1.0.30", "0.0.1.0.31")
    assert not os.path.isdir(pend), "pending_update doit être purgé"
    assert "0.0.1.0.31" not in MainJob._update_launch_blocked_cls


def test_reconcile_keeps_fresh_pending_state():
    """MAJ pas encore appliquée (version ≠ target, état récent) → no-op :
    l'état et l'OXT stagé restent en place pour le fallback manuel."""
    job, pend = _job_with_state({
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "staged",
        "ts": time.time(),
    })
    job._reconcile_update_state()

    job._report_update_status.assert_not_called()
    assert os.path.isfile(os.path.join(pend, "update_state.json"))
    assert os.path.isfile(os.path.join(pend, "mirai_update.oxt"))


def test_reconcile_reports_failed_at_startup_when_target_never_became_active(monkeypatch):
    """Dossier de paquet laissé par une installation native annulée : l'étape dit
    installed_native, mais la cible n'est pas active au redémarrage. Le DM doit
    l'apprendre (failed) et l'état être purgé — sinon le poste ne retente rien
    pendant 14 jours et la campagne reste bloquée sur deferred."""
    monkeypatch.setattr(entrypoint, "_NATIVE_POLL_SECONDS", 0.01)
    job, pend = _job_with_state({
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_native",
        "route": "native", "ts": time.time(),
    }, current_version="0.0.1.0.31")

    job._reconcile_update_state(at_startup=True)

    job._report_update_status.assert_called_once_with(
        7, "failed", "0.0.1.0.31", "0.0.1.0.31", "installation non active au redémarrage")
    failed = [c for c in job._send_telemetry.call_args_list if c.args[0] == "UpdateInstallFailed"]
    assert len(failed) == 1
    assert failed[0].args[1]["route"] == "native"
    assert not os.path.isdir(pend)


def test_reconcile_at_startup_rechecks_registry_before_failing(monkeypatch):
    """Juste après le démarrage, le registre peut encore se consolider : une
    première lecture périmée ne doit pas produire un « failed » — la seconde
    lecture confirme l'installation."""
    monkeypatch.setattr(entrypoint, "_NATIVE_POLL_SECONDS", 0.01)
    job, pend = _job_with_state({
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_native",
        "route": "native", "ts": time.time(),
    }, current_version="0.0.1.0.31")
    job._get_extension_version = MagicMock(side_effect=["0.0.1.0.31", "0.0.1.0.32"])

    job._reconcile_update_state(at_startup=True)

    job._report_update_status.assert_called_once_with(
        7, "installed", "0.0.1.0.31", "0.0.1.0.32")
    assert not os.path.isdir(pend)


def test_reconcile_keeps_installed_state_in_session():
    """En session, LibreOffice garde l'ancien paquet enregistré jusqu'au
    redémarrage : la réconciliation du worker ne conclut rien sur une étape
    installed_*, elle ne rapporte surtout pas un échec."""
    job, pend = _job_with_state({
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "installed_native",
        "route": "native", "ts": time.time(),
    }, current_version="0.0.1.0.31")

    job._reconcile_update_state()

    job._report_update_status.assert_not_called()
    job._send_telemetry.assert_not_called()
    assert os.path.isfile(os.path.join(pend, "update_state.json"))


def test_reconcile_purges_stale_state():
    """État périmé (> 14 jours) → purge silencieuse, aucun rapport."""
    job, pend = _job_with_state({
        "campaign_id": 7, "target_version": "0.0.1.0.32",
        "version_before": "0.0.1.0.31", "stage": "staged",
        "ts": time.time() - 15 * 24 * 3600,
    })
    job._reconcile_update_state()

    job._report_update_status.assert_not_called()
    assert not os.path.isdir(pend)


def test_reconcile_discards_corrupt_state_file():
    """update_state.json illisible → supprimé, jamais d'exception."""
    job = make_job(config_dir=tempfile.mkdtemp())
    pend = os.path.join(job._get_user_config_dir(), "pending_update")
    os.makedirs(pend, exist_ok=True)
    state_path = os.path.join(pend, "update_state.json")
    with open(state_path, "w") as fh:
        fh.write("{not json")
    job._report_update_status = MagicMock()

    job._reconcile_update_state()

    assert not os.path.isfile(state_path)
    job._report_update_status.assert_not_called()


def test_save_update_state_roundtrip():
    """_save_update_state écrit un état relisible par la réconciliation."""
    job = make_job(config_dir=tempfile.mkdtemp())
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


# Le feed est récupéré par la pile HTTP de LibreOffice (UpdateInformationProvider,
# la machinerie exacte du bouton « Vérifier les mises à jour ») : ce check
# headless valide proxy/TLS/GPO sur la flotte sans action utilisateur.

def _job_for_feed_check(urls):
    job = make_job()
    job._failover_ordered_urls = MagicMock(return_value=urls)
    job._get_extension_version = MagicMock(return_value="0.0.1.0.31")
    job._send_telemetry = MagicMock()
    return job


def test_update_feed_urls_follow_failover_order():
    """Une URL de feed par bootstrap, ordre de failover préservé, même
    convention de chemin que le bake au build."""
    job = _job_for_feed_check(["https://dm-b.example/", "https://dm-a.example"])
    assert job._update_feed_urls() == [
        "https://dm-b.example/catalog/mirai-libreoffice/update.xml",
        "https://dm-a.example/catalog/mirai-libreoffice/update.xml",
    ]


def test_check_native_feed_reports_announced_version():
    """Feed joignable → version annoncée extraite du DOM (<version value>),
    télémétrie feed.ok=true, et l'identifiant d'extension est bien passé au
    provider (sinon LO matcherait d'autres extensions du feed)."""
    job = _job_for_feed_check(["https://dm.example"])
    provider = MagicMock(name="UpdateInformationProvider")
    job.ctx.getServiceManager.return_value.createInstanceWithContext.return_value = provider
    element = MagicMock(name="descriptionElement")
    nodes = MagicMock()
    nodes.getLength.return_value = 1
    nodes.item.return_value.getAttribute.return_value = "0.0.1.0.32"
    element.getElementsByTagNameNS.return_value = nodes
    provider.getUpdateInformation.return_value = [element]

    assert job._check_native_feed() == "0.0.1.0.32"

    args = provider.getUpdateInformation.call_args.args
    # Adresse nue tant qu'aucune réécriture n'a réussi (device-management#40).
    assert args[0] == ("https://dm.example/catalog/mirai-libreoffice/update.xml",)
    assert args[1] == "fr.gouv.interieur.mirai"
    attrs = job._send_telemetry.call_args.args[1]
    assert job._send_telemetry.call_args.args[0] == "NativeFeedCheck"
    assert attrs["feed.ok"] == "true"
    assert attrs["feed.announced_version"] == "0.0.1.0.32"


def test_check_native_feed_reports_error_without_raising():
    """Feed injoignable (proxy/TLS/GPO/404) → None, télémétrie feed.ok=false
    avec le détail — jamais d'exception (best-effort)."""
    job = _job_for_feed_check(["https://dm.example"])
    provider = MagicMock(name="UpdateInformationProvider")
    provider.getUpdateInformation.side_effect = RuntimeError("proxy timeout")
    job.ctx.getServiceManager.return_value.createInstanceWithContext.return_value = provider

    assert job._check_native_feed() is None

    attrs = job._send_telemetry.call_args.args[1]
    assert attrs["feed.ok"] == "false"
    assert "proxy timeout" in attrs["feed.error"]


def test_check_native_feed_skips_without_bootstrap():
    """Aucun bootstrap configuré → skip total : ni service UNO ni télémétrie."""
    job = _job_for_feed_check([])
    smgr = MagicMock(name="freshServiceManager")
    job.ctx.getServiceManager.return_value = smgr

    assert job._check_native_feed() is None

    smgr.createInstanceWithContext.assert_not_called()
    job._send_telemetry.assert_not_called()


def test_schedule_update_runs_for_rollback_action():
    """Une directive action=rollback démarre le worker comme un update."""
    import threading as _threading
    job = make_job()
    done = _threading.Event()
    job._perform_update = lambda directive: done.set()

    job._schedule_update({"action": "rollback", "target_version": "0.0.1.0.30-rb"})

    assert done.wait(2), "le worker de rollback aurait dû tourner"


def test_reconcile_confirms_rollback_to_older_version():
    """Après un rollback (target < version_before), la réconciliation confirme
    « installed » dès que la version ACTIVE == target — la comparaison est une
    égalité stricte, pas un « plus récent que »."""
    job, pend = _job_with_state({
        "campaign_id": 11, "target_version": "0.0.1.0.30",
        "version_before": "0.0.1.0.31", "stage": "user_accepted",
        "ts": time.time(),
    }, current_version="0.0.1.0.30")

    job._reconcile_update_state()

    job._report_update_status.assert_called_once_with(
        11, "installed", "0.0.1.0.31", "0.0.1.0.30")
    assert not os.path.isdir(pend)
