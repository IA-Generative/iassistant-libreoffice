"""Contrat de bout en bout : ce que le moteur émet, ce qui part sur le fil.

Les tests unitaires vérifient que le moteur appelle `shell.telemetry` avec les
bons attributs. Ils s'arrêtent là — et c'est exactement où la panne se logeait :
le payload OTLP est ensuite construit par la coquille, et le Device Management
ne relisait que `stringValue`. Résultat, un `document.paragraphs: 45` partait
correctement du moteur et arrivait VIDE en base : chaque compteur ajouté ici
aurait été silencieusement perdu.

Ces tests couvrent le trajet complet, sans réseau : attributs émis → encodage
OTLP de la coquille → relecture par la logique du DM. Ils échouent si l'un des
trois maillons cesse de préserver les types.

La relecture reproduit `_persist_telemetry_spans` (device-management,
app/main.py) : les deux dépôts sont livrés ensemble, et ce contrat est
précisément ce qui les lie.
"""

import pytest

from src.mirai.core import telemetry_steps
from tests.stubs.uno_stubs import install

install()

from src.mirai.entrypoint import otel_attributes


def _read_otlp_value(value):
    """Relecture typée d'un AnyValue OTLP — miroir du lecteur du DM."""
    if not isinstance(value, dict):
        return ""
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    return ""


def _round_trip(attributes):
    """Attributs émis → encodage OTLP → relecture côté serveur."""
    encoded = otel_attributes(attributes)
    return {entry["key"]: _read_otlp_value(entry["value"]) for entry in encoded}


class _RecordingShell:
    def __init__(self):
        self.spans = []

    def telemetry(self, name, attributes=None):
        self.spans.append((name, dict(attributes or {})))


# Le défaut d'origine, verrouillé

def test_counters_survive_the_wire_as_numbers():
    """Le cas qui arrivait vide en base : des compteurs devenus chaînes."""
    read_back = _round_trip({
        "document.paragraphs": 45,
        "session.duration_ms": 754321,
        "tool.args_coerced": 2,
    })
    assert read_back == {"document.paragraphs": 45,
                         "session.duration_ms": 754321,
                         "tool.args_coerced": 2}
    assert all(isinstance(v, int) for v in read_back.values())


def test_flags_survive_as_booleans_not_as_ones_and_zeros():
    """`True` est un entier en Python : un encodage naïf enverrait 1."""
    read_back = _round_trip({"assistant.ok": True, "append.mode": False,
                             "caps.agentic": False})
    assert read_back == {"assistant.ok": True, "append.mode": False,
                         "caps.agentic": False}
    assert all(isinstance(v, bool) for v in read_back.values())


def test_labels_survive_as_text():
    read_back = _round_trip({"run.kind": "document_rewrite",
                             "assistant.reason": "reasoning_starved"})
    assert read_back == {"run.kind": "document_rewrite",
                         "assistant.reason": "reasoning_starved"}


# Chaque nouveauté, du moteur jusqu'au fil

RUN_ATTRIBUTES = {
    "run.kind": "agentic",
    "assistant.preset": "formula",
    "assistant.ok": False,
    "assistant.cancelled": True,
    "assistant.reason": "cancelled",
    "assistant.mode": "json",
    "assistant.iterations": 3,
    "assistant.duration_ms": 12034,
    "append.mode": True,
}


def test_the_unified_run_span_reaches_the_wire_intact():
    shell = _RecordingShell()
    assert telemetry_steps.emit_run(shell, RUN_ATTRIBUTES)

    name, emitted = shell.spans[0]
    assert name == "AssistantRun"
    read_back = _round_trip(emitted)
    for key, value in RUN_ATTRIBUTES.items():
        assert read_back[key] == value, key
        assert isinstance(read_back[key], type(value)), key
    assert read_back["plugin.action"] == "assistant.run"


@pytest.mark.parametrize("step", sorted(telemetry_steps.STEPS))
def test_every_declared_step_survives_the_wire(step):
    """Le vocabulaire est fermé : chaque étape doit franchir le fil entière."""
    shell = _RecordingShell()
    assert telemetry_steps.emit(shell, step, {"probe.count": 7,
                                              "probe.flag": True})

    _name, emitted = shell.spans[0]
    read_back = _round_trip(emitted)
    assert read_back["step.name"] == step
    assert read_back["probe.count"] == 7
    assert read_back["probe.flag"] is True


def test_the_run_duration_is_measured_and_not_hardcoded():
    """Une durée toujours nulle passerait inaperçue : tous les runs de test sont
    instantanés. On en fait durer un pour de vrai.
    """
    import time
    from unittest.mock import MagicMock

    from src.mirai.core.ui_thread import DirectDispatcher
    from src.mirai.ui import dsfr
    from src.mirai.ui import palette as palette_module
    from tests.unit.core.test_palette_build import FakeDialog

    dialog = FakeDialog()
    dsfr.make_dialog = lambda *_a, **_k: (dialog, dialog.model)
    dsfr.probe_font = lambda _toolkit: "Arial"
    palette_module._open_palette = [None]
    palette_module.MainThreadDispatcher = lambda _ctx, log=None: DirectDispatcher()

    spans = []
    shell = MagicMock()
    shell.toolkit.return_value = MagicMock()
    shell.data_dir.return_value = "/tmp/mirai-duration-test"
    shell.get_config.side_effect = lambda key, default=None: default
    shell.log = lambda _m: None
    shell.telemetry = lambda name, attrs=None: spans.append((name, dict(attrs or {})))

    palette = palette_module.AssistantPalette(MagicMock(), shell, "writer")

    def _slow(*_a, **_k):
        time.sleep(0.2)
        return {"ok": True}

    palette._run_agentic = _slow
    palette._run_in_worker(None, "demande", MagicMock(), "demande")

    run = next(a for name, a in spans if name == "AssistantRun")
    assert isinstance(run["assistant.duration_ms"], int)
    assert run["assistant.duration_ms"] >= 150, (
        f"durée non mesurée : {run['assistant.duration_ms']} ms")


def test_document_content_never_reaches_the_wire_even_typed():
    """Le filtre agit AVANT l'encodage : rien à rattraper côté serveur."""
    shell = _RecordingShell()
    telemetry_steps.emit(shell, telemetry_steps.DOCUMENT_READ, {
        "document.title": "Rapport annuel 2026 — synthèse",
        "document.paragraphs": 45,
    })
    _name, emitted = shell.spans[0]
    read_back = _round_trip(emitted)

    assert read_back["document.paragraphs"] == 45
    assert "document.title" not in read_back
    assert "Rapport annuel" not in str(read_back)
