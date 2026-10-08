"""Trace fonctionnelle : agrégeable côté observabilité, muette sur le document.

Le journal d'actions cite le document (« ↳ Titre conservé : « Rapport annuel
2026 » »). Il part dans l'onglet Actions et dans `~/log.txt`, qui restent sur le
poste. La télémétrie, elle, QUITTE la machine : le filtre ci-dessous est ce qui
garantit qu'aucune phrase du document ne l'accompagne — y compris le jour où un
appelant transmettra une chaîne libre sans avoir lu le module.
"""

from src.mirai.core import telemetry_steps


class FakeShell:
    def __init__(self):
        self.spans = []

    def telemetry(self, name, attributes=None):
        self.spans.append((name, attributes or {}))


# Ce qui ne doit JAMAIS partir

def test_document_content_never_reaches_telemetry():
    """Le cas réel : un titre de document passé par mégarde en attribut."""
    dirty = {
        "document.title": "Rapport annuel 2026 — synthèse",
        "paragraph": "Le chat dort sur le canapé toute la journée.",
        "prompt": "réduis à deux paragraphes",
        "user.email": "prenom.nom@interieur.gouv.fr",
    }
    assert telemetry_steps.safe_attributes(dirty) == {}


def test_numbers_and_flags_pass_through():
    kept = telemetry_steps.safe_attributes({
        "document.paragraphs": 45,
        "result.paragraphs": 2,
        "append.mode": True,
        "ratio": 0.5,
    })
    assert kept == {"document.paragraphs": 45, "result.paragraphs": 2,
                    "append.mode": True, "ratio": 0.5}


def test_short_labels_pass_but_sentences_do_not():
    kept = telemetry_steps.safe_attributes({
        "preset.name": "resume",          # étiquette : passe
        "finish.reason": "length",        # étiquette : passe
        "message": "Résumé du document",  # majuscules + espaces : écarté
        "note": "ok mais avec espaces",   # espaces : écarté
    })
    assert kept == {"preset.name": "resume", "finish.reason": "length"}


def test_a_long_slug_is_refused():
    """Une chaîne longue est suspecte, même sans espace : on refuse."""
    assert telemetry_steps.safe_attributes({"k": "a" * 41}) == {}
    assert telemetry_steps.safe_attributes({"k": "a" * 40}) == {"k": "a" * 40}


def test_a_hostile_key_is_refused():
    assert telemetry_steps.safe_attributes({"Contenu du document": 3}) == {}


# Ce qui doit partir

def test_a_known_step_is_emitted_with_its_name():
    shell = FakeShell()
    ok = telemetry_steps.emit(shell, telemetry_steps.DOCUMENT_DONE,
                              {"body.paragraphs": 45, "result.paragraphs": 2})
    assert ok
    name, attrs = shell.spans[0]
    assert name == telemetry_steps.SPAN
    assert attrs["step.name"] == "document.rewrite.done"
    assert attrs["body.paragraphs"] == 45


def test_an_unknown_step_is_never_emitted():
    """Le vocabulaire est fermé : pas de nom d'étape inventé au fil de l'eau."""
    shell = FakeShell()
    assert telemetry_steps.emit(shell, "document.something.new") is False
    assert shell.spans == []


def test_every_declared_step_is_accepted():
    shell = FakeShell()
    for step in telemetry_steps.STEPS:
        assert telemetry_steps.emit(shell, step), step
    assert len(shell.spans) == len(telemetry_steps.STEPS)


def test_a_broken_telemetry_never_breaks_the_run():
    class Broken:
        def telemetry(self, *_a, **_k):
            raise OSError("réseau coupé")

    assert telemetry_steps.emit(Broken(), telemetry_steps.DOCUMENT_READ) is False


# Vocabulaire étendu (fiabilité, usage, santé)

def test_reliability_and_usage_steps_are_declared():
    """Chaque angle mort identifié a sa constante — jamais de chaîne libre."""
    expected = {
        telemetry_steps.TOOLS_FALLBACK_JSON: "llm.tools_fallback_json",
        telemetry_steps.AUTH_RECOVERED: "llm.auth_recovered",
        telemetry_steps.REASONING_RETRY_OK: "llm.reasoning_retry_ok",
        telemetry_steps.RUN_REFUSED: "run.refused",
        telemetry_steps.PALETTE_CLOSED: "palette.closed",
        telemetry_steps.PALETTE_REFOCUSED: "palette.refocused",
        telemetry_steps.UI_HEAL_STUCK: "ui.heal_stuck",
    }
    for constant, value in expected.items():
        assert constant == value
        assert constant in telemetry_steps.STEPS, value


# Span de run unifié (emit_run)

def test_emit_run_uses_the_assistant_run_span():
    shell = FakeShell()
    ok = telemetry_steps.emit_run(shell, {
        "run.kind": "pipeline",
        "assistant.preset": "summarize",
        "assistant.ok": True,
        "assistant.duration_ms": 1234,
    })
    assert ok
    name, attrs = shell.spans[0]
    assert name == "AssistantRun"
    assert attrs["plugin.action"] == "assistant.run"
    assert attrs["run.kind"] == "pipeline"
    assert attrs["assistant.ok"] is True
    assert attrs["assistant.duration_ms"] == 1234


def test_emit_run_filters_free_text_like_emit_does():
    """Le run unifié passe par le MÊME filtre : une phrase n'en sort jamais."""
    shell = FakeShell()
    telemetry_steps.emit_run(shell, {
        "run.kind": "agentic",
        "assistant.reason": "http_429",
        "document.title": "Rapport annuel 2026 — synthèse",
        "Contenu du document": "Le chat dort sur le canapé.",
    })
    _name, attrs = shell.spans[0]
    assert attrs["assistant.reason"] == "http_429"
    assert "Rapport annuel" not in str(attrs)
    assert "chat" not in str(attrs)


def test_emit_run_never_breaks_the_run():
    class Broken:
        def telemetry(self, *_a, **_k):
            raise OSError("réseau coupé")

    assert telemetry_steps.emit_run(Broken(), {"run.kind": "agentic"}) is False
