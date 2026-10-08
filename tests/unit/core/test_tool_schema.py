"""Validateur JSON-schema (sous-ensemble) : types, coercitions, défauts, bornes."""

from src.mirai.core.tool_calls import validate_args

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "count": {"type": "integer", "minimum": 1, "maximum": 10},
        "ratio": {"type": "number"},
        "flag": {"type": "boolean", "default": False},
        "mode": {"type": "string", "enum": ["a", "b"], "default": "a"},
        "items": {"type": "array", "items": {"type": "string"}},
        "pairs": {"type": "array", "items": {"type": "object", "properties": {
            "find": {"type": "string"}}, "required": ["find"]}},
    },
    "required": ["text"],
}


def test_valid_passthrough():
    ok, err, args = validate_args(SCHEMA, {"text": "bonjour", "count": 3})
    assert ok and err == ""
    assert args["text"] == "bonjour"
    assert args["count"] == 3


def test_required_missing():
    ok, err, _ = validate_args(SCHEMA, {"count": 3})
    assert not ok
    assert "requis" in err and "text" in err


def test_string_coercion_from_number():
    ok, _, args = validate_args(SCHEMA, {"text": 42})
    assert ok and args["text"] == "42"


def test_integer_coercion_from_string():
    ok, _, args = validate_args(SCHEMA, {"text": "x", "count": "7"})
    assert ok and args["count"] == 7


def test_integer_rejects_garbage():
    ok, err, _ = validate_args(SCHEMA, {"text": "x", "count": "beaucoup"})
    assert not ok and "count" in err


def test_boolean_coercion():
    ok, _, args = validate_args(SCHEMA, {"text": "x", "flag": "true"})
    assert ok and args["flag"] is True


def test_defaults_applied():
    ok, _, args = validate_args(SCHEMA, {"text": "x"})
    assert ok
    assert args["flag"] is False
    assert args["mode"] == "a"


def test_enum_rejected():
    ok, err, _ = validate_args(SCHEMA, {"text": "x", "mode": "z"})
    assert not ok and "mode" in err


def test_out_of_bounds_is_clamped_not_rejected():
    """Les bornes protègent l'appel, elles n'expriment pas une exigence métier :
    on ramène dans les clous au lieu de rejeter, comme `maxLength` tronque déjà
    les chaînes.
    """
    ok, err, args = validate_args(SCHEMA, {"text": "x", "count": 99})
    assert ok and err == ""
    assert args["count"] == 10

    ok, _, args = validate_args(SCHEMA, {"text": "x", "count": 0})
    assert ok and args["count"] == 1


def test_clamping_lands_inside_fractional_bounds():
    """int(borne) trahirait : int(0.5) == 0, soit toujours sous le minimum."""
    schema = {"type": "object", "properties": {
        "n": {"type": "integer", "minimum": 0.5, "maximum": 9.5},
        "r": {"type": "number", "minimum": 0.5, "maximum": 9.5}}}

    _, _, args = validate_args(schema, {"n": 0, "r": 0})
    assert args["n"] == 1 and args["r"] == 0.5

    _, _, args = validate_args(schema, {"n": 99, "r": 99})
    assert args["n"] == 9 and args["r"] == 9.5


def test_clamping_does_not_rescue_a_wrong_type():
    """La tolérance porte sur l'amplitude, pas sur la nature de la valeur."""
    ok, err, _ = validate_args(SCHEMA, {"text": "x", "count": "beaucoup"})
    assert not ok and "count" in err


def test_array_items_coerced():
    ok, _, args = validate_args(SCHEMA, {"text": "x", "items": [1, "deux"]})
    assert ok and args["items"] == ["1", "deux"]


def test_nested_object_required():
    ok, err, _ = validate_args(SCHEMA, {"text": "x", "pairs": [{"nope": 1}]})
    assert not ok and "find" in err


def test_non_dict_args():
    ok, err, _ = validate_args(SCHEMA, "pas un objet")
    assert not ok


def test_unknown_keys_kept():
    ok, _, args = validate_args(SCHEMA, {"text": "x", "extra": 1})
    assert ok and args["extra"] == 1
