"""Route native pilotée par le DM (spec 2026-09-22, issue #9) : le DM décide
(directive), LibreOffice installe (dialogue « Mise à jour des extensions »,
feed <update-information>), le plugin ferme proprement. Route dirigée de
fix/MAJ en repli ; refus mémorisé 24 h.

Run:  pytest tests/unit/test_native_route.py -v
"""
import json
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint

TARGET = "0.0.1.0.32"
CURRENT = "0.0.1.0.31"


def _job(current_version=CURRENT):
    job = make_job(config_dir=tempfile.mkdtemp())
    job._get_extension_version = MagicMock(return_value=current_version)
    job._report_update_status = MagicMock()
    job._send_telemetry = MagicMock()
    return job


def _state(job):
    with open(job._update_state_path(), encoding="utf-8") as fh:
        return json.load(fh)


# ── État persistant ──────────────────────────────────────────────────────

def test_constants_match_spec():
    assert entrypoint._UPDATE_POSTPONE_SECONDS == 24 * 3600
    assert entrypoint._NATIVE_INSTALL_WAIT_SECONDS == 900
    assert entrypoint._NATIVE_POLL_SECONDS == 5
    assert entrypoint._NATIVE_TRIGGER_TIMEOUT_SECONDS == 30
    assert entrypoint._NATIVE_MAX_ATTEMPTS == 2


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


# ── Cooldown : une cible refusée ou ignorée n'est pas reproposée avant 24 h ──

def _schedule_and_wait(job, directive, seconds=0.5):
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


# ── _native_feed_offers : PackageInformationProvider.isUpdateAvailable ────
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


# ── _trigger_native_update_dialog : PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG") ──
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


# ── _perform_native_update : dialogue natif, surveillance, fermeture ─────

def _native_job(versions, trigger=True):
    """versions : réponses successives de _get_extension_version (la première
    est version_before, puis la surveillance)."""
    job = _job()
    seq = list(versions)
    job._get_extension_version = MagicMock(side_effect=lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    job._wait_before_prompting = MagicMock()
    job._trigger_native_update_dialog = MagicMock(return_value=trigger)
    job._close_after_inprocess_update = MagicMock()
    return job


DIRECTIVE = {"action": "update", "target_version": TARGET, "campaign_id": 7,
             "artifact_url": "/catalog/mirai-libreoffice/download", "urgency": "normal"}


def _events(job):
    return [c.args[0] for c in job._send_telemetry.call_args_list]


def test_native_update_installed_then_closes():
    job = _native_job([CURRENT, CURRENT, TARGET])
    assert job._perform_native_update(DIRECTIVE, wait_seconds=2, poll_seconds=0.01) is True

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


def test_native_update_postponed_when_nothing_installed():
    job = _native_job([CURRENT])
    before = time.time()
    assert job._perform_native_update(DIRECTIVE, wait_seconds=0.05, poll_seconds=0.01) is True

    job._close_after_inprocess_update.assert_not_called()
    state = _state(job)
    assert state["stage"] == "postponed"
    assert state["route"] == "native"
    assert state["postponed_until"] >= before + entrypoint._UPDATE_POSTPONE_SECONDS - 1
    assert _events(job) == ["UpdateNativeDialogShown", "UpdatePostponed"]
    assert job._send_telemetry.call_args_list[1].args[1]["route"] == "native"


def test_native_update_counts_attempts_for_same_target():
    job = _native_job([CURRENT])
    job._save_update_state(DIRECTIVE, "postponed", route="native", native_attempts=1)
    job._perform_native_update(DIRECTIVE, wait_seconds=0.05, poll_seconds=0.01)
    assert _state(job)["native_attempts"] == 2
    attrs = job._send_telemetry.call_args_list[0].args[1]
    assert attrs["attempt"] == "2"


def test_native_update_returns_false_without_side_effects_when_trigger_fails():
    job = _native_job([CURRENT], trigger=False)
    assert job._perform_native_update(DIRECTIVE, wait_seconds=0.05, poll_seconds=0.01) is False
    job._report_update_status.assert_not_called()
    job._send_telemetry.assert_not_called()
    assert not os.path.isfile(job._update_state_path())


# ── Choix de route dans _perform_update ──────────────────────────────────

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
