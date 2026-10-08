"""Pump SSE : ordre des événements, [DONE], erreurs HTTP/réseau → coquille."""

import io
import threading
import urllib.error

from src.mirai.core import sse_pump
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks


def _http_error(status, body=b'{"error":{"code":"quota"}}'):
    return urllib.error.HTTPError(
        "http://fake", status, "err", {"X-Request-Id": "req-1"}, io.BytesIO(body))


def test_chunks_delivered_in_order():
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("a", "b", "c"))])
    seen = []
    outcome = sse_pump.run_stream(shell, object(), lambda e: seen.append(e.chunk))
    assert outcome.ok
    contents = [c["choices"][0]["delta"].get("content") for c in seen]
    assert contents[:3] == ["a", "b", "c"]


def test_done_terminates_stream():
    lines = [b"data: " + b'{"choices":[{"delta":{"content":"x"}}]}',
             b"data: [DONE]",
             b"data: " + b'{"choices":[{"delta":{"content":"jamais"}}]}']
    shell = FakeShell(responses=[FakeSSEResponse(None, raw_lines=lines)])
    seen = []
    sse_pump.run_stream(shell, object(), lambda e: seen.append(e.chunk))
    assert len(seen) == 1


def test_invalid_json_lines_skipped():
    lines = [b"data: {pas du json", b'data: {"choices":[{"delta":{"content":"ok"}}]}',
             b"data: [DONE]"]
    shell = FakeShell(responses=[FakeSSEResponse(None, raw_lines=lines)])
    seen = []
    outcome = sse_pump.run_stream(shell, object(), lambda e: seen.append(e))
    assert outcome.ok and len(seen) == 1


def test_http_error_reported_to_shell():
    shell = FakeShell(responses=[_http_error(429)])
    outcome = sse_pump.run_stream(shell, object(), lambda e: None)
    assert not outcome.ok
    assert isinstance(outcome.error, sse_pump.StreamHttpError)
    assert outcome.error.status == 429
    assert shell.llm_errors and shell.llm_errors[0][0] == 429


def test_network_error_reported_to_shell():
    shell = FakeShell(responses=[OSError("connexion timed out")])
    outcome = sse_pump.run_stream(shell, object(), lambda e: None)
    assert not outcome.ok
    assert isinstance(outcome.error, sse_pump.StreamNetworkError)
    assert shell.llm_errors and shell.llm_errors[0][0] == 0


def test_request_factory_is_called_lazily():
    """La requête peut être construite par un callable — exécuté dans le
    thread réseau pour ne jamais bloquer le thread principal (gel de LO)."""
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("ok"))])
    calls = []

    def _factory():
        calls.append("built")
        return object()

    outcome = sse_pump.run_stream(shell, _factory, lambda e: None)
    assert outcome.ok
    assert calls == ["built"]


def test_request_factory_failure_becomes_network_error():
    shell = FakeShell()

    def _factory():
        raise RuntimeError("résolution du modèle impossible")

    outcome = sse_pump.run_stream(shell, _factory, lambda e: None)
    assert not outcome.ok
    assert isinstance(outcome.error, sse_pump.StreamNetworkError)
    assert shell.llm_errors and shell.llm_errors[0][0] == 0


def test_llm_client_builds_request_inside_the_stream():
    """La requête est construite DANS run_stream, pas avant.

    Bâtir la requête déclenche côté coquille une lecture de configuration, une
    résolution de modèle et, après un 401, une reprise d'authentification — donc
    du réseau. Tant que la construction reste à l'intérieur du pump, elle vit
    dans le thread qui exécute le run (le worker), et jamais sur le thread
    principal.
    """
    from src.mirai.core.llm_client import LLMClient

    order = []

    class _RecordingShell(FakeShell):
        def build_chat_request(self, messages, max_tokens=2000, extra_body=None):
            order.append("build")
            return super().build_chat_request(messages, max_tokens, extra_body)

        def urlopen(self, request, timeout=None):
            order.append("urlopen")
            return super().urlopen(request, timeout)

    shell = _RecordingShell(responses=[FakeSSEResponse(text_chunks("ok"))])
    LLMClient(shell).step([{"role": "user", "content": "x"}])

    assert order[:2] == ["build", "urlopen"], (
        "la construction doit être paresseuse et immédiatement suivie de "
        f"l'ouverture du flux, dans le même thread ; observé : {order}")


def test_run_stream_never_pumps_uno_events():
    """Le pump ne touche jamais au toolkit UNO : ce n'est pas son rôle."""
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("a", "b"))])

    def _explode():
        raise AssertionError("run_stream ne demande jamais le toolkit UNO")

    shell.toolkit = _explode
    outcome = sse_pump.run_stream(shell, object(), lambda e: None)
    assert outcome.ok


def test_cancel_event_stops_the_stream():
    """Le bouton « Arrêter » : la lecture cesse entre deux chunks."""
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("a", "b", "c", "d"))])
    cancel = threading.Event()
    seen = []

    def _handler(event):
        seen.append(event)
        cancel.set()          # on annule dès le premier chunk reçu

    outcome = sse_pump.run_stream(shell, object(), _handler, cancel_event=cancel)
    assert outcome.cancelled is True
    assert not outcome.ok
    assert len(seen) == 1, "aucun chunk ne doit être traité après l'annulation"


def test_cancel_before_start_does_no_network():
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("a"))])
    cancel = threading.Event()
    cancel.set()

    outcome = sse_pump.run_stream(shell, object(), lambda e: None, cancel_event=cancel)
    assert outcome.cancelled is True
    assert shell.urlopen_calls == 0


def test_iter_sse_chunks_is_a_pure_generator():
    """Testable sans réseau ni thread — c'est tout l'intérêt de l'avoir extrait."""
    lines = [b'data: {"n":1}', b"", b"ligne hors data", b'data: {"n":2}',
             b"data: [DONE]", b'data: {"n":3}']
    assert [c["n"] for c in sse_pump.iter_sse_chunks(iter(lines))] == [1, 2]


def test_on_event_exception_does_not_break_stream():
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("a", "b"))])
    calls = []

    def _handler(event):
        calls.append(event)
        raise RuntimeError("boom")

    outcome = sse_pump.run_stream(shell, object(), _handler)
    assert outcome.ok and len(calls) >= 2
