"""Encodage OTLP des attributs : conserver les types, sinon la mesure est perdue."""

from src.mirai.entrypoint import otel_attributes


def _by_key(encoded):
    return {a["key"]: a["value"] for a in encoded}


def test_integers_survive_as_integers():
    values = _by_key(otel_attributes({"document.paragraphs": 45}))
    # OTLP/JSON code les int64 en chaîne — pour la précision, pas par défaut de typage.
    assert values["document.paragraphs"] == {"intValue": "45"}


def test_booleans_are_not_encoded_as_integers():
    """`True` est un `int` en Python : l'ordre des tests de type compte."""
    values = _by_key(otel_attributes({"append.mode": True}))
    assert values["append.mode"] == {"boolValue": True}


def test_floats_keep_their_precision():
    values = _by_key(otel_attributes({"ratio": 0.5}))
    assert values["ratio"] == {"doubleValue": 0.5}


def test_strings_pass_through():
    values = _by_key(otel_attributes({"step.name": "document.rewrite.done"}))
    assert values["step.name"] == {"stringValue": "document.rewrite.done"}


def test_anything_else_is_rendered_as_text():
    values = _by_key(otel_attributes({"weird": [1, 2]}))
    assert values["weird"] == {"stringValue": "[1, 2]"}


def test_no_attributes_gives_an_empty_list():
    assert otel_attributes(None) == []
    assert otel_attributes({}) == []


def test_a_full_functional_step_round_trips():
    encoded = otel_attributes({
        "step.name": "document.rewrite.done",
        "body.paragraphs": 45,
        "result.paragraphs": 2,
        "append.mode": False,
    })
    assert len(encoded) == 4
    values = _by_key(encoded)
    assert values["body.paragraphs"] == {"intValue": "45"}
    assert values["append.mode"] == {"boolValue": False}
