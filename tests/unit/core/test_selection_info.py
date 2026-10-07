"""Formateurs de l'indicateur de sélection — purs, donc testables sans LibreOffice."""

from src.mirai.core.selection_info import (
    calc_label,
    compact_whitespace,
    middle_ellipsis,
    writer_label,
)
from src.mirai.core.tools.calc_tools import col_letter


def test_compact_whitespace():
    assert compact_whitespace("  le   préfet\n\narrête\tque ") == "le préfet arrête que"
    assert compact_whitespace("") == ""
    assert compact_whitespace(None) == ""


def test_short_text_is_untouched():
    assert middle_ellipsis("texte court") == "texte court"


def test_long_text_keeps_head_and_tail():
    text = "Le préfet arrête que " + "x" * 200 + " fin du texte"
    result = middle_ellipsis(text, limit=40)

    assert len(result) <= 41           # ellipse comprise
    assert result.startswith("Le")
    assert result.endswith("texte")
    assert "…" in result


def test_ellipsis_prefers_word_boundaries():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    result = middle_ellipsis(text, limit=30)
    head, tail = result.split("…")
    assert not head.endswith(" ")
    assert not tail.startswith(" ")


# Writer

def test_writer_shows_the_selection():
    assert writer_label("Le préfet arrête") == "Sélection : « Le préfet arrête »"


def test_writer_without_selection_announces_the_whole_document():
    """Sans sélection, l'orchestrateur annonce « DOCUMENT ENTIER » au modèle.

    Le libellé citait le paragraphe sous le curseur — donc le TITRE, curseur en
    tête — ce qui laissait croire à une action limitée à cette ligne. Il nomme
    désormais la portée large, et mentionne que les presets, eux, restent sur
    le paragraphe courant.
    """
    label = writer_label("", "Vu le code général des collectivités")
    assert label.startswith("Document entier")
    assert "paragraphe courant" in label
    assert "collectivités" not in label      # plus d'extrait qui n'engage rien


def test_writer_empty_document_invites_the_user():
    label = writer_label("", "")
    assert "curseur" in label.lower()


def test_writer_ignores_whitespace_only_selection():
    assert writer_label("   \n\t ", "paragraphe").startswith("Document entier")


# Calc

def test_column_letters():
    assert col_letter(0) == "A"
    assert col_letter(25) == "Z"
    assert col_letter(26) == "AA"
    assert col_letter(27) == "AB"


def test_calc_range_label_matches_historic_wording():
    assert calc_label(0, 3, 0, 7) == "5 cellules sélectionnées (A4:A8)"


def test_calc_single_cell():
    assert calc_label(2, 0, 2, 0) == "Cellule sélectionnée (C1)"


def test_calc_rectangular_range_counts_cells():
    assert calc_label(0, 0, 2, 3) == "12 cellules sélectionnées (A1:C4)"


def test_calc_accepts_inverted_coordinates():
    """Une sélection tirée vers le haut/la gauche donne le même libellé."""
    assert calc_label(2, 7, 0, 3) == calc_label(0, 3, 2, 7)
