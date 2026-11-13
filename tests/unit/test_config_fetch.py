"""Récupération de la config DM : ordre des bases, cache et délai."""
import os
import threading
import time
import urllib.error
from unittest.mock import MagicMock, call

from src.mirai import credentials, local_config
from src.mirai.shell import runtime as shell_runtime
from tests.stubs.helpers import http_response, make_update_directive
from tests.stubs.uno_stubs import install, make_job, make_jwt, read_user_config, seed_user_config

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


# ── paire relais liée à son émetteur ─────────────────────────

def _enrolled_with(job, dms, base):
    """Ce que l'enrôlement laisse quand le DM `base` a enrôlé le poste."""
    job.set_config("relay_client_id", "relay-" + base)
    job.set_config("relay_client_key", "key-" + base)
    job.set_config("relay_dm_base", base)
    job.set_config("enrolled", True)
    dms.known[base].add("relay-" + base)


def test_the_pair_goes_to_its_issuer_and_the_token_comes_back_from_it(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    _enrolled_with(job, dms, A)
    dms.requests.clear()
    job._fetch_config(force=True)
    assert dms.requests == [(A, "relay-" + A)]
    assert credentials.recall(credentials.DM_LLM_TOKEN) == "token-from-" + A


def test_a_pair_issued_by_another_dm_is_neither_sent_nor_valid_for_the_active_one(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    _enrolled_with(job, dms, B)              # paire de B ; seul A est candidat
    job._fetch_config(force=True)
    assert dms.requests == [(A, None)]
    assert job._relay_credentials_valid() is False
    assert job._relay_credentials_valid(base=B) is True


def test_a_pair_without_issuer_is_sent_to_any_dm_and_gets_its_issuer_from_the_first_token(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job.set_config("relay_client_id", "relay-legacy")
    job.set_config("relay_client_key", "key-legacy")
    dms.known[A].add("relay-legacy")
    job._fetch_config(force=True)
    assert dms.requests == [(A, "relay-legacy")]
    assert job._get_config_from_file("relay_dm_base", "") == A


def test_no_issuer_is_recorded_when_the_dm_mints_no_token(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job.set_config("relay_client_id", "relay-legacy")
    job.set_config("relay_client_key", "key-legacy")
    job._fetch_config(force=True)            # A ne connaît pas cette paire
    assert job._get_config_from_file("relay_dm_base", "") == ""


def _enrolling(job):
    """Un MainJob prêt à enrôler auprès de B : session et e-mail obtenus, DM actif B,
    /enroll qui rend une paire."""
    job._device_management_enabled = MagicMock(return_value=True)
    job._fetch_config = MagicMock(return_value={"config": {}})
    job._ensure_access_token = MagicMock(return_value="tok")
    job._token_email = MagicMock(return_value="a@b.c")
    job._keycloak_config = MagicMock(return_value={})
    job._keycloak_endpoint = MagicMock(return_value="")
    job._ensure_extension_uuid = MagicMock(return_value="uuid")
    job._active_bootstrap_url = MagicMock(return_value=B)
    job._urlopen = MagicMock(return_value=http_response(
        {"relayClientId": "id-" + B, "relayClientKey": "key-" + B}))
    return job


def _enroll_posts(job):
    return [c for c in job._urlopen.call_args_list if c.args[0].full_url.endswith("/enroll")]


def test_enrolling_records_the_active_dm_as_the_issuer_of_the_pair(tmp_path):
    job = _enrolling(make_job(config_dir=str(tmp_path)))
    job._ensure_device_management_state()
    assert len(_enroll_posts(job)) == 1
    assert job._get_config_from_file("relay_dm_base", "") == B
    assert job._get_config_from_file("enrolled", False) is True


# ── bascule de DM ────────────────────────────────────────────

SWITCH_RECOVERY = call(min_interval_seconds=0, prompt_if_unenrolled=True)


def _handed_over(tmp_path):
    """Poste enrôlé sur A, llmToken en main ; A annonce désormais [B, A], pas
    encore récupéré."""
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    _enrolled_with(job, dms, A)
    job._fetch_config(force=True)
    assert credentials.recall(credentials.DM_LLM_TOKEN) == "token-from-" + A
    dms.announce[A] = [B, A]
    dms.requests.clear()
    job._schedule_relay_recovery.reset_mock()
    return job, dms


def _recovered(job):
    """Attend la fin du worker de récupération relais."""
    deadline = time.time() + 5
    while job._relay_recovery_in_progress and time.time() < deadline:
        time.sleep(0.01)


def test_the_hand_over_takes_effect_within_the_fetch_that_announces_it(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == B
    assert make_job(config_dir=str(tmp_path))._active_bootstrap_url() == B


def test_a_switch_drops_what_the_former_dm_issued_and_schedules_one_re_enrollment(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)
    for key in ("relay_client_id", "relay_client_key", "relay_key_expires_at", "relay_dm_base"):
        assert job._get_config_from_file(key, "") == ""
    assert credentials.recall(credentials.DM_LLM_TOKEN) == ""
    assert credentials.recall(credentials.DM_TELEMETRY_KEY) == ""
    assert job._get_config_from_file("enrolled", True) is False
    assert job._schedule_relay_recovery.call_args_list == [SWITCH_RECOVERY]


def test_the_first_request_to_the_new_dm_carries_no_pair_of_the_former_one(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)
    assert dms.requests == [(A, "relay-" + A), (B, None)]


def test_the_new_dm_answering_again_is_not_a_switch(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)
    _enrolled_with(job, dms, B)
    job._schedule_relay_recovery.reset_mock()
    dms.requests.clear()
    job._fetch_config(force=True)
    assert dms.bases() == [B]
    assert job._get_config_from_file("relay_client_id", "") == "relay-" + B
    job._schedule_relay_recovery.assert_not_called()


def test_landing_on_the_current_base_through_a_hop_is_not_a_switch(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)                            # bascule vers B
    _enrolled_with(job, dms, B)
    job.set_config("dm_bootstrap_urls", [A, B])              # comme si B avait annoncé [A, B]
    job._schedule_relay_recovery.reset_mock()
    dms.requests.clear()
    job._fetch_config(force=True)                            # A répond [B, A] et saute vers B
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == B
    assert job._get_config_from_file("relay_client_id", "") == "relay-" + B
    job._schedule_relay_recovery.assert_not_called()


def test_the_first_dm_ever_to_answer_is_not_a_switch(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    job._fetch_config(force=True)
    assert job._active_bootstrap_url() == A
    assert "enrolled" not in read_user_config(str(tmp_path))
    job._schedule_relay_recovery.assert_called_once_with()   # l'_auth_notice, avec son backoff


def test_when_no_dm_named_ahead_answers_nothing_is_dropped(tmp_path):
    job, dms = _handed_over(tmp_path)
    dms.down.add(B)
    job._fetch_config(force=True)
    assert dms.bases() == [A, B]
    assert job._active_bootstrap_url() == A
    assert job._get_config_from_file("relay_client_id", "") == "relay-" + A
    assert credentials.recall(credentials.DM_LLM_TOKEN) == "token-from-" + A
    assert job._get_config_from_file("enrolled", False) is True
    job._schedule_relay_recovery.assert_not_called()


def test_a_fallback_is_a_switch_and_the_primary_returns_at_the_next_start(tmp_path):
    job, dms = _handed_over(tmp_path)
    dms.announce[B] = [B, A]
    job._fetch_config(force=True)                            # bascule vers B
    _enrolled_with(job, dms, B)
    job._schedule_relay_recovery.reset_mock()
    dms.down.add(B)
    dms.requests.clear()
    job._fetch_config(force=True)                            # repli : bascule vers A
    assert dms.bases() == [B, A]
    assert job._active_bootstrap_url() == A
    assert job._get_config_from_file("relay_client_id", "") == ""
    assert job._schedule_relay_recovery.call_args_list == [SWITCH_RECOVERY]
    _enrolled_with(job, dms, A)
    dms.down.clear()
    dms.requests.clear()
    job._schedule_relay_recovery.reset_mock()
    job._fetch_config(force=True)                            # B revenue reste en queue
    assert dms.bases() == [A]
    job._schedule_relay_recovery.assert_not_called()
    restarted = _restarted(tmp_path, dms)
    dms.requests.clear()
    restarted._fetch_config(force=True)                      # nouveau processus : retour sur B
    assert dms.bases() == [B]
    assert restarted._active_bootstrap_url() == B
    assert restarted._schedule_relay_recovery.call_args_list == [SWITCH_RECOVERY]


def test_the_switch_re_enrollment_starts_once_the_fetch_has_released_its_guard(tmp_path):
    job, dms = _handed_over(tmp_path)
    del job._schedule_relay_recovery                         # la vraie planification et son thread
    seen = []
    job._ensure_device_management_state = MagicMock(
        side_effect=lambda **_kwargs: seen.append(job._fetching_config))
    job._fetch_config(force=True)
    _recovered(job)
    assert seen == [False]


def test_a_switch_during_a_running_recovery_starts_no_second_one(tmp_path):
    job, dms = _handed_over(tmp_path)
    del job._schedule_relay_recovery
    job._relay_recovery_in_progress = True
    job._fetch_config(force=True)
    assert job._active_bootstrap_url() == B
    assert job._get_config_from_file("relay_client_id", "") == ""


def test_an_enrollment_that_waited_for_the_lock_does_nothing_once_another_succeeded(tmp_path):
    release, second_fetched = threading.Event(), threading.Event()
    first = _enrolling(make_job(config_dir=str(tmp_path)))
    pair = first._urlopen.return_value

    def slow_enroll(_request, **_kwargs):
        release.wait(5)
        return pair

    first._urlopen = MagicMock(side_effect=slow_enroll)
    second = _enrolling(make_job(config_dir=str(tmp_path)))
    second._fetch_config = MagicMock(
        side_effect=lambda *_a, **_k: (second_fetched.set(), {"config": {}})[1])
    t1 = threading.Thread(target=first._ensure_device_management_state,
                          kwargs={"force_enroll": True})
    t2 = threading.Thread(target=second._ensure_device_management_state,
                          kwargs={"force_enroll": True})
    t1.start()
    deadline = time.time() + 5
    while not shell_runtime.get().enrollment_lock.locked() and time.time() < deadline:
        time.sleep(0.01)
    t2.start()
    assert second_fetched.wait(5)
    release.set()
    t1.join(5)
    t2.join(5)
    assert len(_enroll_posts(first)) == 1
    assert _enroll_posts(second) == []
    assert shell_runtime.get().enrollment_generation == 1
    assert second._get_config_from_file("relay_client_id", "") == "id-" + B


def test_the_wizard_gate_stays_closed_while_an_enrollment_is_running(tmp_path):
    seed_user_config(str(tmp_path), {"enabled": True})
    job = make_job(config_dir=str(tmp_path))
    lock = shell_runtime.get().enrollment_lock
    lock.acquire()
    try:
        assert job._needs_first_enrollment() is False
    finally:
        lock.release()
    assert job._needs_first_enrollment() is True


def test_a_switch_without_session_leaves_the_device_unenrolled_for_the_wizard(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)
    _enrolling(job)
    job._ensure_access_token = MagicMock(return_value=None)   # pas de session Keycloak
    job._ensure_device_management_state()
    assert _enroll_posts(job) == []
    assert job._get_config_from_file("enrolled", True) is False
    assert job._needs_first_enrollment() is True


def test_a_switch_without_session_opens_the_wizard_right_away(tmp_path):
    job, dms = _handed_over(tmp_path)
    del job._schedule_relay_recovery
    job._ensure_device_management_state = MagicMock()        # sans session, la tentative n'enrôle pas
    job._fetch_config(force=True)
    _recovered(job)
    job._schedule_enrollment_check.assert_called_once_with()


def test_no_wizard_when_the_switch_re_enrollment_succeeds(tmp_path):
    job, dms = _handed_over(tmp_path)
    del job._schedule_relay_recovery
    job._ensure_device_management_state = MagicMock(
        side_effect=lambda **_kwargs: _enrolled_with(job, dms, B))
    job._fetch_config(force=True)
    _recovered(job)
    job._schedule_enrollment_check.assert_not_called()


def test_no_wizard_while_the_session_is_alive_even_if_the_new_dm_refuses_the_enrollment(tmp_path):
    job, dms = _handed_over(tmp_path)
    del job._schedule_relay_recovery
    job.set_config("access_token", make_jwt({"exp": int(time.time()) + 600, "email": "a@b.c"}))
    job._ensure_device_management_state = MagicMock()        # /enroll en erreur : rien d'enregistré
    job._fetch_config(force=True)
    _recovered(job)
    job._schedule_enrollment_check.assert_not_called()


def test_the_relay_recovery_outside_a_switch_never_opens_the_wizard(tmp_path):
    dms = FakeDms()
    job = _device(tmp_path, dms)
    del job._schedule_relay_recovery
    job._ensure_device_management_state = MagicMock()
    job._fetch_config(force=True)            # A répond sans paire : _auth_notice, pas de bascule
    _recovered(job)
    job._schedule_enrollment_check.assert_not_called()


def test_a_successful_switch_re_enrollment_binds_the_pair_to_the_new_dm(tmp_path):
    job, dms = _handed_over(tmp_path)
    job._fetch_config(force=True)
    _enrolling(job)
    job._ensure_device_management_state()
    assert len(_enroll_posts(job)) == 1
    assert job._get_config_from_file("relay_dm_base", "") == B
    assert job._get_config_from_file("enrolled", False) is True


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
