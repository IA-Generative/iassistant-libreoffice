"""Récupération de la config DM : ordre des bases, cache et délai."""
import os
import time
import urllib.error
from unittest.mock import MagicMock

from src.mirai import local_config
from src.mirai.shell import runtime as shell_runtime
from tests.stubs.helpers import http_response, make_update_directive
from tests.stubs.uno_stubs import install, make_job, read_user_config, seed_user_config

install()

A = "https://dm-a.example"
B = "https://dm-b.example"
C = "https://dm-c.example"


class FakeDms:
    """Un DM par base : chacun annonce sa liste, ne reconnaît que les paires qu'il
    a émises, et mint un llmToken à une requête authentifiée seulement."""

    def __init__(self):
        self.announce = {A: [A], B: [B], C: [C]}
        self.known = {A: set(), B: set(), C: set()}
        self.down = set()
        self.requests = []   # (base, valeur de X-Relay-Client ou None)

    def __call__(self, request, **_kwargs):
        base = next(b for b in self.announce if request.full_url.startswith(b + "/"))
        client = request.get_header("X-relay-client")
        self.requests.append((base, client))
        if base in self.down:
            raise urllib.error.URLError("down")
        inner = {"enabled": True, "llm_base_urls": base + "/llm/v1"}
        if self.announce[base] is not None:
            inner["bootstrapUrls"] = self.announce[base]
        if client in self.known[base]:
            inner["llmToken"] = "token-from-" + base
            inner["telemetryKey"] = "telemetry-" + base
        else:
            inner["llmToken"] = ""
            inner["_auth_notice"] = "Authentification requise. Effectuez un enrollment."
        return http_response({"config": inner})

    def bases(self):
        return [base for base, _ in self.requests]


def _wired(job, dms):
    """Branche un MainJob sur les faux DM ; la récupération relais et l'assistant
    de démarrage sont neutralisés (ils lancent des threads) sauf quand un test
    les veut."""
    job._urlopen = dms
    job._schedule_relay_recovery = MagicMock(return_value=True)
    job._schedule_enrollment_check = MagicMock()
    return job


def _device(tmp_path, dms):
    """Un poste dont l'OXT embarque A, face aux faux DM."""
    seed_user_config(str(tmp_path), {"enabled": True, "bootstrap_urls": [A]})
    return _wired(make_job(config_dir=str(tmp_path)), dms)


def _restarted(tmp_path, dms):
    """Le même poste dans un nouveau processus : réglages gardés, mémoire perdue."""
    shell_runtime.reset_for_tests()
    return _wired(make_job(config_dir=str(tmp_path)), dms)


def _cfg(job, values):
    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: values.get(k, d))


# ── ordre des bases ──────────────────────────────────────────

def test_the_announced_list_is_tried_before_the_baked_one(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job.set_config("dm_bootstrap_urls", [B, A])
    job._fetch_config(force=True)
    assert dms.bases() == [B]
    assert job._active_bootstrap_url() == B


def test_the_dm_that_answers_hands_over_within_the_fetch_to_the_one_it_names_ahead(tmp_path):
    dms = FakeDms()
    dms.announce[A] = [B, A]
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == B
    assert read_user_config(str(tmp_path))["dm_bootstrap_urls"] == [B]   # la liste de B, la gagnante
    dms.requests.clear()
    job._fetch_config(force=True)
    assert dms.bases() == [B]


def test_a_dm_naming_itself_first_causes_no_extra_request(tmp_path):
    dms = FakeDms()
    dms.announce[A] = [A, B]
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert dms.bases() == [A]
    assert read_user_config(str(tmp_path))["dm_bootstrap_urls"] == [A, B]


def test_a_dm_naming_others_without_itself_hands_over_to_the_first_that_answers(tmp_path):
    dms = FakeDms()
    dms.announce[A] = [C, B]
    dms.down.add(C)
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert dms.bases() == [A, C, B]
    assert job._active_bootstrap_url() == B


def test_when_no_dm_named_ahead_answers_the_former_base_stays(tmp_path):
    dms = FakeDms()
    dms.announce[A] = [B, A]
    dms.down.add(B)
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == A
    assert read_user_config(str(tmp_path))["dm_bootstrap_urls"] == [B, A]


def test_one_hop_only_the_list_the_hop_brings_waits_for_the_next_fetch(tmp_path):
    dms = FakeDms()
    dms.announce[A] = [B, A]
    dms.announce[B] = [A, B]                 # deux DM qui se nomment l'un l'autre en tête
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == B
    dms.requests.clear()
    job._fetch_config(force=True)            # [A, B] enregistrée : A répond et saute vers B
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == B


def test_a_response_without_a_usable_list_keeps_the_stored_one(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job.set_config("dm_bootstrap_urls", [B, A])
    for announced in (None, [], ["", "/"]):
        dms.announce[B] = announced
        job._fetch_config(force=True)        # B répond
        assert read_user_config(str(tmp_path))["dm_bootstrap_urls"] == [B, A]


def test_a_base_that_failed_goes_last_for_the_rest_of_the_process(tmp_path):
    dms = FakeDms()
    dms.announce[A] = dms.announce[B] = [B, A]
    job = _device(tmp_path, dms)
    job.set_config("dm_bootstrap_urls", [B, A])
    dms.down.add(B)
    job._fetch_config(force=True)
    assert dms.bases() == [B, A]
    dms.down.clear()
    dms.requests.clear()
    job._fetch_config(force=True)            # B répond de nouveau, mais n'est plus essayée en premier
    assert dms.bases() == [A]
    dms.requests.clear()
    _restarted(tmp_path, dms)._fetch_config(force=True)
    assert dms.bases() == [B]


def test_a_demoted_base_is_sound_again_as_soon_as_it_answers(tmp_path):
    dms = FakeDms()
    dms.announce[A] = dms.announce[B] = [B, A]
    job = _device(tmp_path, dms)
    job.set_config("dm_bootstrap_urls", [B, A])
    dms.down.add(B)
    job._fetch_config(force=True)            # B en queue
    dms.down.clear()
    dms.down.add(A)
    dms.requests.clear()
    job._fetch_config(force=True)            # A tombe : B, dernier recours, répond
    assert dms.bases() == [A, B]
    dms.down.clear()
    dms.requests.clear()
    job._fetch_config(force=True)
    assert dms.bases() == [B]                # B réhabilitée, A en queue


def test_the_hop_skips_a_base_that_failed_earlier_in_the_process(tmp_path):
    dms = FakeDms()
    dms.announce[A] = [B, A]
    dms.down.add(B)
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert dms.bases() == [A, B]
    dms.requests.clear()
    job._fetch_config(force=True)
    assert dms.bases() == [A]


def test_the_announced_list_survives_an_upgrade_and_not_a_transport_change(tmp_path):
    job = _device(tmp_path, FakeDms())
    job.set_config("dm_bootstrap_urls", [B, A])
    job._apply_install_changes({"version", "package"})
    assert read_user_config(str(tmp_path))["dm_bootstrap_urls"] == [B, A]
    job._apply_install_changes({"transport"})
    assert "dm_bootstrap_urls" not in read_user_config(str(tmp_path))


def test_a_relative_artifact_is_downloaded_in_the_announced_order(tmp_path):
    job = _device(tmp_path, FakeDms())
    job.set_config("dm_bootstrap_urls", [B])
    tried = []

    def down(request, **_kwargs):
        tried.append(request.full_url)
        raise urllib.error.URLError("down")

    job._urlopen = down
    job._perform_update(make_update_directive(urgency="deferred"))
    downloads = [url for url in tried if "/binaries/" in url]
    assert downloads[0].startswith(B + "/")
    assert downloads[1].startswith(A + "/")


# ── persist + hydrate du cache config ────────────────────────

def test_config_cache_persist_and_hydrate_roundtrip(tmp_path):
    d = str(tmp_path)
    job = make_job(config_dir=d)
    data = {"meta": {"schema_version": 2}, "config": {"keycloakRealm": "mirai"}}
    job._persist_config_cache(data)
    assert os.path.isfile(os.path.join(d, "mirai", "dm_snapshot.json"))
    # instance fraîche, cache vide → hydrate le recharge
    job2 = make_job(config_dir=d)
    job2.config_cache = None
    job2._hydrate_config_cache()
    assert job2.config_cache == data


def test_config_cache_hydrate_skips_when_stale(tmp_path):
    d = str(tmp_path)
    job = make_job(config_dir=d)
    local_config.write_json_atomic(
        os.path.join(d, "mirai", "dm_snapshot.json"),
        {"ts": time.time() - (job.config_ttl + 100), "config_data": {"config": {}}})
    job.config_cache = None
    job._hydrate_config_cache()
    assert job.config_cache is None


def test_config_cache_hydrate_noop_when_already_cached(tmp_path):
    d = str(tmp_path)
    job = make_job(config_dir=d)
    job._persist_config_cache({"config": {"a": 1}})
    job.config_cache = {"config": {"already": "loaded"}}
    job._hydrate_config_cache()   # ne doit pas écraser
    assert job.config_cache == {"config": {"already": "loaded"}}


# ── timeout de fetch configurable ────────────────────────────

def test_config_fetch_uses_configurable_timeout():
    job = make_job()
    _cfg(job, {
        "bootstrap_url": "https://dm",
        "config_path": "/c.json",
        "enabled": True,
        "proxy_enabled": False,
        "config_fetch_timeout_seconds": 3,
    })
    job._relay_headers = MagicMock(return_value={})
    job._get_extension_version = MagicMock(return_value="1.0.0")
    job._get_lo_version = MagicMock(return_value="24.8")
    job._ensure_plugin_uuid = MagicMock(return_value="u")
    job._persist_bootstrap_config = MagicMock()
    job._persist_config_cache = MagicMock()
    job.set_config = MagicMock()
    job._urlopen = MagicMock(return_value=http_response({"config": {}}))

    job._fetch_config(force=True)

    _, kwargs = job._urlopen.call_args
    assert kwargs.get("timeout") == 3


def test_config_fetch_timeout_defaults_to_4():
    job = make_job()
    _cfg(job, {
        "bootstrap_url": "https://dm",
        "config_path": "/c.json",
        "enabled": True,
        "proxy_enabled": False,
    })
    job._relay_headers = MagicMock(return_value={})
    job._get_extension_version = MagicMock(return_value="1.0.0")
    job._get_lo_version = MagicMock(return_value="24.8")
    job._ensure_plugin_uuid = MagicMock(return_value="u")
    job._persist_bootstrap_config = MagicMock()
    job._persist_config_cache = MagicMock()
    job.set_config = MagicMock()
    job._urlopen = MagicMock(return_value=http_response({"config": {}}))

    job._fetch_config(force=True)

    _, kwargs = job._urlopen.call_args
    assert kwargs.get("timeout") == 4
