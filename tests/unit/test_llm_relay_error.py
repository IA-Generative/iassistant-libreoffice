"""Journalisation fonctionnelle des erreurs du relais LLM (LlmRelayError).

Contrat : device-management, docs/plugin-developer/
plugin-dm-protocol-update-features.md § 8 bis — événement télémétrie par erreur
relais (429/401/403/5xx) avec corrélation X-Request-Id, retry_after, anti-tempête
(dédup ~1 min par code), et jamais de contenu de prompt.
No LibreOffice required — UNO modules are stubbed.
"""
import io
import unittest
import urllib.error
from email.message import Message

import pytest

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.core import sse_pump  # install() doit précéder l'import
from src.mirai.core.shell_facade import MainJobShell

_RATE_LIMITED = '{"error": {"code": "rate_limit_exceeded"}, "retry_after": 30}'


def _http_error(code, body=b"", headers=None):
    msg = Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return urllib.error.HTTPError(
        "https://dm.test/llm/v1/chat/completions", code, "err", msg, io.BytesIO(body)
    )


class TestParseLlmError(unittest.TestCase):
    def setUp(self):
        self.job = make_job()

    def test_parses_openai_body_with_retry_after(self):
        body = ('{"error": {"message": "Rate limit exceeded", '
                '"type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}, '
                '"retry_after": 30}')
        code, retry_after = self.job._parse_llm_error(429, body)
        self.assertEqual(code, "rate_limit_exceeded")
        self.assertEqual(retry_after, 30)

    def test_falls_back_to_retry_after_header(self):
        headers = Message()
        headers["Retry-After"] = "12"
        code, retry_after = self.job._parse_llm_error(429, "", headers)
        self.assertEqual(code, "http_429")
        self.assertEqual(retry_after, "12")

    def test_falls_back_to_http_status_code(self):
        code, retry_after = self.job._parse_llm_error(502, "pas du json")
        self.assertEqual(code, "http_502")
        self.assertIsNone(retry_after)


class TestSendLlmRelayError(unittest.TestCase):
    def setUp(self):
        self.job = make_job()
        self.sent = []
        self.job._send_telemetry = lambda name, attrs=None: self.sent.append((name, attrs))

    def test_emits_spec_attributes_without_content(self):
        self.job._send_llm_relay_error(
            429, "rate_limit_exceeded", retry_after=30,
            request_id="trace-abc", will_retry=False)
        self.assertEqual(len(self.sent), 1)
        name, attrs = self.sent[0]
        self.assertEqual(name, "LlmRelayError")
        self.assertEqual(attrs["llm.status_code"], 429)
        self.assertEqual(attrs["llm.error_code"], "rate_limit_exceeded")
        self.assertEqual(attrs["llm.retry_after_s"], 30)
        self.assertEqual(attrs["llm.request_id"], "trace-abc")
        self.assertEqual(attrs["llm.endpoint"], "chat/completions")
        self.assertFalse(attrs["llm.will_retry"])
        # Jamais de contenu : uniquement des attributs llm.* / plugin.* connus.
        for key in attrs:
            self.assertTrue(key.startswith(("llm.", "plugin.", "trigger.")), key)

    def test_dedup_same_error_code_within_window(self):
        self.job._send_llm_relay_error(429, "rate_limit_exceeded")
        self.job._send_llm_relay_error(429, "rate_limit_exceeded")
        self.assertEqual(len(self.sent), 1)  # anti-tempête

    def test_different_error_codes_both_emitted(self):
        self.job._send_llm_relay_error(429, "rate_limit_exceeded")
        self.job._send_llm_relay_error(401, "invalid_api_key")
        self.assertEqual(len(self.sent), 2)


@pytest.fixture
def relay_error_codes(monkeypatch):
    """Codes des LlmRelayError émis, toutes instances de MainJob confondues."""
    from src.mirai import entrypoint
    codes = []

    def capture(job, span_name, attributes=None):
        if span_name == "LlmRelayError":
            codes.append(attributes["llm.error_code"])

    monkeypatch.setattr(entrypoint, "send_telemetry_trace_async", capture)
    return codes


def _new_action():
    """LibreOffice crée un MainJob par action : réouverture de la palette,
    clic sur « Tester le modèle »…"""
    return MainJobShell(make_job())


def test_dedup_holds_across_mainjob_instances(relay_error_codes, fast_clock):
    _new_action().report_llm_error(429, _RATE_LIMITED)
    fast_clock.sleep(59)
    _new_action().report_llm_error(429, _RATE_LIMITED)
    assert relay_error_codes == ["rate_limit_exceeded"]


def test_emits_again_once_the_window_is_over(relay_error_codes, fast_clock):
    _new_action().report_llm_error(429, _RATE_LIMITED)
    fast_clock.sleep(60)
    _new_action().report_llm_error(429, _RATE_LIMITED)
    assert relay_error_codes == ["rate_limit_exceeded", "rate_limit_exceeded"]


def test_each_error_code_keeps_its_own_window(relay_error_codes):
    _new_action().report_llm_error(429, _RATE_LIMITED)
    _new_action().report_llm_network_error("timed out")
    _new_action().report_llm_error(429, _RATE_LIMITED)
    assert relay_error_codes == ["rate_limit_exceeded", "timeout"]


def test_a_429_from_the_relay_is_correlated_with_the_server_audit():
    """Le 429 du relais, lu par le flux de la palette, émet un LlmRelayError
    conforme au § 8 bis, corrélé à l'audit serveur par X-Request-Id."""
    job = make_job()
    sent = []
    job._send_telemetry = lambda name, attrs=None: sent.append((name, attrs))
    shell = MainJobShell(job)

    def refuse(request, timeout=None):
        raise _http_error(429, _RATE_LIMITED.encode("utf-8"), {"X-Request-Id": "trace-429"})

    shell.urlopen = refuse

    sse_pump.run_stream(shell, object(), lambda event: None)

    assert [(name, attrs["llm.status_code"], attrs["llm.error_code"],
             attrs["llm.retry_after_s"], attrs["llm.request_id"], attrs["llm.will_retry"])
            for name, attrs in sent] == [
        ("LlmRelayError", 429, "rate_limit_exceeded", 30, "trace-429", False)]


if __name__ == "__main__":
    unittest.main()
