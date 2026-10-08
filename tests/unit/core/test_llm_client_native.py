"""Client LLM : assemblage natif fragmenté, rétention JSON, détection auto."""

import io
import urllib.error

from src.mirai.core.llm_client import LLMClient, StepResult
from src.mirai.core.tool_calls import ToolCall, ToolResult
from tests.stubs.fake_shell import (
    FakeShell,
    FakeSSEResponse,
    native_tool_call_chunks,
    text_chunks,
)

TOOLS = [{"type": "function",
          "function": {"name": "writer_get_selection",
                       "description": "d", "parameters": {"type": "object",
                                                          "properties": {}}}}]


def test_text_only_streams_live():
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Bon", "jour"))])
    client = LLMClient(shell)
    deltas = []
    step = client.step([{"role": "user", "content": "salut"}],
                       on_text_delta=deltas.append)
    assert step.text == "Bonjour"
    assert step.streamed and deltas == ["Bon", "jour"]
    assert step.tool_calls == []


def test_native_fragmented_tool_call_assembled():
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[FakeSSEResponse(
            native_tool_call_chunks("calc_read_range", '{"range": "A1:B2"}'))])
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "lis"}], tools=TOOLS)
    assert len(step.tool_calls) == 1
    call = step.tool_calls[0]
    assert call.name == "calc_read_range"
    assert call.arguments == {"range": "A1:B2"}
    assert call.id == "call_abc"
    assert step.finish_reason == "tool_calls"
    # la requête portait bien les tools
    assert shell.requests[0].get("tools") == TOOLS


def test_json_mode_no_tools_in_body():
    shell = FakeShell(
        config={"llm_tool_mode": "json"},
        responses=[FakeSSEResponse(text_chunks(
            '{"tool_calls": [{"name": "writer_get_selection", "arguments": {}}]}'))])
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert "tools" not in shell.requests[0]
    assert len(step.tool_calls) == 1
    assert step.raw_json


def test_json_mode_withholds_tool_call_from_sink():
    shell = FakeShell(
        config={"llm_tool_mode": "json"},
        responses=[FakeSSEResponse(text_chunks(
            '{"tool_calls": [{"na', 'me": "writer_get_selection", "arguments": {}}]}'))])
    client = LLMClient(shell)
    deltas = []
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS,
                       on_text_delta=deltas.append)
    assert deltas == []                 # rien n'a fui vers le document
    assert len(step.tool_calls) == 1


def test_json_mode_streams_plain_answer_live():
    shell = FakeShell(
        config={"llm_tool_mode": "json"},
        responses=[FakeSSEResponse(text_chunks("Voici ", "la réponse."))])
    client = LLMClient(shell)
    deltas = []
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS,
                       on_text_delta=deltas.append)
    assert step.text == "Voici la réponse."
    assert step.streamed
    assert "".join(deltas) == "Voici la réponse."


def test_json_mode_withheld_non_json_never_lost():
    # Commence par '{' mais n'est pas un tool call → texte restitué à la fin.
    shell = FakeShell(
        config={"llm_tool_mode": "json"},
        responses=[FakeSSEResponse(text_chunks('{"resultat"', ': "pas un tool"}'))])
    client = LLMClient(shell)
    deltas = []
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS,
                       on_text_delta=deltas.append)
    assert step.tool_calls == []
    assert step.text == '{"resultat": "pas un tool"}'
    assert not step.streamed            # le sink le recevra via finish()


def test_auto_mode_flips_to_json_on_http_400():
    error = urllib.error.HTTPError("http://fake", 400, "bad",
                                   {}, io.BytesIO(b'{"error":"tools"}'))
    shell = FakeShell(
        config={"llm_tool_mode": "auto"},
        responses=[error, FakeSSEResponse(text_chunks("ok sans tools"))])
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert shell.config.get("llm_tool_mode_detected") == "json"
    assert step.text == "ok sans tools"
    assert "tools" in shell.requests[0]      # 1er essai natif
    assert "tools" not in shell.requests[1]  # retry JSON


def test_http_error_returned_as_step_error():
    error = urllib.error.HTTPError("http://fake", 429, "quota",
                                   {}, io.BytesIO(b"{}"))
    shell = FakeShell(config={"llm_tool_mode": "native"}, responses=[error])
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert step.error == "http_429"


def _unauthorized():
    return urllib.error.HTTPError(
        "http://fake", 401, "Unauthorized", {},
        io.BytesIO(b'{"error":{"code":"invalid_api_key"}}'))


def test_401_recovers_auth_and_retries_once():
    """Le llmToken du proxy DM est court et peut être révoqué : un 401 doit
    déclencher une récupération d'auth puis UNE seule re-tentative."""
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[_unauthorized(), FakeSSEResponse(text_chunks("ok"))],
        recover_auth_result=True)
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert step.text == "ok"
    assert shell.recover_auth_calls == 1


def test_401_twice_gives_up_without_looping():
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[_unauthorized(), _unauthorized()])
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert step.error == "http_401"
    assert shell.recover_auth_calls == 1


def test_401_recovery_happens_inside_the_stream():
    """La reprise d'authentification fait du réseau bloquant.

    Elle doit donc se produire à l'intérieur du pump, c'est-à-dire dans le
    thread qui exécute le run (le worker), et jamais sur
    le thread principal. On le vérifie par la position de l'appel dans la
    séquence : la reprise s'intercale entre l'échec et la reconstruction de la
    requête, sans repasser par l'appelant.
    """
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[_unauthorized(), FakeSSEResponse(text_chunks("ok"))],
        recover_auth_result=True)
    order = []
    original_recover = shell.recover_llm_auth
    original_build = shell.build_chat_request

    def _tracked_recover():
        order.append("recover")
        return original_recover()

    def _tracked_build(messages, max_tokens=2000, extra_body=None):
        order.append("build")
        return original_build(messages, max_tokens, extra_body)

    shell.recover_llm_auth = _tracked_recover
    shell.build_chat_request = _tracked_build

    LLMClient(shell).step([{"role": "user", "content": "x"}], tools=TOOLS)

    assert order == ["build", "recover", "build"], (
        "la reprise doit s'intercaler entre l'échec et la nouvelle requête, "
        f"dans le même thread ; observé : {order}")


def test_network_error_returned_as_step_error():
    shell = FakeShell(responses=[OSError("timed out")])
    client = LLMClient(shell)
    step = client.step([{"role": "user", "content": "x"}])
    assert step.error == "network_error"


def test_encode_tool_exchange_native():
    client = LLMClient(FakeShell(config={"llm_tool_mode": "native"}))
    step = StepResult(tool_calls=[ToolCall(id="c1", name="t", arguments={"a": 1})])
    results = [ToolResult(call_id="c1", ok=True, content="résultat")]
    messages = client.encode_tool_exchange(step, results)
    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["id"] == "c1"
    assert messages[1] == {"role": "tool", "tool_call_id": "c1",
                           "content": "résultat"}


def test_encode_tool_exchange_json():
    client = LLMClient(FakeShell(config={"llm_tool_mode": "json"}))
    raw = '{"tool_calls": [{"name": "t", "arguments": {}}]}'
    step = StepResult(tool_calls=[ToolCall(id="call_0", name="t", arguments={})],
                      raw_json=raw)
    results = [ToolResult(call_id="call_0", ok=False, content="", error="cassé")]
    messages = client.encode_tool_exchange(step, results)
    assert messages[0] == {"role": "assistant", "content": raw}
    assert "RÉSULTATS DES OUTILS" in messages[1]["content"]
    assert "cassé" in messages[1]["content"]


# Bilan de flux

def _summary(shell):
    lines = [line for line in shell.logs if line.startswith("[llm] step")]
    assert len(lines) == 1, f"attendu 1 bilan, vu {len(lines)}"
    return lines[0]


def test_step_logs_stream_summary():
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Bon", "jour"))])
    LLMClient(shell).step([{"role": "user", "content": "salut"}])
    summary = _summary(shell)
    assert "mode=text" in summary
    assert "texte=7c" in summary
    assert "tool_calls=0" in summary
    assert "ok=True" in summary


def test_empty_stream_is_visible_in_log():
    """Un flux sans erreur qui ne livre RIEN doit rester lisible dans le log.

    Sans ce bilan, « aucun chunk reçu » et « chunks reçus mais non exploités »
    produisent exactement la même trace, c'est-à-dire aucune.
    """
    shell = FakeShell(config={"llm_tool_mode": "native"},
                      responses=[FakeSSEResponse([])])
    step = LLMClient(shell).step([{"role": "user", "content": "x"}], tools=TOOLS)
    assert step.text == "" and step.tool_calls == [] and not step.error
    summary = _summary(shell)
    assert "chunks=0" in summary
    assert "texte=0c" in summary
    assert "tool_calls=0" in summary


def test_summary_reports_assembled_tool_calls():
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[FakeSSEResponse(
            native_tool_call_chunks("calc_read_range", '{"range": "A1:B2"}'))])
    LLMClient(shell).step([{"role": "user", "content": "lis"}], tools=TOOLS)
    summary = _summary(shell)
    assert "mode=native" in summary
    assert "tool_calls=1" in summary
    assert "finish=tool_calls" in summary
