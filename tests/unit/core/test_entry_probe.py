"""« Tester le modèle » : seule une sonde aboutie devient un verdict.

Une sonde interrompue (jeton refusé, délai dépassé, relais indisponible) ne dit
rien du modèle. La mémoriser écraserait le verdict précédent et figerait le
modèle hors du mode agentique jusqu'à la prochaine sonde réussie ; l'annoncer
comme un verdict tromperait l'utilisateur.
"""

import io
import urllib.error

import pytest

from src.mirai.core import capabilities, entry
from src.mirai.i18n import t
from tests.stubs.fake_shell import (
    FakeShell,
    FakeSSEResponse,
    native_tool_call_chunks,
    text_chunks,
)

ENDPOINT = "http://relay"
MODEL = "llama3.2"
VERDICT_SENTENCES = ("caps.no_tools", "caps.no_calls", "caps.no_chains", "caps.full")


def _http_error(status):
    return urllib.error.HTTPError("http://fake", status, "err", {},
                                  io.BytesIO(b"{}"))


# Scénario → (réponses du relais, erreur attendue dans le message).
INTERRUPTED = {
    "jeton_refuse": (lambda: [_http_error(401), _http_error(401)], "http_401"),
    "delai_depasse": (lambda: [TimeoutError("timed out")], "network_error"),
    "second_tour_interrompu": (
        lambda: [FakeSSEResponse(native_tool_call_chunks(
                     "writer_get_document_map", "{}")),
                 _http_error(503)],
        "http_503"),
}


class _Job:
    """Ce que « Tester le modèle » affiche à l'utilisateur."""

    def __init__(self):
        self.messages = []

    def _show_message(self, _title, text):
        self.messages.append(text)


def _shell(responses, previous=None):
    shell = FakeShell(config={"llm_base_urls": ENDPOINT,
                              "llm_default_models": MODEL},
                      responses=responses)
    if previous is not None:
        capabilities.save_cached(shell, ENDPOINT, MODEL, previous)
    return shell


def _probe_from_menu(monkeypatch, shell):
    monkeypatch.setattr(entry, "MainJobShell", lambda _job: shell)
    job = _Job()
    entry.test_model_capabilities(job)
    return job.messages


AGENTIC = capabilities.Capabilities(model=MODEL, accepts_tools=True,
                                    calls_tool=True, chains=True)


@pytest.mark.parametrize("scenario", INTERRUPTED)
def test_an_interrupted_probe_keeps_the_previous_verdict(monkeypatch, scenario):
    responses, _error = INTERRUPTED[scenario]
    shell = _shell(responses(), previous=AGENTIC)

    _probe_from_menu(monkeypatch, shell)

    assert capabilities.load_cached(shell, ENDPOINT, MODEL).supports_agentic is True


def test_an_interrupted_probe_leaves_the_model_unmeasured(monkeypatch):
    shell = _shell([_http_error(401), _http_error(401)])

    _probe_from_menu(monkeypatch, shell)

    assert capabilities.load_cached(shell, ENDPOINT, MODEL) is None


@pytest.mark.parametrize("scenario", INTERRUPTED)
def test_an_interrupted_probe_is_reported_as_a_failed_test(monkeypatch, scenario):
    responses, error = INTERRUPTED[scenario]

    [shown] = _probe_from_menu(monkeypatch, _shell(responses()))

    assert shown.startswith(t("entry.test_failed", detail=""))
    assert error in shown
    assert not [key for key in VERDICT_SENTENCES if t(key) in shown]


def test_a_relay_refusing_tools_in_native_mode_is_a_verdict(monkeypatch):
    """Mode natif imposé : sans repli JSON, le refus des outils est mesuré et
    mémorisé, même par-dessus un verdict agentique devenu faux."""
    shell = _shell([_http_error(400)], previous=AGENTIC)
    shell.config["llm_tool_mode"] = "native"

    [shown] = _probe_from_menu(monkeypatch, shell)

    verdict = capabilities.load_cached(shell, ENDPOINT, MODEL)
    assert verdict.accepts_tools is False
    assert verdict.supports_agentic is False
    assert t("caps.no_tools") in shown


def test_a_model_answering_without_tools_is_still_a_verdict(monkeypatch):
    shell = _shell([FakeSSEResponse(text_chunks("Bien sûr, voici…"))],
                   previous=AGENTIC)

    [shown] = _probe_from_menu(monkeypatch, shell)

    verdict = capabilities.load_cached(shell, ENDPOINT, MODEL)
    assert verdict.calls_tool is False
    assert verdict.supports_agentic is False
    assert t("caps.no_calls") in shown
