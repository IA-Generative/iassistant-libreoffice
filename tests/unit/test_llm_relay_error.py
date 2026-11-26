"""Erreurs du relais LLM rapportées en télémétrie (LlmRelayError).

Contrat : device-management, docs/plugin-developer/
plugin-dm-protocol-update-features.md § 8 bis : un événement par erreur du relais
(429, 401, 403, 5xx), corrélé à l'audit serveur par X-Request-Id, au plus un par
code d'erreur et par minute, et jamais de contenu de prompt.
"""
import urllib.request

import pytest

from src.mirai.core import sse_pump
from src.mirai.core.shell_facade import MainJobShell
from tests.stubs.helpers import spans_sent
from tests.stubs.uno_stubs import make_job, seed_user_config

COLLECTOR = "https://collecteur.example/v1/traces"
LLM = "https://ia.example/v1"

_RATE_LIMITED = '{"error": {"code": "rate_limit_exceeded"}, "retry_after": 30}'


@pytest.fixture
def relay(network):
    """Un collecteur qui accepte les spans et un relais qui liste son modèle."""
    network.on("POST", COLLECTOR)
    network.on("GET", LLM + "/models", {"data": [{"id": "modele-a"}]})
    return network


def _device(tmp_path, **settings):
    seed_user_config(str(tmp_path), {
        "telemetryEndpoint": COLLECTOR, "llm_base_urls": LLM,
        "llm_default_models": "modele-a", **settings})


def _new_action(tmp_path):
    """LibreOffice crée un MainJob par action : réouverture de la palette,
    clic sur « Tester le modèle »…"""
    return MainJobShell(make_job(config_dir=str(tmp_path)))


def _shell(tmp_path, **settings):
    _device(tmp_path, **settings)
    return _new_action(tmp_path)


def _relay_errors(network):
    """Les LlmRelayError reçus, sans les attributs que tout span porte."""
    common = ("extension.uuid", "extension.name", "extension.version",
              "plugin.action", "trigger.source")
    return [{key: value for key, value in attributes.items() if key not in common}
            for name, attributes in spans_sent(network, COLLECTOR) if name == "LlmRelayError"]


def _codes(network):
    return [error["llm.error_code"] for error in _relay_errors(network)]


def test_a_relay_error_carries_what_the_server_said_and_no_content(tmp_path, relay):
    shell = _shell(tmp_path)
    answer = shell.report_llm_error(429, _RATE_LIMITED, {"X-Request-Id": "trace-abc"})
    assert answer == ("rate_limit_exceeded", 30)
    assert _relay_errors(relay) == [{
        "llm.status_code": 429, "llm.error_code": "rate_limit_exceeded",
        "llm.endpoint": "chat/completions", "llm.model": "modele-a",
        "llm.will_retry": False, "llm.retry_after_s": 30, "llm.request_id": "trace-abc"}]


def test_the_report_is_filed_under_the_llm_error_action(tmp_path, relay):
    _shell(tmp_path).report_llm_error(429, _RATE_LIMITED)
    [(_, attributes)] = spans_sent(relay, COLLECTOR)
    assert attributes["plugin.action"] == "llm.error"


@pytest.mark.parametrize("status, body, headers, code, retry_after", [
    (429, '{"error": {"type": "overloaded"}}', None, "overloaded", None),
    (502, "pas du json", None, "http_502", None),
    (429, "", {"Retry-After": "12"}, "http_429", 12),
    (429, '{"retry_after": 30}', {"Retry-After": "12"}, "http_429", 30),
    (429, "", {"Retry-After": "bientôt"}, "http_429", None),
])
def test_the_error_code_and_the_delay_are_read_from_the_answer(
        tmp_path, relay, status, body, headers, code, retry_after):
    """Code : `error.code`, sinon `error.type`, sinon le statut HTTP. Délai : celui
    du corps, sinon l'en-tête Retry-After ; illisible, il n'est pas rapporté."""
    _shell(tmp_path).report_llm_error(status, body, headers)
    [error] = _relay_errors(relay)
    assert (error["llm.error_code"], error.get("llm.retry_after_s")) == (code, retry_after)


def test_a_long_request_identifier_is_cut_to_64_characters(tmp_path, relay):
    _shell(tmp_path).report_llm_error(500, "", {"X-Request-Id": "r" * 80})
    [error] = _relay_errors(relay)
    assert error["llm.request_id"] == "r" * 64


@pytest.mark.parametrize("reason, code", [
    ("The read operation timed out", "timeout"), ("Connection refused", "network_error")])
def test_a_network_failure_is_reported_without_status(tmp_path, relay, reason, code):
    _shell(tmp_path).report_llm_network_error(reason)
    [error] = _relay_errors(relay)
    assert (error["llm.status_code"], error["llm.error_code"]) == (0, code)


def test_naming_the_model_asks_the_relay_for_its_models(tmp_path, relay):
    """Le modèle rapporté est le réglage courant, dont la lecture interroge le
    relais."""
    _shell(tmp_path).report_llm_error(429, _RATE_LIMITED)
    spans_sent(relay, COLLECTOR)
    assert len(relay.requests("GET", LLM + "/models")) == 1


def test_dedup_holds_across_mainjob_instances(tmp_path, relay, fast_clock):
    _shell(tmp_path).report_llm_error(429, _RATE_LIMITED)
    fast_clock.sleep(59)
    _new_action(tmp_path).report_llm_error(429, _RATE_LIMITED)
    assert _codes(relay) == ["rate_limit_exceeded"]


def test_emits_again_once_the_window_is_over(tmp_path, relay, fast_clock):
    _shell(tmp_path).report_llm_error(429, _RATE_LIMITED)
    fast_clock.sleep(60)
    _new_action(tmp_path).report_llm_error(429, _RATE_LIMITED)
    assert _codes(relay) == ["rate_limit_exceeded", "rate_limit_exceeded"]


def test_each_error_code_keeps_its_own_window(tmp_path, relay):
    _shell(tmp_path).report_llm_error(429, _RATE_LIMITED)
    _new_action(tmp_path).report_llm_network_error("timed out")
    _new_action(tmp_path).report_llm_error(429, _RATE_LIMITED)
    assert sorted(_codes(relay)) == ["rate_limit_exceeded", "timeout"]


def test_an_error_met_while_telemetry_is_off_still_opens_its_window(tmp_path, relay):
    """L'erreur tue par le réglage occupe quand même le créneau de son code."""
    shell = _shell(tmp_path, telemetryEnabled=False)
    shell.report_llm_error(429, _RATE_LIMITED)
    shell.set_config("telemetryEnabled", True)
    shell.report_llm_error(429, _RATE_LIMITED)
    assert _relay_errors(relay) == []


def test_a_429_from_the_relay_is_correlated_with_the_server_audit(tmp_path, relay):
    """Le 429 du relais, lu par le flux de la palette, émet un LlmRelayError
    conforme au § 8 bis, corrélé à l'audit serveur par X-Request-Id."""
    relay.on("POST", "/chat/completions", _RATE_LIMITED, status=429,
             headers={"X-Request-Id": "trace-429"})
    request = urllib.request.Request(LLM + "/chat/completions", data=b"{}")

    sse_pump.run_stream(_shell(tmp_path), request, lambda event: None)

    [error] = _relay_errors(relay)
    assert (error["llm.status_code"], error["llm.error_code"], error["llm.retry_after_s"],
            error["llm.request_id"], error["llm.will_retry"]) == (
        429, "rate_limit_exceeded", 30, "trace-429", False)
