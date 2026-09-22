"""Route native pilotée par le DM (spec 2026-09-22, issue #9) : le DM décide
(directive), LibreOffice installe (dialogue « Mise à jour des extensions »,
feed <update-information>), le plugin ferme proprement. Route dirigée de
fix/MAJ en repli ; refus mémorisé 24 h.

Run:  pytest tests/unit/test_native_route.py -v
"""
import json
import os
import tempfile
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
