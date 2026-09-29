"""Route native pilotée par le DM : le DM décide (directive), LibreOffice
installe (dialogue « Mise à jour des extensions », feed <update-information>),
le plugin ferme proprement. Route dirigée en repli ; refus mémorisé 24 h.
"""
import itertools
import json
import os
import shutil
import tempfile
import threading
import time
from unittest.mock import MagicMock

import pytest

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint
from src.mirai.entrypoint import MainJob

TARGET = "0.0.1.0.32"
CURRENT = "0.0.1.0.31"

_TMPDIRS = []


def _mkdtemp():
    """Répertoire temporaire supprimé à la fin du module (les jobs écrivent un
    profil et un cache de paquets factices à chaque test)."""
    path = tempfile.mkdtemp()
    _TMPDIRS.append(path)
    return path


@pytest.fixture(autouse=True, scope="module")
def _purge_tmpdirs():
    yield
    for path in _TMPDIRS:
        shutil.rmtree(path, ignore_errors=True)
    _TMPDIRS.clear()


@pytest.fixture
def native_timing(monkeypatch):
    """Surveillance native accélérée : les tests jouent sur les constantes de
    module, seul mécanisme d'injection des délais."""
    def _set(wait=2, poll=0.01):
        monkeypatch.setattr(entrypoint, "_NATIVE_INSTALL_WAIT_SECONDS", wait)
        monkeypatch.setattr(entrypoint, "_NATIVE_POLL_SECONDS", poll)
    _set()
    return _set


def _job(current_version=CURRENT):
    job = make_job(config_dir=_mkdtemp())
    job._get_extension_version = MagicMock(return_value=current_version)
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    job._package_cache_dir = lambda d=_mkdtemp(): d
    return job


def _state(job):
    with open(job._update_state_path(), encoding="utf-8") as fh:
        return json.load(fh)


def test_constants_match_spec():
    assert entrypoint._UPDATE_POSTPONE_SECONDS == 24 * 3600
    assert entrypoint._NATIVE_INSTALL_WAIT_SECONDS == 900
    assert entrypoint._NATIVE_POLL_SECONDS == 5
    assert entrypoint._NATIVE_TRIGGER_TIMEOUT_SECONDS == 30
    assert entrypoint._NATIVE_MAX_ATTEMPTS == 2
    assert entrypoint._CLOSE_RETRY_SECONDS == 120
    assert entrypoint._CLOSE_RETRY_INTERVAL_SECONDS == 3
    assert entrypoint._PROMPT_WIZARD_WAIT_SECONDS == 120
    assert entrypoint._PROMPT_GRACE_SECONDS == 30
    assert entrypoint._CLOSE_USER_REFUSAL_SECONDS == 1.0


def test_save_update_state_persists_route_and_cooldown_fields():
    job = _job()
    until = time.time() + 3600
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "native_dialog",
                           route="native", postponed_until=until, native_attempts=1)
    state = _state(job)
    assert state["route"] == "native"
    assert state["postponed_until"] == until
    assert state["native_attempts"] == 1
    assert state["stage"] == "native_dialog"


def test_save_update_state_keeps_fields_for_same_target():
    """Un enregistrement d'étape ne perd pas route / native_attempts posés avant."""
    job = _job()
    d = {"campaign_id": 3, "target_version": TARGET}
    job._save_update_state(d, "native_dialog", route="native", native_attempts=1)
    job._save_update_state(d, "postponed", postponed_until=time.time() + 10)
    state = _state(job)
    assert state["route"] == "native"
    assert state["native_attempts"] == 1
    assert state["postponed_until"] > time.time()


def test_save_update_state_resets_fields_for_new_target():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() + 10, native_attempts=2)
    job._save_update_state({"campaign_id": 4, "target_version": "0.0.1.0.33"}, "staged")
    state = _state(job)
    assert state["target_version"] == "0.0.1.0.33"
    assert state["route"] == ""
    assert state["postponed_until"] == 0
    assert state["native_attempts"] == 0


def test_save_update_state_keeps_version_before_across_stages():
    """Après l'installation, la version active est déjà la cible : version_before
    doit rester celle d'avant, sinon la réconciliation rapporte n'importe quoi."""
    job = _job()
    d = {"campaign_id": 3, "target_version": TARGET}
    job._save_update_state(d, "native_dialog", route="native")
    job._get_extension_version = MagicMock(return_value=TARGET)
    job._save_update_state(d, "installed_native")
    assert _state(job)["version_before"] == CURRENT


def test_load_update_state_returns_empty_when_missing_or_corrupt():
    job = _job()
    assert job._load_update_state() == {}
    os.makedirs(os.path.dirname(job._update_state_path()), exist_ok=True)
    with open(job._update_state_path(), "w") as fh:
        fh.write("{not json")
    assert job._load_update_state() == {}


def _schedule_and_wait(job, directive, seconds=2):
    done = threading.Event()
    job._perform_update = lambda d: done.set()
    job._schedule_update(directive)
    return done.wait(seconds)


def test_schedule_update_skips_target_in_cooldown():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() + 3600)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is False


def test_schedule_update_runs_after_cooldown_expired():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() - 1)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is True


def test_schedule_update_ignores_cooldown_of_other_target():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="native", postponed_until=time.time() + 3600)
    assert _schedule_and_wait(job, {"action": "update", "target_version": "0.0.1.0.33"}) is True


def test_schedule_update_tolerates_corrupt_postponed_until():
    """Un état corrompu ne doit jamais faire échouer le rafraîchissement de config."""
    job = _job()
    os.makedirs(os.path.dirname(job._update_state_path()), exist_ok=True)
    with open(job._update_state_path(), "w", encoding="utf-8") as fh:
        json.dump({"target_version": TARGET, "postponed_until": "x"}, fh)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is True


# C'est LibreOffice qui interroge le feed cuit dans l'extension INSTALLÉE, avec
# sa pile HTTP. Vrai seulement si la version annoncée == cible de la directive.

PIP_PATH = "/singletons/com.sun.star.deployment.PackageInformationProvider"


def _job_with_provider(pairs=None, error=None):
    job = _job()
    provider = MagicMock(name="PackageInformationProvider")
    if error is not None:
        provider.isUpdateAvailable.side_effect = error
    else:
        provider.isUpdateAvailable.return_value = tuple(pairs or ())
    job.ctx.getValueByName = MagicMock(return_value=provider)
    return job, provider


def test_native_feed_offers_true_when_announced_version_matches():
    job, provider = _job_with_provider([("fr.gouv.interieur.mirai", TARGET)])
    assert job._native_feed_offers(TARGET) is True
    job.ctx.getValueByName.assert_called_with(PIP_PATH)
    provider.isUpdateAvailable.assert_called_once_with("fr.gouv.interieur.mirai")


def test_native_feed_offers_false_when_version_differs():
    job, _ = _job_with_provider([("fr.gouv.interieur.mirai", "0.0.1.0.40")])
    assert job._native_feed_offers(TARGET) is False


def test_native_feed_offers_false_when_feed_silent():
    """Bloc feed absent, feed injoignable ou pas plus récent → séquence vide."""
    job, _ = _job_with_provider([])
    assert job._native_feed_offers(TARGET) is False


def test_native_feed_offers_ignores_other_extensions():
    job, _ = _job_with_provider([("org.example.other", TARGET)])
    assert job._native_feed_offers(TARGET) is False


def test_native_feed_offers_false_on_error_or_missing_provider():
    job, _ = _job_with_provider(error=RuntimeError("proxy"))
    assert job._native_feed_offers(TARGET) is False
    job2 = _job()
    job2.ctx.getValueByName = MagicMock(return_value=None)
    assert job2._native_feed_offers(TARGET) is False
    assert job2._native_feed_offers("") is False


# L'appel exact de la bulle de notification de LibreOffice (updatecheck.cxx →
# showExtensionDialog). Il met la vérification en file sur le thread de commandes
# de LO et rend la main ; il doit partir du thread principal (AsyncCallback).

PMD = "com.sun.star.deployment.ui.PackageManagerDialog"
ASYNC = "com.sun.star.awt.AsyncCallback"


def _job_with_services(run_callback=True):
    """AsyncCallback synchrone (le main thread est disponible tout de suite)
    ou inerte (run_callback=False) ; PackageManagerDialog mocké."""
    job = _job()
    smgr = job.ctx.getServiceManager.return_value
    default = smgr.createInstanceWithContext.return_value
    dialog = MagicMock(name="PackageManagerDialog")
    async_cb = MagicMock(name="AsyncCallback")
    if run_callback:
        async_cb.addCallback.side_effect = lambda cb, data: cb.notify(data)
    services = {PMD: dialog, ASYNC: async_cb}
    smgr.createInstanceWithContext.side_effect = lambda name, ctx: services.get(name, default)
    return job, dialog, async_cb


def test_trigger_opens_update_dialog_on_main_thread():
    job, dialog, async_cb = _job_with_services()
    assert job._trigger_native_update_dialog(timeout=2) is True
    async_cb.addCallback.assert_called_once()
    dialog.trigger.assert_called_once_with("SHOW_UPDATE_DIALOG")


def test_trigger_returns_false_when_dialog_raises():
    job, dialog, _ = _job_with_services()
    dialog.trigger.side_effect = RuntimeError("Cannot initialize VCL")
    assert job._trigger_native_update_dialog(timeout=2) is False


def test_trigger_times_out_when_main_thread_unavailable():
    job, dialog, _ = _job_with_services(run_callback=False)
    assert job._trigger_native_update_dialog(timeout=0.2) is False
    dialog.trigger.assert_not_called()


def test_trigger_late_callback_after_timeout_is_noop():
    """Le main thread se libère APRÈS le timeout : le rappel ne doit plus ouvrir
    le dialogue (l'appelant a déjà dégradé vers la route dirigée)."""
    job, dialog, async_cb = _job_with_services(run_callback=False)
    captured = {}
    async_cb.addCallback.side_effect = lambda cb, data: captured.setdefault("cb", cb)
    assert job._trigger_native_update_dialog(timeout=0.2) is False
    captured["cb"].notify(None)
    dialog.trigger.assert_not_called()


def test_main_thread_install_resolves_the_real_extension_manager_singleton():
    """Le singleton UNO est com.sun.star.deployment.ExtensionManager ; le nom
    theExtensionManager n'existe pas (getValueByName → None) : l'install
    retomberait toujours sur le chemin worker."""
    job, _dialog, _async_cb = _job_with_services()
    mgr = MagicMock(name="ExtensionManager")
    def _by_name(path):
        return mgr if path == "/singletons/com.sun.star.deployment.ExtensionManager" else None
    job.ctx.getValueByName = MagicMock(side_effect=_by_name)
    assert job._run_install_on_main_thread("file:///x.oxt", (), None, timeout=2) is True
    mgr.addExtension.assert_called_once()
    assert job._main_thread_install_in_flight is False


def _native_job(versions, trigger=True):
    """versions : réponses successives de _get_extension_version — version_before,
    puis la surveillance (la dernière valeur reste collante)."""
    job = _job()
    seq = list(versions)
    job._get_extension_version = MagicMock(side_effect=lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    job._wait_before_prompting = MagicMock()
    job._trigger_native_update_dialog = MagicMock(return_value=trigger)
    job._close_after_inprocess_update = MagicMock()
    job._package_cache_dir = lambda d=_mkdtemp(): d
    return job


DIRECTIVE = {"action": "update", "target_version": TARGET, "campaign_id": 7,
             "artifact_url": "/catalog/mirai-libreoffice/download", "urgency": "normal"}


def _events(job):
    return [c.args[0] for c in job._send_telemetry.call_args_list]


def test_native_update_installed_then_closes(native_timing):
    job = _native_job([CURRENT, CURRENT, CURRENT, TARGET])
    assert job._perform_native_update(DIRECTIVE) is True
    job._wait_before_prompting.assert_called_once()
    job._trigger_native_update_dialog.assert_called_once()
    job._report_update_status.assert_called_once_with(7, "deferred", CURRENT, TARGET)
    job._close_after_inprocess_update.assert_called_once()
    state = _state(job)
    assert state["stage"] == "installed_native"
    assert state["route"] == "native"
    assert state["native_attempts"] == 1
    assert state["version_before"] == CURRENT
    assert _events(job) == ["UpdateNativeDialogShown", "UpdateInstalledPendingRestart"]
    attrs = job._send_telemetry.call_args_list[1].args[1]
    assert attrs["route"] == "native" and attrs["version_after"] == TARGET


def test_native_update_postponed_when_nothing_installed(native_timing):
    job = _native_job([CURRENT])
    before = time.time()
    native_timing(wait=0.05)
    assert job._perform_native_update(DIRECTIVE) is True

    job._close_after_inprocess_update.assert_not_called()
    state = _state(job)
    assert state["stage"] == "postponed"
    assert state["route"] == "native"
    assert state["postponed_until"] >= before + entrypoint._UPDATE_POSTPONE_SECONDS - 1
    assert _events(job) == ["UpdateNativeDialogShown", "UpdatePostponed"]
    assert job._send_telemetry.call_args_list[1].args[1]["route"] == "native"


def test_native_update_counts_attempts_for_same_target(native_timing):
    job = _native_job([CURRENT])
    job._save_update_state(DIRECTIVE, "postponed", route="native", native_attempts=1)
    native_timing(wait=0.05)
    job._perform_native_update(DIRECTIVE)
    assert _state(job)["native_attempts"] == 2
    attrs = job._send_telemetry.call_args_list[0].args[1]
    assert attrs["attempt"] == "2"


def test_native_update_returns_false_without_side_effects_when_trigger_fails(native_timing):
    job = _native_job([CURRENT], trigger=False)
    native_timing(wait=0.05)
    assert job._perform_native_update(DIRECTIVE) is False
    job._report_update_status.assert_not_called()
    job._send_telemetry.assert_not_called()
    assert not os.path.isfile(job._update_state_path())


def test_native_attempts_for_normalises_target_and_tolerates_corruption():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "native_dialog",
                           route="native", native_attempts=1)
    assert job._native_attempts_for(TARGET) == 1
    assert job._native_attempts_for(f"  {TARGET} ") == 1
    assert job._native_attempts_for("0.0.1.0.99") == 0
    assert job._native_attempts_for("") == 0
    with open(job._update_state_path(), "w", encoding="utf-8") as fh:
        json.dump({"target_version": TARGET, "native_attempts": "x"}, fh)
    assert job._native_attempts_for(TARGET) == 0


def test_native_update_still_true_when_close_raises(native_timing):
    """Installée mais fermeture en échec : l'issue reste « installée » (état
    installed_native), jamais un rapport failed."""
    job = _native_job([CURRENT, CURRENT, TARGET])
    job._close_after_inprocess_update = MagicMock(side_effect=RuntimeError("terminate"))
    assert job._perform_native_update(DIRECTIVE) is True
    assert _state(job)["stage"] == "installed_native"


def _routing_job(feed_offers=True, native_result=True):
    job = _job()
    job._native_feed_offers = MagicMock(return_value=feed_offers)
    job._perform_native_update = MagicMock(return_value=native_result)
    job._wait_before_prompting = MagicMock()
    job.get_ssl_context = MagicMock()
    job._urlopen = MagicMock()
    job._urlopen.return_value.__enter__.return_value.read.return_value = b"oxt-bytes"
    job._failover_ordered_urls = MagicMock(return_value=["https://dm.example"])
    return job


def test_perform_update_takes_native_route_without_downloading():
    job = _routing_job()
    job._perform_update(dict(DIRECTIVE))
    job._native_feed_offers.assert_called_once_with(TARGET)
    job._perform_native_update.assert_called_once()
    job._urlopen.assert_not_called()


def test_perform_update_falls_back_to_directed_route_when_native_fails():
    job = _routing_job(native_result=False)
    job._perform_update(dict(DIRECTIVE))
    job._perform_native_update.assert_called_once()
    assert job._urlopen.called, "la route dirigée doit télécharger"


def test_perform_update_skips_native_when_feed_diverges():
    job = _routing_job(feed_offers=False)
    job._perform_update(dict(DIRECTIVE))
    job._perform_native_update.assert_not_called()
    assert job._urlopen.called


def test_perform_update_skips_native_for_deferred_urgency_and_rollback():
    job = _routing_job()
    job._perform_update(dict(DIRECTIVE, urgency="deferred"))
    job._perform_update(dict(DIRECTIVE, action="rollback"))
    job._native_feed_offers.assert_not_called()
    job._perform_native_update.assert_not_called()


def test_perform_update_skips_native_after_max_attempts():
    job = _routing_job()
    job._save_update_state(DIRECTIVE, "postponed", route="native",
                           native_attempts=entrypoint._NATIVE_MAX_ATTEMPTS)
    job._perform_update(dict(DIRECTIVE))
    job._native_feed_offers.assert_not_called()
    assert job._urlopen.called


def test_directed_route_postponed_sets_cooldown():
    """Route dirigée, l'utilisateur répond « Non » (msgbox mocké ≠ 2) → état
    postponed avec échéance, route « directed »."""
    job = _routing_job(feed_offers=False)
    before = time.time()
    job._perform_update(dict(DIRECTIVE))
    state = _state(job)
    assert state["stage"] == "postponed"
    assert state["route"] == "directed"
    assert state["postponed_until"] >= before + entrypoint._UPDATE_POSTPONE_SECONDS - 1
    assert "UpdatePostponed" in _events(job)


UPDATE_EVENTS = {
    "UpdateStaged", "UpdateAccepted", "UpdatePostponed", "UpdateInstalledPendingRestart",
    "UpdateInstallFailed", "UpdateNativeDialogShown", "ExtensionUpdated", "NativeFeedCheck",
    "UpdateCloseDeferred",
}


def test_update_events_are_technical():
    """Sans cela, le pipeline sécurisé jette ces spans avant la liaison
    utilisateur : l'entonnoir de campagne serait aveugle sur une partie du parc."""
    assert UPDATE_EVENTS <= MainJob._TECHNICAL_EVENTS


def test_action_names_cover_native_dialog_event():
    assert MainJob._ACTION_NAMES["UpdateNativeDialogShown"] == "update"


def test_reconcile_joins_route_to_extension_updated():
    job = _job(current_version=TARGET)
    job._save_update_state(DIRECTIVE, "installed_native", route="native")
    job._reconcile_update_state()
    updated = [c for c in job._send_telemetry.call_args_list if c.args[0] == "ExtensionUpdated"]
    assert len(updated) == 1
    assert updated[0].args[1]["route"] == "native"
    assert updated[0].args[1]["confirmed"] == "true"


def _blocking_dialog_job(block):
    """Le callback main-thread est capturé et exécuté sur un thread qui bloque
    dans dialog.trigger jusqu'à `block.set()` : simule une action encore en
    cours quand le worker atteint son timeout."""
    job, dialog, async_cb = _job_with_services(run_callback=False)
    captured = {}
    async_cb.addCallback.side_effect = lambda cb, data: captured.setdefault("cb", cb)
    dialog.trigger.side_effect = lambda _evt: block.wait(5)

    def _run_later():
        while "cb" not in captured:
            time.sleep(0.01)
        captured["cb"].notify(None)

    threading.Thread(target=_run_later, daemon=True).start()
    return job, dialog


def test_run_on_main_thread_reports_timeout_after_start():
    block = threading.Event()
    job, dialog = _blocking_dialog_job(block)
    try:
        ok, err = job._run_on_main_thread(
            lambda: dialog.trigger("SHOW_UPDATE_DIALOG"), 0.3, "test")
        assert ok is False
        assert err == entrypoint._MAIN_THREAD_TIMEOUT_AFTER_START
    finally:
        block.set()


def test_run_on_main_thread_late_callback_never_starts_the_action():
    """Le callback est livré APRÈS l'expiration : l'action ne doit pas démarrer
    (sinon deux flux d'installation concurrents), et l'issue vue par l'appelant
    reste « timeout », jamais « timeout after start »."""
    job, _dialog, async_cb = _job_with_services(run_callback=False)
    captured = {}
    async_cb.addCallback.side_effect = lambda cb, data: captured.setdefault("cb", cb)
    ran = []
    ok, err = job._run_on_main_thread(lambda: ran.append(1), 0.1, "test")
    assert (ok, err) == (False, "timeout")
    captured["cb"].notify(None)
    assert ran == []
    assert job._last_main_thread_action_s == 0.0


def test_trigger_counts_started_timeout_as_triggered():
    """L'action a démarré mais dure : l'effet est en cours, on ne bascule PAS
    en route dirigée (deux flux d'installation concurrents sinon)."""
    block = threading.Event()
    job, dialog = _blocking_dialog_job(block)
    try:
        assert job._trigger_native_update_dialog(timeout=0.3) is True
        dialog.trigger.assert_called_once_with("SHOW_UPDATE_DIALOG")
    finally:
        block.set()


def _set_open_documents(desktop, modified_flags):
    """Composants ouverts énumérés par le Desktop, avec leur drapeau
    isModified() ; l'énumération est rejouable (une par sonde)."""
    docs = []
    for flag in modified_flags:
        doc = MagicMock(name="Document")
        doc.isModified.return_value = flag
        docs.append(doc)
    enumeration = desktop.getComponents.return_value.createEnumeration.return_value
    enumeration.hasMoreElements.side_effect = itertools.cycle([True] * len(docs) + [False])
    enumeration.nextElement.side_effect = itertools.cycle(docs) if docs else None


def _message_texts(job):
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    return [c.args[4] for c in toolkit.createMessageBox.call_args_list]


def _deferred_messages(job):
    return [t for t in _message_texts(job) if "prochain démarrage" in t]


def _close_job(terminate_results, modified_documents=()):
    """Desktop.terminate() renvoie successivement les valeurs données ;
    `modified_documents` décrit les documents ouverts (modifiés ou non)."""
    job, _dialog, _async_cb = _job_with_services()
    smgr = job.ctx.getServiceManager.return_value
    desktop = MagicMock(name="Desktop")
    desktop.terminate.side_effect = list(terminate_results)
    _set_open_documents(desktop, modified_documents)
    previous = smgr.createInstanceWithContext.side_effect
    smgr.createInstanceWithContext.side_effect = (
        lambda name, ctx: desktop if name == "com.sun.star.frame.Desktop" else previous(name, ctx))
    job._terminate_on_main_thread = MagicMock()
    return job, desktop


def test_close_after_inprocess_update_retries_until_veto_clears(monkeypatch):
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    job, desktop = _close_job([False, False, True])
    assert job._close_after_inprocess_update() is True
    assert desktop.terminate.call_count == 3
    job._terminate_on_main_thread.assert_not_called()
    assert _deferred_messages(job) == [], "fermeture aboutie : aucune boîte de report"


def test_close_after_inprocess_update_treats_veto_with_modified_document_as_refusal(monkeypatch):
    """Un document porte des modifications non enregistrées : le veto vient du
    dialogue « Enregistrer les modifications ? », donc de l'utilisateur — une
    seule tentative, et il est informé que la MAJ s'activera au redémarrage."""
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    job, desktop = _close_job([False, False, True], modified_documents=(False, True))
    assert job._close_after_inprocess_update() is False
    assert desktop.terminate.call_count == 1
    assert len(_deferred_messages(job)) == 1
    job._terminate_on_main_thread.assert_not_called()


def test_close_after_inprocess_update_gives_up_after_deadline(monkeypatch):
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_SECONDS", 0.05)
    job, desktop = _close_job([False] * 50)
    assert job._close_after_inprocess_update() is False
    assert desktop.terminate.call_count >= 2
    job._terminate_on_main_thread.assert_not_called()
    assert len(_deferred_messages(job)) == 1, "une seule boîte, à l'abandon"


def test_close_after_inprocess_update_respects_user_refusal(monkeypatch):
    """Un veto lent = l'utilisateur a répondu Annuler à « Enregistrer ? » :
    une seule tentative, pas de harcèlement."""
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(entrypoint, "_CLOSE_USER_REFUSAL_SECONDS", 0.05)
    job, desktop = _close_job([False, False, True])
    slow = desktop.terminate.side_effect
    def _slow():
        time.sleep(0.1)
        return next(slow)
    desktop.terminate.side_effect = _slow
    assert job._close_after_inprocess_update() is False
    assert desktop.terminate.call_count == 1


def test_close_after_inprocess_update_ignores_scheduling_latency(monkeypatch):
    """Thread principal lent à prendre le callback, mais veto instantané dans
    terminate() : ce n'est PAS un refus humain → on réessaie et on aboutit."""
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(entrypoint, "_CLOSE_USER_REFUSAL_SECONDS", 0.05)
    job, _dialog, async_cb = _job_with_services(run_callback=False)
    async_cb.addCallback.side_effect = (
        lambda cb, data: threading.Timer(0.2, cb.notify, args=(data,)).start())
    smgr = job.ctx.getServiceManager.return_value
    desktop = MagicMock(name="Desktop")
    desktop.terminate.side_effect = [False, True]
    _set_open_documents(desktop, ())
    previous = smgr.createInstanceWithContext.side_effect
    smgr.createInstanceWithContext.side_effect = (
        lambda name, ctx: desktop if name == "com.sun.star.frame.Desktop" else previous(name, ctx))
    job._terminate_on_main_thread = MagicMock()
    assert job._close_after_inprocess_update() is True
    assert desktop.terminate.call_count == 2
    job._terminate_on_main_thread.assert_not_called()


def test_run_on_main_thread_exposes_action_duration():
    job, dialog, _async_cb = _job_with_services()
    dialog.trigger.side_effect = lambda _evt: time.sleep(0.05)
    ok, _err = job._run_on_main_thread(lambda: dialog.trigger("SHOW_UPDATE_DIALOG"), 2, "t")
    assert ok is True
    assert job._last_main_thread_action_s >= 0.04


def test_close_after_inprocess_update_retries_when_main_thread_busy(monkeypatch):
    """Callback jamais démarré (thread principal occupé) → on réessaie, JAMAIS de SIGTERM."""
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_SECONDS", 0.05)
    job, _dialog, async_cb = _job_with_services(run_callback=False)
    job._terminate_on_main_thread = MagicMock()
    job._run_on_main_thread = MagicMock(return_value=(False, "timeout"))
    assert job._close_after_inprocess_update() is False
    assert job._run_on_main_thread.call_count >= 2
    job._terminate_on_main_thread.assert_not_called()


def test_close_after_inprocess_update_sigterm_only_when_unreachable():
    job, _dialog, _async_cb = _job_with_services()
    job._terminate_on_main_thread = MagicMock()
    job._run_on_main_thread = MagicMock(
        return_value=(False, entrypoint._MAIN_THREAD_ASYNC_UNAVAILABLE))
    assert job._close_after_inprocess_update() is False
    job._terminate_on_main_thread.assert_called_once()


def test_close_after_inprocess_update_sigterm_when_scheduling_fails():
    """addCallback en échec : le thread principal est injoignable, SIGTERM reste
    la seule sortie."""
    job, _dialog, _async_cb = _job_with_services()
    job._terminate_on_main_thread = MagicMock()
    job._run_on_main_thread = MagicMock(
        return_value=(False, entrypoint._MAIN_THREAD_SCHEDULE_FAILED + "boom"))
    assert job._close_after_inprocess_update() is False
    job._terminate_on_main_thread.assert_called_once()


def test_close_after_inprocess_update_no_sigterm_on_action_error(monkeypatch):
    """Le Desktop se dérobe (service en cours de disposition) alors que le thread
    principal a bien exécuté le callback : tuer le processus à coups de SIGTERM
    perdrait les documents non enregistrés — on rend la main, la MAJ s'activera
    au prochain démarrage."""
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 0.01)
    job, desktop = _close_job([])
    desktop.terminate.side_effect = RuntimeError("Desktop disposé")
    assert job._close_after_inprocess_update() is False
    assert desktop.terminate.call_count == 1
    job._terminate_on_main_thread.assert_not_called()


def test_schedule_update_skips_target_installed_pending_restart():
    job = _job()
    job._save_update_state({"campaign_id": 9, "target_version": TARGET}, "installed_native", route="native")
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is False


def test_native_close_deferred_emits_telemetry(native_timing):
    job = _native_job([CURRENT, CURRENT, TARGET, TARGET])
    job._close_after_inprocess_update = MagicMock(return_value=False)
    assert job._perform_native_update(DIRECTIVE) is True
    assert "UpdateCloseDeferred" in _events(job)


def _accepting_job():
    """Route dirigée, l'utilisateur accepte le redémarrage (msgbox → YES = 2)."""
    job = _routing_job(feed_offers=False)
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    toolkit.createMessageBox.return_value.execute.return_value = 2
    return job


def test_install_in_flight_is_not_reported_as_a_failure():
    """addExtension encore en vol sur le thread principal : ni rapport failed, ni
    bannissement de la cible, ni boîte « mise à jour bloquée » — l'étape
    installed_inprocess laisse la réconciliation trancher au redémarrage."""
    MainJob._update_launch_blocked_cls.discard(TARGET)
    job = _accepting_job()
    job._notify_update_blocked = MagicMock()

    def _install(_path, *_args, **_kwargs):
        job._main_thread_install_in_flight = True
        return False

    job._install_and_restart_in_process = _install
    job._perform_update(dict(DIRECTIVE))

    assert _state(job)["stage"] == "installed_inprocess"
    job._notify_update_blocked.assert_not_called()
    assert not [c for c in job._report_update_status.call_args_list if c.args[1] == "failed"]
    assert TARGET not in MainJob._update_launch_blocked_cls
    assert "UpdateInstallFailed" not in _events(job)


def test_directed_close_deferred_carries_version_and_campaign():
    """UpdateCloseDeferred de la route dirigée doit porter la version et la
    campagne, comme la variante native — sans quoi l'entonnoir ne relie pas une
    fermeture reportée à sa campagne."""
    job = _accepting_job()
    job._run_install_on_main_thread = MagicMock(return_value=True)
    job._close_after_inprocess_update = MagicMock(return_value=False)
    job._perform_update(dict(DIRECTIVE))
    deferred = [c.args[1] for c in job._send_telemetry.call_args_list
                if c.args[0] == "UpdateCloseDeferred"]
    assert deferred == [{"route": "directed", "version_after": TARGET, "campaign_id": "7"}]


def test_install_in_flight_skips_legacy_worker_path():
    """addExtension encore en cours sur le main thread → pas de repli
    thePackageManagerFactory depuis le worker (double install)."""
    job = _job()
    fd, path = tempfile.mkstemp(suffix=".oxt")
    os.close(fd)
    try:
        def _fake_install(*_a, **_k):
            job._main_thread_install_in_flight = True
            return False
        job._run_install_on_main_thread = _fake_install
        job._install_oxt_inprocess = MagicMock(return_value=True)
        job._close_after_inprocess_update = MagicMock()
        assert job._install_and_restart_in_process(path) is False
        job._install_oxt_inprocess.assert_not_called()
        job._close_after_inprocess_update.assert_not_called()
    finally:
        os.remove(path)


def _job_with_extension_list(pairs):
    job = make_job(config_dir=_mkdtemp())
    smgr = job.ctx.getServiceManager.return_value
    default = smgr.createInstanceWithContext.return_value
    pip = MagicMock(name="PackageInformationProvider")
    pip.getExtensionList.return_value = tuple(pairs)
    smgr.createInstanceWithContext.side_effect = (
        lambda name, ctx: pip if name == "com.sun.star.deployment.PackageInformationProvider" else default)
    return job, pip


def test_get_extension_version_reads_registry_pairs():
    job, pip = _job_with_extension_list([("org.example.other", "9.9"), ("fr.gouv.interieur.mirai", " 0.0.1.0.32 ")])
    assert job._get_extension_version() == "0.0.1.0.32"
    pip.getExtensionList.assert_called_once()
    assert not pip.getExtensionVersion.called


def test_get_extension_version_falls_back_when_registry_unavailable():
    """Registre injoignable → repli description.xml (ici absent) → chaîne vide,
    jamais d'exception."""
    job, pip = _job_with_extension_list([])
    pip.getExtensionList.side_effect = RuntimeError("no registry")
    assert job._get_extension_version() == ""


def test_native_poll_sees_new_version_from_registry(native_timing):
    """Après l'installation native, l'ancien module tourne encore ; il doit voir
    la nouvelle version via le registre, pas via son propre dossier disparu."""
    job, pip = _job_with_extension_list([("fr.gouv.interieur.mirai", CURRENT)])
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    job._wait_before_prompting = MagicMock()
    job._trigger_native_update_dialog = MagicMock(return_value=True)
    job._close_after_inprocess_update = MagicMock()
    calls = {"n": 0}
    def _list():
        calls["n"] += 1
        return ((("fr.gouv.interieur.mirai", TARGET),) if calls["n"] >= 3 else (("fr.gouv.interieur.mirai", CURRENT),))
    pip.getExtensionList.side_effect = _list
    assert job._perform_native_update(DIRECTIVE) is True
    job._close_after_inprocess_update.assert_called_once()


def test_wait_before_prompting_rechecks_wizard_after_grace(monkeypatch):
    """L'assistant d'enrôlement s'ouvre PENDANT la grâce : la seconde
    vérification doit le voir et attendre sa fermeture."""
    monkeypatch.setattr(entrypoint, "_PROMPT_GRACE_SECONDS", 0.15)
    monkeypatch.setattr(entrypoint, "_PROMPT_WIZARD_WAIT_SECONDS", 5)
    monkeypatch.setattr(entrypoint, "_PROMPT_POLL_SECONDS", 0.02)
    job = _job()
    previous = MainJob._enrollment_wizard_active_cls
    MainJob._enrollment_wizard_active_cls = False

    def _open_then_close():
        time.sleep(0.05)
        with MainJob._enrollment_wizard_lock_cls:
            MainJob._enrollment_wizard_active_cls = True
        time.sleep(0.25)
        with MainJob._enrollment_wizard_lock_cls:
            MainJob._enrollment_wizard_active_cls = False

    wizard = threading.Thread(target=_open_then_close, daemon=True)
    wizard.start()
    try:
        t0 = time.time()
        job._wait_before_prompting()
        assert time.time() - t0 >= 0.25, "doit attendre la fermeture de l'assistant ouvert pendant la grâce"
        assert MainJob._enrollment_wizard_active_cls is False
    finally:
        wizard.join(2)
        MainJob._enrollment_wizard_active_cls = previous


def _fake_cache(versions):
    """Cache <cache>/<lu>/<pkg>.oxt/description.xml pour chaque version donnée."""
    cache = _mkdtemp()
    for i, v in enumerate(versions):
        pkg = os.path.join(cache, f"lu{i}.tmp_", f"mirai-libreoffice-{v}.oxt")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "description.xml"), "w", encoding="utf-8") as fh:
            fh.write('<description><identifier value="fr.gouv.interieur.mirai"/>'
                     f'<version value="{v}"/></description>')
    other = os.path.join(cache, "luX.tmp_", "other.oxt")
    os.makedirs(other)
    with open(os.path.join(other, "description.xml"), "w", encoding="utf-8") as fh:
        fh.write('<description><identifier value="org.example.other"/><version value="9"/></description>')
    return cache


def test_package_cache_dir_anchors_on_description_xml(monkeypatch):
    """Le dossier scruté est déduit de la racine du paquet (le dossier qui
    contient description.xml), pas d'un compteur de niveaux : une réorganisation
    de src/mirai/ ne doit pas déplacer silencieusement la sonde."""
    cache = _mkdtemp()
    pkg = os.path.join(cache, "lu42.tmp_", "mirai-libreoffice.oxt")
    os.makedirs(os.path.join(pkg, "python", "src", "mirai"))
    open(os.path.join(pkg, "description.xml"), "w").close()
    monkeypatch.setattr(entrypoint, "__file__",
                        os.path.join(pkg, "python", "src", "mirai", "entrypoint.py"))
    job = make_job(config_dir=_mkdtemp())
    assert job._package_cache_dir() == cache


def test_package_cache_dir_falls_back_to_fixed_depth_without_description_xml(monkeypatch):
    """Aucun description.xml au-dessus (tests, arborescence inattendue) :
    comportement historique, cinq niveaux."""
    root = _mkdtemp()
    deep = os.path.join(root, "cache", "lu42.tmp_", "mirai.oxt", "src", "mirai")
    os.makedirs(deep)
    monkeypatch.setattr(entrypoint, "__file__", os.path.join(deep, "entrypoint.py"))
    job = make_job(config_dir=_mkdtemp())
    assert job._package_cache_dir() == os.path.join(root, "cache")


def test_cached_package_versions_lists_our_identifier_only():
    job = _job()
    job._package_cache_dir = lambda: _fake_cache([CURRENT, TARGET])
    assert job._versions_of(job._cached_package_versions()) == {CURRENT, TARGET}


def test_cached_package_versions_never_raises():
    job = _job()
    job._package_cache_dir = lambda: "/nonexistent/cache/dir"
    assert job._cached_package_versions() == set()


def test_native_poll_detects_install_via_package_cache_when_registry_stale(native_timing):
    """En session, getExtensionList renvoie toujours l'ancienne version, mais
    le dossier du nouveau paquet est dans le cache.
    Détection confirmée sur deux lectures consécutives (les polls #2 et #3)."""
    job = _native_job([CURRENT])
    calls = {"n": 0}
    def _cache():
        calls["n"] += 1
        return _fake_cache([CURRENT, TARGET] if calls["n"] >= 3 else [CURRENT])
    job._package_cache_dir = _cache
    assert job._perform_native_update(DIRECTIVE) is True
    job._close_after_inprocess_update.assert_called_once()
    assert _state(job)["stage"] == "installed_native"


def test_native_poll_ignores_target_folder_present_before_dialog(native_timing):
    """Dossier résiduel d'une tentative antérieure : présent AVANT le dialogue,
    il ne doit pas compter comme une installation."""
    job = _native_job([CURRENT])
    cache = _fake_cache([CURRENT, TARGET])
    job._package_cache_dir = lambda: cache
    native_timing(wait=0.2)
    assert job._perform_native_update(DIRECTIVE) is True
    job._close_after_inprocess_update.assert_not_called()
    assert _state(job)["stage"] == "postponed"


def test_schedule_update_reconciles_previous_target_before_new_directive():
    """Cas du banc : la directive suivante arrive avant le timer de
    réconciliation. La campagne précédente est active (registre à jour après
    redémarrage) : elle doit être rapportée installed et purgée, puis la
    nouvelle directive s'exécuter."""
    job = _job(current_version=CURRENT)
    job._save_update_state({"campaign_id": 6, "target_version": CURRENT}, "installed_native", route="native")
    done = threading.Event()
    job._perform_update = lambda d: done.set()
    job._schedule_update({"action": "update", "target_version": TARGET, "campaign_id": 7})
    assert done.wait(2)
    installed_calls = [c for c in job._report_update_status.call_args_list if c.args[0] == 6 and c.args[1] == "installed"]
    assert installed_calls, "la campagne précédente doit être rapportée installed avant la nouvelle directive"
    assert not os.path.isfile(job._update_state_path()), "l'état réconcilié doit être purgé"


def test_schedule_update_defers_new_directive_while_previous_install_pending_restart():
    """Cible X installée, fermeture refusée, l'utilisateur continue de travailler :
    en session le registre reste sur l'ancienne version. Une directive Y ne doit
    ni s'exécuter ni écraser l'état de X — sinon la campagne X ne serait jamais
    rapportée installed."""
    job = _job(current_version=CURRENT)
    job._save_update_state({"campaign_id": 6, "target_version": TARGET}, "installed_native", route="native")
    assert _schedule_and_wait(
        job, {"action": "update", "target_version": "0.0.1.0.33", "campaign_id": 7}) is False
    state = _state(job)
    assert state["target_version"] == TARGET
    assert state["stage"] == "installed_native"
    assert state["campaign_id"] == 6


def test_close_helpers_survive_disposed_context():
    """Contexte disposé pendant la boucle de fermeture : la sonde répond Faux
    et la boîte d'information s'abstient, sans exception remontée jusqu'à
    _perform_update (qui rapporterait un échec à tort)."""
    job = _job()
    job.ctx.getServiceManager.side_effect = RuntimeError("disposed")
    assert job._has_modified_documents() is False
    job._notify_update_activates_at_restart()
