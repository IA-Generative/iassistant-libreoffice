"""Analyse du document : fabrication de la demande, lecture de la réponse.

Le module ne fait ni réseau ni UNO — ces tests s'exécutent donc sans
LibreOffice ni relais, et couvrent ce qui casse en vrai : un modèle qui ne
respecte pas la consigne de format.
"""

from src.mirai.core import doc_analysis
from src.mirai.i18n import t as _t


def _texte(n):
    return "Un paragraphe de démonstration. " * n


# ── Fabrication de la demande ────────────────────────────────────────────────

def test_short_document_is_not_sent():
    """Rien à structurer, et l'appel coûterait un aller-retour pour rien."""
    assert doc_analysis.build_messages("Trois mots ici.") is None
    assert doc_analysis.build_messages("") is None
    assert doc_analysis.build_messages(None) is None


def test_long_enough_document_is_sent():
    messages = doc_analysis.build_messages(_texte(20))
    assert messages is not None
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_long_document_is_truncated():
    messages = doc_analysis.build_messages("x" * 50_000)
    assert len(messages[1]["content"]) <= doc_analysis.MAX_CHARS


def test_truncation_falls_on_a_line_boundary():
    """Un paragraphe coupé en plein milieu se lit comme une faute, et le
    modèle la signale — on perdrait une proposition sur cinq à la signaler."""
    corps = "\n".join(f"Ligne numéro {i} du document de test." for i in range(500))
    messages = doc_analysis.build_messages(corps)
    envoye = messages[1]["content"]
    assert envoye.endswith(".")
    assert "\n" in envoye


def test_system_prompt_states_the_item_cap():
    assert str(doc_analysis.MAX_ITEMS) in doc_analysis.SYSTEM_PROMPT


# ── Lecture de la réponse ────────────────────────────────────────────────────

def test_parses_dashed_list():
    raw = "- Ajouter des intertitres\n- Scinder le paragraphe 4\n"
    assert doc_analysis.parse(raw) == ["Ajouter des intertitres",
                                       "Scinder le paragraphe 4"]


def test_parses_various_bullets():
    raw = "• Premier point\n* Deuxième point\n– Troisième point"
    assert len(doc_analysis.parse(raw)) == 3


def test_parses_numbered_list():
    raw = "1. Ajouter un titre\n2) Raccourcir l'introduction"
    assert doc_analysis.parse(raw) == ["Ajouter un titre",
                                       "Raccourcir l'introduction"]


def test_ignores_preamble_and_conclusion():
    """Les modèles ajoutent une phrase de politesse malgré la consigne."""
    raw = ("Voici mes propositions :\n"
           "- Ajouter des intertitres\n"
           "- Scinder le paragraphe 4\n"
           "N'hésitez pas si vous voulez que je détaille.")
    assert doc_analysis.parse(raw) == ["Ajouter des intertitres",
                                       "Scinder le paragraphe 4"]


def test_deduplicates():
    raw = "- Ajouter un titre\n- Ajouter un titre\n- Alléger la conclusion"
    assert doc_analysis.parse(raw) == ["Ajouter un titre", "Alléger la conclusion"]


def test_caps_the_number_of_items():
    raw = "\n".join(f"- Proposition {i}" for i in range(20))
    assert len(doc_analysis.parse(raw)) == doc_analysis.MAX_ITEMS


def test_parse_survives_empty_and_none():
    assert doc_analysis.parse("") == []
    assert doc_analysis.parse(None) == []


def test_prose_without_any_list_yields_nothing():
    """Mieux vaut rien afficher que de faire passer un paragraphe pour une
    liste de propositions — l'appelant garde alors les suggestions statiques."""
    assert doc_analysis.parse("Le document me paraît clair et bien construit.") == []


# ── Rendu ────────────────────────────────────────────────────────────────────

def test_render_lists_the_items():
    texte = doc_analysis.render(["Ajouter des intertitres", "Scinder le §4"])
    assert "Ajouter des intertitres" in texte
    assert "Scinder le §4" in texte


def test_render_without_items_says_so():
    assert doc_analysis.render([]) == _t("analysis.unavailable")


# ── Réponse tronquée (constaté en recette avec gemma-4) ──────────────────────

def test_truncated_answer_drops_the_last_item():
    """Le flux s'arrête sur `length` : la dernière ligne est coupée en plein mot.

    Recette du 2026-08-04 : l'onglet affichait « Fusionner les sections en une
    seule chron ». Une proposition tronquée ne vaut pas mieux qu'aucune.
    """
    raw = ("- Supprimer les redondances de l'introduction\n"
           "- Fusionner les sections Historique et Version initiale en une seule chron")
    items = doc_analysis.parse(raw, truncated=True)
    assert items == ["Supprimer les redondances de l'introduction"]


def test_truncation_keeps_a_lone_item():
    """Tout jeter laisserait l'onglet vide, donc les suggestions statiques —
    alors qu'une proposition complète est peut-être là."""
    raw = "- Ajouter des intertitres"
    assert doc_analysis.parse(raw, truncated=True) == ["Ajouter des intertitres"]


def test_complete_answer_keeps_everything():
    raw = "- Première proposition\n- Seconde proposition"
    assert len(doc_analysis.parse(raw, truncated=False)) == 2
