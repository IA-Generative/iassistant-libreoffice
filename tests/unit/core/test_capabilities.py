"""Sonde de capacités : mesurer ce que le modèle sait faire, pas le supposer.

Mesures réelles ayant motivé ce module (Ollama, 2026-07-26) — trois modèles,
trois comportements :

    llama3.2      accepte ✓  appelle ✓  enchaîne ✓
    gemma4:12b    accepte ✓  appelle ✓  enchaîne ✗   (lit puis s'arrête)
    mistral       accepte ✓  appelle ✗  enchaîne ✗   (répond du texte)

C'est la capacité « enchaîne » qui décide du chemin d'exécution : sans elle, un
run agentique se termine en succès en laissant le document intact.
"""

from unittest.mock import MagicMock

from src.mirai.core.capabilities import (
    Capabilities,
    cache_key,
    load_cached,
    probe,
    save_cached,
)
from src.mirai.core.tool_calls import ToolCall


class _Step:
    def __init__(self, tool_calls=None, text="", error=None):
        self.tool_calls = tool_calls or []
        self.text = text
        self.error = error


class ScriptedLLM:
    """Rejoue une séquence de réponses, en notant les appels reçus."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = 0

    def step(self, messages, tools=None, **_kwargs):
        self.calls += 1
        return self._steps.pop(0)

    def encode_tool_exchange(self, _step, results):
        return [{"role": "tool", "content": r.content} for r in results]


def _read_call():
    return ToolCall(id="1", name="writer_get_document_map", arguments={})


def _write_call():
    return ToolCall(id="2", name="writer_replace_paragraphs",
                    arguments={"start": 1, "text": "x"})


def test_model_that_chains_read_then_write():
    """Profil llama3.2 : lit au premier tour, écrit au second."""
    llm = ScriptedLLM([_Step([_read_call()]), _Step([_write_call()])])

    verdict = probe(llm, model="llama3.2")

    assert verdict.accepts_tools and verdict.calls_tool and verdict.chains
    assert verdict.supports_agentic is True
    assert llm.calls == 2


def test_model_that_writes_immediately_also_chains():
    llm = ScriptedLLM([_Step([_write_call()])])

    verdict = probe(llm, model="direct")

    assert verdict.chains is True
    assert llm.calls == 1, "inutile d'un second tour si l'écriture a eu lieu"
    assert "premier tour" in verdict.detail


def test_model_that_reads_then_stops():
    """Profil gemma4 / llama-3.3 : LE cas qui laissait le document intact."""
    llm = ScriptedLLM([_Step([_read_call()]), _Step(text="Voici le texte…")])

    verdict = probe(llm, model="gemma4:12b")

    assert verdict.accepts_tools and verdict.calls_tool
    assert verdict.chains is False
    assert verdict.supports_agentic is False


def test_model_that_never_calls_a_tool():
    """Profil mistral : répond du texte malgré les outils proposés."""
    llm = ScriptedLLM([_Step(text="Bien sûr, voici…")])

    verdict = probe(llm, model="mistral")

    assert verdict.accepts_tools is True
    assert verdict.calls_tool is False
    assert verdict.chains is False


def test_relay_refusing_tools():
    llm = ScriptedLLM([_Step(error="http_400")])

    verdict = probe(llm, model="ancien")

    assert verdict.accepts_tools is False
    assert "http_400" in verdict.detail


def test_second_turn_failure_is_not_a_chain():
    llm = ScriptedLLM([_Step([_read_call()]), _Step(error="network_error")])

    verdict = probe(llm, model="instable")

    assert verdict.chains is False
    assert "second tour" in verdict.detail


def test_summary_is_actionable_and_jargon_free():
    for verdict, expected in (
        (Capabilities(chains=True, calls_tool=True, accepts_tools=True),
         "agentique"),
        (Capabilities(accepts_tools=True, calls_tool=True), "chemin direct"),
        (Capabilities(accepts_tools=True), "n'utilise pas les outils"),
        (Capabilities(), "n'accepte pas les outils"),
    ):
        summary = verdict.summary()
        assert expected in summary
        assert "tool_call" not in summary, "pas de jargon dans un message utilisateur"


class FakeShell:
    def __init__(self):
        self.config = {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


def test_verdict_survives_a_round_trip():
    shell = FakeShell()
    verdict = Capabilities(model="m", accepts_tools=True, calls_tool=True,
                           chains=True, detail="ok")

    save_cached(shell, "https://relais/v1", "m", verdict)
    restored = load_cached(shell, "https://relais/v1", "m")

    assert restored.chains is True
    assert restored.model == "m"


def test_cache_is_per_endpoint_and_model():
    """Un verdict vaut pour un couple, pas pour un poste."""
    shell = FakeShell()
    save_cached(shell, "https://a/v1", "m1", Capabilities(chains=True))
    save_cached(shell, "https://a/v1", "m2", Capabilities(chains=False))
    save_cached(shell, "https://b/v1", "m1", Capabilities(chains=False))

    assert load_cached(shell, "https://a/v1", "m1").chains is True
    assert load_cached(shell, "https://a/v1", "m2").chains is False
    assert load_cached(shell, "https://b/v1", "m1").chains is False


def test_saving_one_model_keeps_the_others():
    shell = FakeShell()
    save_cached(shell, "e", "m1", Capabilities(chains=True))
    save_cached(shell, "e", "m2", Capabilities(chains=False))

    assert load_cached(shell, "e", "m1") is not None


def test_unknown_model_has_no_verdict():
    assert load_cached(FakeShell(), "e", "jamais-testé") is None


def test_corrupted_cache_is_tolerated():
    """Un cache illisible doit relancer une sonde, jamais faire échouer."""
    shell = FakeShell()
    shell.config["assistant_model_capabilities"] = "{pas du json"

    assert load_cached(shell, "e", "m") is None
    save_cached(shell, "e", "m", Capabilities(chains=True))
    assert load_cached(shell, "e", "m").chains is True


def test_cache_key_ignores_surrounding_spaces():
    assert cache_key(" https://a/v1 ", " m ") == cache_key("https://a/v1", "m")


def test_save_never_raises_when_config_is_read_only():
    shell = MagicMock()
    shell.get_config.return_value = ""
    shell.set_config.side_effect = OSError("lecture seule")

    save_cached(shell, "e", "m", Capabilities())   # ne doit pas lever
