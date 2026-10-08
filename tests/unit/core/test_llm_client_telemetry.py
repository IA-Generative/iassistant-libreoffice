"""Télémétrie des bascules et reprises du client LLM.

Trois événements structurants restaient invisibles :
- la bascule DÉFINITIVE natif → JSON (le poste change de protocole pour
  toujours — `llm_tool_mode_detected`) ;
- la reprise d'authentification RÉUSSIE après un 401 (seul l'échec se voyait,
  via LlmRelayError côté coquille) ;
- la reprise réussie après un budget mangé par le raisonnement (seul l'échec
  était télémétré, par la palette : `llm.reasoning_starved`).

Sans eux, un parc où la moitié des postes a silencieusement basculé en JSON —
ou récupère son jeton dix fois par jour — est indistinguable d'un parc sain.
"""

import io
import urllib.error

from src.mirai.core import telemetry_steps
from src.mirai.core.llm_client import LLMClient
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks

TOOLS = [{"type": "function",
          "function": {"name": "writer_get_selection",
                       "description": "d", "parameters": {"type": "object",
                                                          "properties": {}}}}]


def _steps(shell):
    """Étapes AssistantStep émises, sous forme (step.name, attrs)."""
    return [(attrs.get("step.name"), attrs)
            for span, attrs in shell.telemetry_events
            if span == telemetry_steps.SPAN]


def _http_error(status, body=b"{}"):
    return urllib.error.HTTPError("http://fake", status, "err", {},
                                  io.BytesIO(body))


# Bascule natif → JSON

def test_fallback_to_json_is_telemetered_once():
    shell = FakeShell(
        config={"llm_tool_mode": "auto"},
        responses=[_http_error(400), FakeSSEResponse(text_chunks("ok"))])
    LLMClient(shell).step([{"role": "user", "content": "x"}], tools=TOOLS)

    steps = _steps(shell)
    assert len(steps) == 1
    name, attrs = steps[0]
    assert name == telemetry_steps.TOOLS_FALLBACK_JSON
    assert attrs["llm.status"] == "http_400"


def test_no_fallback_step_on_a_plain_success():
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[FakeSSEResponse(text_chunks("ok"))])
    LLMClient(shell).step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert _steps(shell) == []


# Reprise 401

def test_recovered_auth_is_telemetered():
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[_http_error(401), FakeSSEResponse(text_chunks("ok"))],
        recover_auth_result=True)
    step = LLMClient(shell).step([{"role": "user", "content": "x"}], tools=TOOLS)

    assert step.text == "ok"
    assert [name for name, _ in _steps(shell)] == [telemetry_steps.AUTH_RECOVERED]


def test_a_failed_recovery_is_not_reported_as_recovered():
    """Le second 401 est déjà couvert par LlmRelayError côté coquille : dire
    « récupéré » ici serait un mensonge de tableau de bord."""
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[_http_error(401), _http_error(401)])
    step = LLMClient(shell).step([{"role": "user", "content": "x"}], tools=TOOLS)

    assert step.error == "http_401"
    assert telemetry_steps.AUTH_RECOVERED not in [n for n, _ in _steps(shell)]
