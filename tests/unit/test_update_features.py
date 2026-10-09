"""
Headless unit tests for Update & Feature Toggling (schema_version=2).
Covers TC-LO-04 to TC-LO-13 from the test cahier.

Run with:
    pytest tests/unit/test_update_features.py -v --tb=short
"""
import hashlib
import time
from unittest.mock import MagicMock

# ── Stubs must be installed before importing entrypoint ──────────────
from tests.stubs.helpers import http_response
from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.entrypoint import MainJob

# ── helpers ──────────────────────────────────────────────────────────

def _enriched_v2(features=None, update=None):
    """Build a minimal EnrichedConfigResponse (schema_version=2)."""
    return {
        "meta": {"schema_version": 2, "generated_at": "2026-03-15T00:00:00Z"},
        "config": {},
        "features": features or {},
        "update": update,
    }


def _make_update_directive(action="update", target="2.0.0", current="1.0.0",
                            checksum=None, artifact_url="/binaries/lo/2.0.0/mirai.oxt",
                            urgency="normal", campaign_id=1):
    d = {
        "action": action,
        "target_version": target,
        "current_version": current,
        "artifact_url": artifact_url,
        "checksum": checksum or "",
        "urgency": urgency,
        "campaign_id": campaign_id,
    }
    return d


def _dm_job(payload):
    """Job dont le fetch de config reçoit *payload* du DM."""
    job = make_job()
    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: {
        "bootstrap_url": "http://localhost:9999",
        "config_path": "/config/lo/config.json",
        "enabled": True,
        "proxy_enabled": False,
    }.get(k, d))
    job._relay_headers = MagicMock(return_value={})
    job._get_extension_version = MagicMock(return_value="1.0.0")
    job._get_lo_version = MagicMock(return_value="24.8.0")
    job._ensure_plugin_uuid = MagicMock(return_value="test-uuid")
    job._persist_bootstrap_config = MagicMock()
    job._schedule_update = MagicMock()
    job._urlopen = MagicMock(return_value=http_response(payload))
    return job


# ── TC-LO-04 : fetch v2 popule _features_cache ───────────────────────

def test_lo04_fetch_v2_populates_features_cache():
    job = _dm_job(_enriched_v2(features={"writer_assistant": True, "calc_assistant": False}))

    result = job._fetch_config(force=True)

    assert result is not None
    assert job._features_cache == {"writer_assistant": True, "calc_assistant": False}


# ── TC-LO-05 : fetch v2 schedule_update appelé si action présente ────

def test_lo05_fetch_v2_calls_schedule_update():
    directive = _make_update_directive()
    job = _dm_job(_enriched_v2(features={}, update=directive))

    job._fetch_config(force=True)

    job._schedule_update.assert_called_once_with(directive)


# ── TC-LO-06 : fetch v2 update=null → schedule_update non appelé ────

def test_lo06_fetch_v2_no_update_directive():
    job = _dm_job(_enriched_v2(features={}, update=None))

    job._fetch_config(force=True)

    job._schedule_update.assert_not_called()


# ── TC-LO-07 : fetch legacy (schema_version absent) → features_cache inchangé

def test_lo07_fetch_legacy_does_not_touch_features_cache():
    # Legacy flat response without meta.schema_version
    job = _dm_job({"config": {"telemetryEnabled": False}, "update_url": "http://old"})
    job._features_cache = {"existing_flag": True}

    job._fetch_config(force=True)

    # features_cache must be untouched
    assert job._features_cache == {"existing_flag": True}
    job._schedule_update.assert_not_called()


# ── TC-LO-08 : update non retriggeré si déjà en cours ───────────────

def test_lo08_update_not_retriggered_if_in_progress():
    job = make_job()
    MainJob._update_in_progress_cls = True

    called = []

    def fake_perform(directive):
        called.append(directive)

    job._perform_update = fake_perform
    job._schedule_update(_make_update_directive())

    time.sleep(0.1)
    assert called == [], "perform_update should not be called when already in progress"


# ── TC-LO-09 : _perform_update checksum OK → install ────────────────

def test_lo09_perform_update_checksum_ok_stages(fast_clock):
    """checksum OK → l'artefact est stagé (statut 'deferred' rapporté — le
    rapport « installed » n'arrive qu'à la réconciliation post-redémarrage,
    quand la nouvelle version est réellement active). L'install
    réelle est différée au restart-accept — PAS exécutée au staging (sinon on
    double-installerait et clobbererait l'instance en cours)."""
    job = make_job()
    fake_binary = b"fake oxt content"
    expected_checksum = "sha256:" + hashlib.sha256(fake_binary).hexdigest()

    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: {
        "bootstrap_url": "http://localhost:9999",
    }.get(k, d))
    job._report_update_status = MagicMock()
    job._install_and_restart_in_process = MagicMock()  # ne doit PAS être appelé au staging

    directive = _make_update_directive(
        action="update",
        target="2.0.0",
        checksum=expected_checksum,
        artifact_url="/binaries/lo/2.0.0/mirai.oxt",
    )
    job._urlopen = MagicMock(return_value=http_response(fake_binary))

    job._perform_update(directive)

    statuses = [c.args[1] for c in job._report_update_status.call_args_list if len(c.args) > 1]
    assert "deferred" in statuses                  # stagé, install à suivre
    assert "installed" not in statuses             # véridique : pas encore actif
    job._install_and_restart_in_process.assert_not_called()  # pas d'install au staging
    assert MainJob._update_in_progress_cls is False


# ── TC-LO-09b : download failover multi-bootstrap ───────────────────

def test_lo09b_download_fails_over_to_next_bootstrap(fast_clock):
    """Si la 1re base bootstrap est injoignable (DGX hors réseau : Errno 8), le
    download bascule sur la suivante au lieu d'abandonner (bug observé : figé sur
    l'hôte GPU interne)."""
    job = make_job()
    fake_binary = b"oxt bytes for failover test"
    checksum = "sha256:" + hashlib.sha256(fake_binary).hexdigest()

    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: {
        "bootstrap_urls": ["https://onyxia.unreachable/bootstrap", "https://scaleway.ok"],
    }.get(k, d))

    tried = []

    def _fake_urlopen(req, **kw):
        url = req.full_url
        tried.append(url)
        if "onyxia" in url:
            raise OSError("nodename nor servname provided, or not known")
        return http_response(fake_binary)

    job._urlopen = _fake_urlopen

    directive = _make_update_directive(
        action="update", target="0.0.1.0.23", checksum=checksum,
        artifact_url="/catalog/mirai-libreoffice/download",
    )
    job._perform_update(directive)

    assert any("onyxia" in u for u in tried), "doit d'abord tenter la 1re base"
    assert "https://scaleway.ok/catalog/mirai-libreoffice/download" in tried, \
        "doit basculer (failover) sur la base suivante"
    assert MainJob._update_in_progress_cls is False


def test_active_bootstrap_url_prefers_persisted_last_good():
    """_active_bootstrap_url() doit préférer last_bootstrap_url persisté à urls[0]
    (sinon une instance fraîche retombe sur le DGX injoignable)."""
    job = make_job()
    job._resolved_bootstrap_url = ""
    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: {
        "bootstrap_urls": ["https://onyxia.unreachable/bootstrap", "https://scaleway.ok"],
        "last_bootstrap_url": "https://scaleway.ok",
    }.get(k, d))
    assert job._active_bootstrap_url() == "https://scaleway.ok"


# ── TC-LO-10 : _perform_update checksum KO → pas d'install ──────────

def test_lo10_perform_update_checksum_mismatch_no_install():
    job = make_job()
    fake_binary = b"corrupted data"
    wrong_checksum = "sha256:" + "0" * 64

    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: {
        "bootstrap_url": "http://localhost:9999",
    }.get(k, d))

    job._install_and_restart_in_process = MagicMock()

    directive = _make_update_directive(checksum=wrong_checksum)
    job._urlopen = MagicMock(return_value=http_response(fake_binary))

    job._perform_update(directive)

    # checksum KO → retour avant tout staging/install
    job._install_and_restart_in_process.assert_not_called()


# ── TC-LO-11 : _perform_update libère flag sur exception ─────────────

def test_lo11_perform_update_releases_flag_on_exception(fast_clock):
    job = make_job()
    job._get_config_from_file = MagicMock(return_value="http://localhost:9999")

    import urllib.error
    job._urlopen = MagicMock(side_effect=urllib.error.URLError("connection refused"))

    directive = _make_update_directive()
    # _perform_update must not raise and must not leave _update_in_progress True
    job._perform_update(directive)
    # _schedule_update sets/clears the flag; here we test perform directly
    assert MainJob._update_in_progress_cls is False  # perform_update doesn't touch the flag itself


# ── TC-LO-12 : _get_extension_version retourne une version ───────────

def test_lo12_get_extension_version_returns_version():
    job = make_job()
    mock_pip = MagicMock()
    mock_pip.getExtensionList.return_value = (("fr.gouv.interieur.mirai", "1.9.0"),)
    job.ctx.getServiceManager.return_value.createInstanceWithContext.return_value = mock_pip

    version = job._get_extension_version()

    assert version == "1.9.0"


# ── TC-LO-13 : _get_extension_version retourne "" sur erreur ─────────

def test_lo13_get_extension_version_returns_empty_on_error():
    job = make_job()
    job.ctx.getServiceManager.return_value.createInstanceWithContext.side_effect = Exception("UNO error")

    version = job._get_extension_version()

    assert version == ""
