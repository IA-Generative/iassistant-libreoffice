"""Mise à jour pilotée par le DM : le DM décide (directive), le plugin
télécharge, demande l'accord, installe sur le thread principal et ferme
LibreOffice proprement ; refus mémorisé 24 h.
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

from tests.stubs.helpers import http_response
from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint
from src.mirai.shell import runtime as shell_runtime

TARGET = "0.0.1.0.32"
CURRENT = "0.0.1.0.31"

_TMPDIRS = []


def _mkdtemp():
    """Répertoire temporaire supprimé à la fin du module (les jobs écrivent un
    profil factice à chaque test)."""
    path = tempfile.mkdtemp()
    _TMPDIRS.append(path)
    return path


@pytest.fixture(autouse=True, scope="module")
def _purge_tmpdirs():
    yield
    for path in _TMPDIRS:
        shutil.rmtree(path, ignore_errors=True)
    _TMPDIRS.clear()


def _job(current_version=CURRENT):
    job = make_job(config_dir=_mkdtemp())
    job._get_extension_version = MagicMock(return_value=current_version)
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    return job


def _state(job):
    with open(job._update_state_path(), encoding="utf-8") as fh:
        return json.load(fh)


def test_constants_match_spec():
    assert entrypoint._UPDATE_POSTPONE_SECONDS == 24 * 3600
    assert entrypoint._RECONCILE_RECHECK_SECONDS == 5
    assert entrypoint._CLOSE_RETRY_SECONDS == 120
    assert entrypoint._CLOSE_RETRY_INTERVAL_SECONDS == 3
    assert entrypoint._PROMPT_WIZARD_WAIT_SECONDS == 120
    assert entrypoint._PROMPT_GRACE_SECONDS == 30
    assert entrypoint._CLOSE_USER_REFUSAL_SECONDS == 1.0


def test_save_update_state_persists_route_and_cooldown_fields():
    job = _job()
    until = time.time() + 3600
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="directed", postponed_until=until)
    state = _state(job)
    assert state["route"] == "directed"
    assert state["postponed_until"] == until
    assert state["stage"] == "postponed"


def test_save_update_state_keeps_fields_for_same_target():
    """Un enregistrement d'étape ne perd pas la route posée avant."""
    job = _job()
    d = {"campaign_id": 3, "target_version": TARGET}
    job._save_update_state(d, "user_accepted", route="directed")
    job._save_update_state(d, "postponed", postponed_until=time.time() + 10)
    state = _state(job)
    assert state["route"] == "directed"
    assert state["postponed_until"] > time.time()


def test_save_update_state_resets_fields_for_new_target():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="directed", postponed_until=time.time() + 10)
    job._save_update_state({"campaign_id": 4, "target_version": "0.0.1.0.33"}, "staged")
    state = _state(job)
    assert state["target_version"] == "0.0.1.0.33"
    assert state["route"] == ""
    assert state["postponed_until"] == 0


def test_save_update_state_keeps_version_before_across_stages():
    """Après l'installation, la version active est déjà la cible : version_before
    doit rester celle d'avant, sinon la réconciliation rapporte n'importe quoi."""
    job = _job()
    d = {"campaign_id": 3, "target_version": TARGET}
    job._save_update_state(d, "user_accepted", route="directed")
    job._get_extension_version = MagicMock(return_value=TARGET)
    job._save_update_state(d, "installed_inprocess")
    assert _state(job)["version_before"] == CURRENT


def test_load_update_state_returns_empty_when_missing_or_corrupt():
    job = _job()
    assert job._load_update_state() == {}
    os.makedirs(os.path.dirname(job._update_state_path()), exist_ok=True)
    with open(job._update_state_path(), "w") as fh:
        fh.write("{not json")
    assert job._load_update_state() == {}


def _schedule_and_wait(job, directive, seconds=2):
    """Vrai si la planification a lancé l'installation ; attend la fin du
    worker plutôt qu'un délai."""
    done = threading.Event()
    job._perform_update = lambda d: done.set()
    before = set(threading.enumerate())
    job._schedule_update(directive)
    for thread in set(threading.enumerate()) - before:
        thread.join(seconds)
    return done.is_set()


def test_schedule_update_skips_target_in_cooldown():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="directed", postponed_until=time.time() + 3600)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is False


def test_schedule_update_runs_after_cooldown_expired():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="directed", postponed_until=time.time() - 1)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is True


def test_schedule_update_ignores_cooldown_of_other_target():
    job = _job()
    job._save_update_state({"campaign_id": 3, "target_version": TARGET}, "postponed",
                           route="directed", postponed_until=time.time() + 3600)
    assert _schedule_and_wait(job, {"action": "update", "target_version": "0.0.1.0.33"}) is True


def test_schedule_update_tolerates_corrupt_postponed_until():
    """Un état corrompu ne doit jamais faire échouer le rafraîchissement de config."""
    job = _job()
    os.makedirs(os.path.dirname(job._update_state_path()), exist_ok=True)
    with open(job._update_state_path(), "w", encoding="utf-8") as fh:
        json.dump({"target_version": TARGET, "postponed_until": "x"}, fh)
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is True


ASYNC = "com.sun.star.awt.AsyncCallback"


def _job_with_services(run_callback=True):
    """AsyncCallback synchrone (le main thread est disponible tout de suite)
    ou inerte (run_callback=False)."""
    job = _job()
    smgr = job.ctx.getServiceManager.return_value
    default = smgr.createInstanceWithContext.return_value
    async_cb = MagicMock(name="AsyncCallback")
    if run_callback:
        async_cb.addCallback.side_effect = lambda cb, data: cb.notify(data)
    smgr.createInstanceWithContext.side_effect = (
        lambda name, ctx: async_cb if name == ASYNC else default)
    return job, async_cb


def test_main_thread_install_resolves_the_real_extension_manager_singleton():
    """Le singleton UNO est com.sun.star.deployment.ExtensionManager ; le nom
    theExtensionManager n'existe pas (getValueByName → None) : l'install
    retomberait toujours sur le chemin worker."""
    job, _async_cb = _job_with_services()
    mgr = MagicMock(name="ExtensionManager")
    def _by_name(path):
        return mgr if path == "/singletons/com.sun.star.deployment.ExtensionManager" else None
    job.ctx.getValueByName = MagicMock(side_effect=_by_name)
    assert job._run_install_on_main_thread("file:///x.oxt", (), None, timeout=2) is True
    mgr.addExtension.assert_called_once()
    assert job._main_thread_install_in_flight is False


DIRECTIVE = {"action": "update", "target_version": TARGET, "campaign_id": 7,
             "artifact_url": "/catalog/mirai-libreoffice/download", "urgency": "normal"}


def _events(job):
    return [c.args[0] for c in job._send_telemetry.call_args_list]


def _directed_job():
    job = _job()
    job._wait_before_prompting = MagicMock()
    job.get_ssl_context = MagicMock()
    job._urlopen = MagicMock(return_value=http_response(b"oxt-bytes"))
    job._failover_ordered_urls = MagicMock(return_value=["https://dm.example"])
    return job


def test_directed_route_postponed_sets_cooldown():
    """Route dirigée, l'utilisateur répond « Non » (msgbox mocké ≠ 2) → état
    postponed avec échéance, route « directed »."""
    job = _directed_job()
    before = time.time()
    job._perform_update(dict(DIRECTIVE))
    state = _state(job)
    assert state["stage"] == "postponed"
    assert state["route"] == "directed"
    assert state["postponed_until"] >= before + entrypoint._UPDATE_POSTPONE_SECONDS - 1
    assert "UpdatePostponed" in _events(job)


def test_reconcile_joins_route_to_extension_updated():
    job = _job(current_version=TARGET)
    job._save_update_state(DIRECTIVE, "installed_inprocess", route="directed")
    job._reconcile_update_state()
    updated = [c for c in job._send_telemetry.call_args_list if c.args[0] == "ExtensionUpdated"]
    assert len(updated) == 1
    assert updated[0].args[1]["route"] == "directed"
    assert updated[0].args[1]["confirmed"] == "true"


def _late_main_thread_job():
    """Le callback main-thread est capturé puis exécuté plus tard, sur un autre
    thread : l'action peut encore tourner quand le worker atteint son timeout."""
    job, async_cb = _job_with_services(run_callback=False)
    captured = {}
    async_cb.addCallback.side_effect = lambda cb, data: captured.setdefault("cb", cb)

    def _run_later():
        while "cb" not in captured:
            time.sleep(0.01)
        captured["cb"].notify(None)

    threading.Thread(target=_run_later, daemon=True).start()
    return job


def test_run_on_main_thread_reports_timeout_after_start():
    block = threading.Event()
    job = _late_main_thread_job()
    try:
        ok, err = job._run_on_main_thread(lambda: block.wait(5), 0.3, "test")
        assert ok is False
        assert err == entrypoint._MAIN_THREAD_TIMEOUT_AFTER_START
    finally:
        block.set()


def test_run_on_main_thread_late_callback_never_starts_the_action():
    """Le callback est livré APRÈS l'expiration : l'action ne doit pas démarrer
    (sinon deux flux d'installation concurrents), et l'issue vue par l'appelant
    reste « timeout », jamais « timeout after start »."""
    job, async_cb = _job_with_services(run_callback=False)
    captured = {}
    async_cb.addCallback.side_effect = lambda cb, data: captured.setdefault("cb", cb)
    ran = []
    ok, err = job._run_on_main_thread(lambda: ran.append(1), 0.1, "test")
    assert (ok, err) == (False, "timeout")
    captured["cb"].notify(None)
    assert ran == []
    assert job._last_main_thread_action_s == 0.0


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
    job, _async_cb = _job_with_services()
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


def test_close_after_inprocess_update_gives_up_after_deadline(monkeypatch, fast_clock):
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_SECONDS", 30)
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
    job, async_cb = _job_with_services(run_callback=False)
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
    job, _async_cb = _job_with_services()
    ok, _err = job._run_on_main_thread(lambda: time.sleep(0.05), 2, "t")
    assert ok is True
    assert job._last_main_thread_action_s >= 0.04


def test_close_after_inprocess_update_retries_when_main_thread_busy(monkeypatch, fast_clock):
    """Callback jamais démarré (thread principal occupé) → on réessaie, JAMAIS de SIGTERM."""
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(entrypoint, "_CLOSE_RETRY_SECONDS", 30)
    job, _async_cb = _job_with_services(run_callback=False)
    job._terminate_on_main_thread = MagicMock()
    job._run_on_main_thread = MagicMock(return_value=(False, "timeout"))
    assert job._close_after_inprocess_update() is False
    attempts = [c for c in job._run_on_main_thread.call_args_list
                if c.args[2] == "_close_after_inprocess_update"]
    assert len(attempts) >= 2
    job._terminate_on_main_thread.assert_not_called()


def test_close_after_inprocess_update_sigterm_only_when_unreachable():
    job, _async_cb = _job_with_services()
    job._terminate_on_main_thread = MagicMock()
    job._run_on_main_thread = MagicMock(
        return_value=(False, entrypoint._MAIN_THREAD_ASYNC_UNAVAILABLE))
    assert job._close_after_inprocess_update() is False
    job._terminate_on_main_thread.assert_called_once()


def test_close_after_inprocess_update_sigterm_when_scheduling_fails():
    """addCallback en échec : le thread principal est injoignable, SIGTERM reste
    la seule sortie."""
    job, _async_cb = _job_with_services()
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
    job._save_update_state({"campaign_id": 9, "target_version": TARGET}, "installed_inprocess",
                           route="directed")
    assert _schedule_and_wait(job, {"action": "update", "target_version": TARGET}) is False


def _accepting_job():
    """Route dirigée, l'utilisateur accepte le redémarrage (msgbox → YES = 2)."""
    job = _directed_job()
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    toolkit.createMessageBox.return_value.execute.return_value = 2
    return job


def test_install_in_flight_is_not_reported_as_a_failure():
    """addExtension encore en vol sur le thread principal : ni rapport failed, ni
    bannissement de la cible, ni boîte « mise à jour bloquée » — l'étape
    installed_inprocess laisse la réconciliation trancher au redémarrage."""
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
    assert TARGET not in shell_runtime.get().update_launch_blocked
    assert "UpdateInstallFailed" not in _events(job)


def test_failed_install_keeps_libreoffice_open_and_offers_the_manual_route():
    """Route dirigée acceptée, installation en échec sur le thread principal :
    LibreOffice reste ouvert, l'échec est rapporté au DM et la procédure manuelle
    est proposée."""
    job = _accepting_job()
    job._run_install_on_main_thread = MagicMock(return_value=False)
    job._close_after_inprocess_update = MagicMock()
    job._notify_update_blocked = MagicMock()

    job._perform_update(dict(DIRECTIVE))

    job._close_after_inprocess_update.assert_not_called()
    assert [c for c in job._report_update_status.call_args_list if c.args[1] == "failed"]
    job._notify_update_blocked.assert_called_once()


def test_directed_close_deferred_carries_version_and_campaign():
    """UpdateCloseDeferred de la route dirigée doit porter la version et la
    campagne — sans quoi l'entonnoir ne relie pas une fermeture reportée à sa
    campagne."""
    job = _accepting_job()
    job._run_install_on_main_thread = MagicMock(return_value=True)
    job._close_after_inprocess_update = MagicMock(return_value=False)
    job._perform_update(dict(DIRECTIVE))
    deferred = [c.args[1] for c in job._send_telemetry.call_args_list
                if c.args[0] == "UpdateCloseDeferred"]
    assert deferred == [{"route": "directed", "version_after": TARGET, "campaign_id": "7"}]


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


def test_wait_before_prompting_rechecks_wizard_after_grace(monkeypatch):
    """L'assistant d'enrôlement s'ouvre PENDANT la grâce : la seconde
    vérification doit le voir et attendre sa fermeture."""
    monkeypatch.setattr(entrypoint, "_PROMPT_GRACE_SECONDS", 0.15)
    monkeypatch.setattr(entrypoint, "_PROMPT_WIZARD_WAIT_SECONDS", 5)
    monkeypatch.setattr(entrypoint, "_PROMPT_POLL_SECONDS", 0.02)
    job = _job()

    def _open_then_close():
        time.sleep(0.05)
        with shell_runtime.get().enrollment_wizard_lock:
            shell_runtime.get().enrollment_wizard_active = True
        time.sleep(0.25)
        with shell_runtime.get().enrollment_wizard_lock:
            shell_runtime.get().enrollment_wizard_active = False

    wizard = threading.Thread(target=_open_then_close, daemon=True)
    wizard.start()
    try:
        t0 = time.time()
        job._wait_before_prompting()
        assert time.time() - t0 >= 0.25, "doit attendre la fermeture de l'assistant ouvert pendant la grâce"
        assert shell_runtime.get().enrollment_wizard_active is False
    finally:
        wizard.join(2)


def test_schedule_update_reconciles_previous_target_before_new_directive():
    """Cas du banc : la directive suivante arrive avant le timer de
    réconciliation. La campagne précédente est active (registre à jour après
    redémarrage) : elle doit être rapportée installed et purgée, puis la
    nouvelle directive s'exécuter."""
    job = _job(current_version=CURRENT)
    job._save_update_state({"campaign_id": 6, "target_version": CURRENT}, "installed_inprocess",
                           route="directed")
    done = threading.Event()
    job._perform_update = lambda d: done.set()
    job._schedule_update({"action": "update", "target_version": TARGET, "campaign_id": 7})
    assert done.wait(2)
    installed_calls = [c for c in job._report_update_status.call_args_list if c.args[0] == 6 and c.args[1] == "installed"]
    assert installed_calls, "la campagne précédente doit être rapportée installed avant la nouvelle directive"
    assert not os.path.isfile(job._update_state_path()), "l'état réconcilié doit être purgé"


# installed_native : étape laissée par les versions qui installaient aussi par le
# dialogue de LibreOffice.
@pytest.mark.parametrize("stage,route", [("installed_inprocess", "directed"),
                                         ("installed_native", "native")])
def test_schedule_update_defers_new_directive_while_previous_install_pending_restart(stage, route):
    """Cible X installée, fermeture refusée, l'utilisateur continue de travailler :
    en session le registre reste sur l'ancienne version. Une directive Y ne doit
    ni s'exécuter ni écraser l'état de X — sinon la campagne X ne serait jamais
    rapportée installed."""
    job = _job(current_version=CURRENT)
    job._save_update_state({"campaign_id": 6, "target_version": TARGET}, stage, route=route)
    assert _schedule_and_wait(
        job, {"action": "update", "target_version": "0.0.1.0.33", "campaign_id": 7}) is False
    state = _state(job)
    assert state["target_version"] == TARGET
    assert state["stage"] == stage
    assert state["campaign_id"] == 6


def test_close_helpers_survive_disposed_context():
    """Contexte disposé pendant la boucle de fermeture : la sonde répond Faux
    et la boîte d'information s'abstient, sans exception remontée jusqu'à
    _perform_update (qui rapporterait un échec à tort)."""
    job = _job()
    job.ctx.getServiceManager.side_effect = RuntimeError("disposed")
    assert job._has_modified_documents() is False
    job._notify_update_activates_at_restart()
