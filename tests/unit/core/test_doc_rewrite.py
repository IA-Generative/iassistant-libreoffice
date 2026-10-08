"""Réécriture du document : détection d'intention et lecture de la réponse.

Ce chemin existe parce que le tool calling n'a pas suffi : sur un modèle de
taille moyenne, « réécris l'article en deux paragraphes » sans sélection donnait
`iterations=2` — lecture du document, puis réponse en texte, document intact.
Ici Python pilote et applique ; le modèle n'est qu'une fonction texte.
"""

from src.mirai.core.doc_rewrite import (
    build_rewrite_prompt,
    parse_rewritten,
    wants_document_rewrite,
)

# Détection d'intention
#
# Le principe : sans sélection, tout ce qui n'est pas une QUESTION est un ordre
# portant sur le document. Énumérer les verbes de modification est sans fin —
# « réduis », « reformate », « aère », « convertis »… — et chaque oubli redonne
# une action sans effet.

def test_detects_the_cases_that_failed():
    """Deux prompts réels qui n'avaient rien produit, faute de verbe connu."""
    assert wants_document_rewrite(
        "reduit à 2 paragraphes. reformate en poème en alexandrin.")
    assert wants_document_rewrite("Réécris l'article en deux paragraphes")


def test_detects_orders_whatever_the_verb():
    for prompt in ("Reformule tout le texte",
                   "aère la mise en page",
                   "convertis en liste à puces",
                   "mets tout au passé simple",
                   "supprime les répétitions",
                   "Corrige les fautes du document",
                   "Traduis ce texte en anglais",
                   "Rends le ton plus formel"):
        assert wants_document_rewrite(prompt), prompt


def test_ignores_questions():
    """Une question appelle une réponse, pas une modification du document."""
    for prompt in ("Que dit ce document ?",
                   "Quel est le sujet du texte ?",
                   "Pourquoi ce passage est-il ambigu ?",
                   "Comment améliorer ce texte ?",
                   "Combien de paragraphes ?",
                   "Explique-moi la structure",
                   "Dis-moi ce qui cloche",
                   "Est-ce que le ton est adapté ?"):
        assert not wants_document_rewrite(prompt), prompt


def test_polite_orders_are_still_orders():
    """« Peux-tu restructurer… ? » finit par « ? » mais reste un ordre.

    Seule l'OUVERTURE distingue une question d'un ordre poli — se fier au point
    d'interrogation ferait retomber dans l'action sans effet.
    """
    assert wants_document_rewrite("peux-tu restructurer le document ?")
    assert wants_document_rewrite("pourrais-tu raccourcir tout ça ?")


def test_ignores_too_short_prompts():
    """Un mot isolé n'est pas un ordre de réécriture."""
    assert not wants_document_rewrite("bonjour")
    assert not wants_document_rewrite("merci !")
    assert not wants_document_rewrite("")
    assert not wants_document_rewrite(None)


def test_is_question_is_exposed():
    from src.mirai.core.doc_rewrite import is_question

    assert is_question("Pourquoi ce texte est-il long ?")
    assert not is_question("Raccourcis ce texte")


# Construction de la demande

def test_prompt_numbers_the_paragraphs():
    prompt = build_rewrite_prompt(["Titre", "Corps."], "réécris en un bloc")

    assert "[P1] Titre" in prompt
    assert "[P2] Corps." in prompt
    assert "réécris en un bloc" in prompt


def test_prompt_forbids_commentary():
    prompt = build_rewrite_prompt(["A"], "x")
    assert "UNIQUEMENT" in prompt
    assert "sans les marqueurs" in prompt


# Lecture de la réponse

def test_parses_one_paragraph_per_line():
    assert parse_rewritten("Premier bloc.\nSecond bloc.") == [
        "Premier bloc.", "Second bloc."]


def test_strips_markers_the_model_kept():
    assert parse_rewritten("[P1] Un.\n[P2] Deux.") == ["Un.", "Deux."]


def test_strips_bullets_and_code_fences():
    text = "```\n- Un.\n* Deux.\n• Trois.\n```"
    assert parse_rewritten(text) == ["Un.", "Deux.", "Trois."]


def test_ignores_blank_lines():
    assert parse_rewritten("\n\nUn.\n\n\nDeux.\n\n") == ["Un.", "Deux."]


def test_empty_response_yields_nothing():
    """Rien d'exploitable ⇒ le document ne doit pas être touché."""
    assert parse_rewritten("") == []
    assert parse_rewritten("   \n\n  ") == []
    assert parse_rewritten(None) == []


# Préservation des titres

def test_heading_styles_are_recognised():
    from src.mirai.core.doc_rewrite import is_heading

    for style in ("Heading 1", "heading 2", "Titre 1", "Title", "Überschrift 1"):
        assert is_heading(style), style
    for style in ("Standard", "Text Body", "Corps de texte", "", None):
        assert not is_heading(style), style


def test_body_range_excludes_a_leading_heading():
    """Le défaut observé : le corps réécrit héritait du style Titre."""
    from src.mirai.core.doc_rewrite import body_range

    assert body_range(["Heading 1", "Standard", "Standard"]) == (2, 3)


def test_body_range_excludes_headings_at_both_ends():
    from src.mirai.core.doc_rewrite import body_range

    assert body_range(["Title", "Standard", "Standard", "Heading 2"]) == (2, 3)


def test_body_range_covers_everything_without_headings():
    from src.mirai.core.doc_rewrite import body_range

    assert body_range(["Standard", "Standard"]) == (1, 2)


def test_body_range_is_none_when_only_headings():
    from src.mirai.core.doc_rewrite import body_range

    assert body_range(["Heading 1", "Title"]) is None


def test_prompt_gives_the_heading_as_context_only():
    prompt = build_rewrite_prompt(["Corps."], "réécris",
                                  headings=["Mon titre"])

    assert "Mon titre" in prompt
    assert "NE PAS reprendre" in prompt
    assert "Ne reprends pas le titre" in prompt
