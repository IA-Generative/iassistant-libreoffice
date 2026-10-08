"""FakeShell : implémentation de ShellServices pour tester le moteur sans
MainJob ni réseau. FakeSSEResponse simule un flux SSE chat/completions."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


class FakeSSEResponse:
    """Réponse HTTP streamée factice — itérable de lignes SSE, context manager."""

    def __init__(self, chunks, raw_lines=None):
        if raw_lines is not None:
            self._lines = raw_lines
        else:
            self._lines = [
                b"data: " + json.dumps(c, ensure_ascii=False).encode("utf-8")
                for c in chunks
            ] + [b"data: [DONE]", b""]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)


def text_chunks(*texts, finish="stop"):
    """Chunks SSE simulant un texte streamé delta par delta."""
    chunks = [{"choices": [{"delta": {"content": t}}]} for t in texts]
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return chunks


def reasoning_chunks(*reasoning, content="", finish="length", key="reasoning"):
    """Chunks SSE d'un modèle qui RÉFLÉCHIT avant de répondre.

    Reproduit le fil observé sur `gemma-4-26b-a4b-it` : une longue série de
    deltas de raisonnement, puis — seulement à la fin — le contenu utile.
    Avec `finish="length"` et `content=""`, c'est la panne mesurée : le plafond
    de tokens est atteint pendant la réflexion, la réponse n'existe jamais.
    """
    chunks = [{"choices": [{"delta": {key: r}}]} for r in reasoning]
    if content:
        chunks.append({"choices": [{"delta": {"content": content}}]})
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return chunks


def native_tool_call_chunks(name, arguments_json, call_id="call_abc"):
    """Chunks SSE simulant un tool call natif fragmenté."""
    half = max(1, len(arguments_json) // 2)
    return [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": call_id,
             "function": {"name": name, "arguments": arguments_json[:half]}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": arguments_json[half:]}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]


class FakeShell:
    """ShellServices factice : config en dict, réponses HTTP scriptées."""

    def __init__(self, config=None, responses=None, config_dir="/tmp",
                 recover_auth_result=False):
        self.config = dict(config or {})
        self.responses = list(responses or [])
        self.requests = []           # corps JSON de chaque requête émise
        self.urlopen_calls = 0       # nombre d'ouvertures de flux effectives
        self.telemetry_events = []   # (span, attrs)
        self.llm_errors = []         # (status, body)
        self.logs = []
        self._config_dir = config_dir
        self.recover_auth_calls = 0
        self._recover_auth_result = recover_auth_result

    # Config
    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value

    def data_dir(self):
        return self._config_dir

    # Transport
    def build_chat_request(self, messages, max_tokens=2000, extra_body=None):
        body = {"messages": messages, "max_tokens": max_tokens,
                "temperature": 0.2, "stream": True}
        model = self.config.get("llm_default_models")
        if model:
            body["model"] = model
        if extra_body:
            body.update(extra_body)
        self.requests.append(body)
        return SimpleNamespace(
            full_url="http://fake/api/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def recover_llm_auth(self):
        self.recover_auth_calls += 1
        return self._recover_auth_result

    def urlopen(self, request, timeout=None):
        self.urlopen_calls += 1
        if not self.responses:
            raise AssertionError("FakeShell : aucune réponse scriptée restante")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def request_timeout(self):
        return 5

    # Erreurs / télémétrie
    def report_llm_error(self, status_code, body, headers=None):
        self.llm_errors.append((status_code, body))
        return f"http_{status_code}", None

    def report_llm_network_error(self, reason):
        self.llm_errors.append((0, str(reason)))

    def telemetry(self, span_name, attributes=None):
        self.telemetry_events.append((span_name, dict(attributes or {})))

    def log(self, message):
        self.logs.append(str(message))

    # UNO
    def toolkit(self):
        return MagicMock()
