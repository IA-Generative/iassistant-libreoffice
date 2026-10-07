"""Moteur de suggestions v1 — heuristiques pures, testables sans LibreOffice."""

from src.mirai.core.suggestions import Suggestion, looks_numeric, render, suggest


def _labels(items):
    return [s.label for s in items]


# Writer

def test_long_selection_proposes_summarising():
    items = suggest("writer", selected_text="x" * 800)
    assert "Résumer ce passage" in _labels(items)


def test_short_selection_proposes_expanding_not_summarising():
    items = suggest("writer", selected_text="Une phrase courte.")
    assert "Résumer ce passage" not in _labels(items)
    assert any("Développer" in label for label in _labels(items))


def test_medium_selection_proposes_simplifying():
    items = suggest("writer", selected_text="x" * 200)
    assert "Simplifier la formulation" in _labels(items)


def test_without_selection_targets_the_current_paragraph():
    """Sans sélection les actions portent sur le paragraphe : le proposer."""
    items = suggest("writer", selected_text="", has_paragraph=True)
    assert "Simplifier ce paragraphe" in _labels(items)


def test_empty_document_still_offers_prompt_starters():
    items = suggest("writer", selected_text="", has_paragraph=False)
    assert items, "une situation vide ne doit pas rendre une liste vide"
    assert all(not s.runs_immediately for s in items), (
        "sans cible, aucune suggestion ne doit s'exécuter directement")


def test_suggestions_are_capped():
    assert len(suggest("writer", selected_text="x" * 800, limit=3)) == 3


# Calc

def test_numeric_range_proposes_analysis():
    items = suggest("calc", cell_count=10, values=["12", "45,5", "7", "103"])
    assert "Analyser ces chiffres" in _labels(items)


def test_text_range_proposes_transformations_not_analysis():
    items = suggest("calc", cell_count=4, values=["Lyon", "Paris", "Brest"])
    assert "Analyser ces chiffres" not in _labels(items)
    assert "Mettre en majuscules" in _labels(items)


def test_no_selection_in_calc_proposes_a_formula():
    items = suggest("calc", cell_count=0)
    assert any("formule" in label.lower() for label in _labels(items))


# Détection numérique

def test_looks_numeric_accepts_common_formats():
    assert looks_numeric(["1 200", "45,5", "-3", "12%", "8 €"])


def test_looks_numeric_tolerates_a_header():
    """Un en-tête de colonne ne doit pas disqualifier une plage de chiffres."""
    assert looks_numeric(["Montant", "120", "340", "55", "78"])


def test_looks_numeric_rejects_text():
    assert not looks_numeric(["Lyon", "Paris", "Brest"])


def test_looks_numeric_needs_more_than_one_value():
    assert not looks_numeric(["42"])
    assert not looks_numeric([])


# Rendu

def test_render_marks_immediate_actions():
    text = render([Suggestion("Résumer", preset_id="summarize"),
                   Suggestion("Corrige", prompt="Corrige.")])
    lines = text.split("\n")
    assert lines[0].startswith("1. ▸"), "une action directe est marquée ▸"
    assert lines[1].startswith("2. ·"), "un pré-remplissage est marqué ·"


def test_render_handles_emptiness():
    assert "Aucune suggestion" in render([])
