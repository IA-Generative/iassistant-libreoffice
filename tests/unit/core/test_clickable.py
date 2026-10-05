"""Lignes cliquables : décalage du curseur → contenu à rejouer.

Les onglets Conversation et Suggestions restent des zones de texte — une
`ListBox` ne renvoie pas à la ligne, et une réponse de l'assistant y serait
illisible. On traduit donc la position du curseur en ligne, puis en demande
nue, débarrassée de ce qui n'appartient qu'à l'affichage.
"""

import pytest

from src.mirai import i18n
from src.mirai.core import clickable, doc_analysis

SUGGESTIONS = ("1. ▸ Résumer la sélection\n"
               "2. · Reformuler en langage clair\n"
               "3. ▸ Raccourcir le paragraphe")

ANALYSE = ("Propositions d'amélioration du document :\n"
           "\n"
           "· Ajouter des intertitres aux sections longues\n"
           "· Supprimer le titre « Écosystème » qui ne contient aucun texte")

CONVERSATION = ("Vous : résume ce document en deux paragraphes\n"
                "MIrAI : Document réécrit : 45 → 2 paragraphes.")


def _offset_de_ligne(texte, numero):
    """Décalage du premier caractère d'une ligne — ce que rend un clic dessus."""
    return sum(len(ligne) + 1 for ligne in texte.split("\n")[:numero])


# ── Traduction décalage → ligne ──────────────────────────────────────────────

def test_offset_maps_to_the_right_line():
    for numero in range(3):
        offset = _offset_de_ligne(SUGGESTIONS, numero)
        assert clickable.line_at(SUGGESTIONS, offset) == numero


def test_offset_inside_a_line_stays_on_that_line():
    offset = _offset_de_ligne(SUGGESTIONS, 1) + 5
    assert clickable.line_at(SUGGESTIONS, offset) == 1


def test_line_at_tolerates_absurd_offsets():
    assert clickable.line_at(SUGGESTIONS, -1) == 0
    assert clickable.line_at(SUGGESTIONS, None) == 0
    assert clickable.line_at("", 42) == 0
    assert clickable.line_at(SUGGESTIONS, 10_000) <= SUGGESTIONS.count("\n")


# ── Nettoyage de la ligne ────────────────────────────────────────────────────

def test_numbering_and_bullet_are_stripped():
    assert clickable.clean("1. ▸ Résumer la sélection") == "Résumer la sélection"
    assert clickable.clean("2. · Reformuler") == "Reformuler"
    assert clickable.clean("· Ajouter des intertitres") == "Ajouter des intertitres"


def test_speaker_label_is_stripped():
    assert clickable.clean("Vous : résume ce document") == "résume ce document"
    assert clickable.clean("MIrAI : Document réécrit.") == "Document réécrit."


def test_section_title_is_not_replayable():
    """« Propositions d'amélioration du document : » n'est pas une demande."""
    assert clickable.clean("Propositions d'amélioration du document :") == ""


def test_blank_line_is_not_replayable():
    assert clickable.clean("") == ""
    assert clickable.clean("   ") == ""


def test_a_date_is_not_mistaken_for_numbering():
    """« 2026. » en tête ne doit pas manger le début de la ligne."""
    assert clickable.clean("2026. année de bascule") == "année de bascule"


# ── Contenu rendu au clic ────────────────────────────────────────────────────

def test_click_on_a_suggestion_returns_the_bare_request():
    offset = _offset_de_ligne(SUGGESTIONS, 1)
    assert clickable.payload_at(SUGGESTIONS, offset) == "Reformuler en langage clair"


def test_click_on_an_analysis_item_returns_it():
    offset = _offset_de_ligne(ANALYSE, 2)
    assert clickable.payload_at(ANALYSE, offset).startswith("Ajouter des intertitres")


def test_click_on_the_analysis_title_returns_nothing():
    assert clickable.payload_at(ANALYSE, 0) == ""


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_click_on_the_analysis_title_returns_nothing_in_every_language(code):
    i18n.set_locale(code)
    rendered = doc_analysis.render(["Ajouter des intertitres"])
    assert clickable.payload_at(rendered, 0) == ""


def test_click_on_a_blank_line_returns_nothing():
    offset = _offset_de_ligne(ANALYSE, 1)
    assert clickable.payload_at(ANALYSE, offset) == ""


def test_click_on_a_conversation_turn_returns_the_message():
    assert clickable.payload_at(CONVERSATION, 0) == "résume ce document en deux paragraphes"


def test_too_short_a_line_is_ignored():
    """Une ligne d'un ou deux caractères ne vaut pas la peine d'écraser la saisie."""
    assert clickable.payload_at("ok\nune vraie demande ici", 0) == ""


def test_payload_at_survives_empty_text():
    assert clickable.payload_at("", 0) == ""
    assert clickable.payload_at(None, 0) == ""
