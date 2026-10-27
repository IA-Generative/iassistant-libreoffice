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

    for style in ("Heading 1", "heading 2", "Titre 1", "Title", "Subtitle", "Überschrift 1"):
        assert is_heading(style), style
    for style in ("Standard", "Text Body", "Corps de texte", "", None):
        assert not is_heading(style), style


def test_prompt_gives_the_heading_as_context_only():
    prompt = build_rewrite_prompt(["Corps."], "réécris",
                                  headings=["Mon titre"])

    assert "Mon titre" in prompt
    assert "NE PAS reprendre" in prompt
    assert "Ne reprends pas le titre" in prompt


# Découpage en sections : un titre et son corps, jusqu'au titre suivant

def test_each_heading_starts_a_section():
    from src.mirai.core.doc_rewrite import Section, sections

    texts = ["Intro.", "Titre A", "Corps A.", "Titre B", "Corps B1.", "Corps B2."]
    styles = ["Standard", "Heading 1", "Standard", "Heading 2", "Standard", "Standard"]

    assert sections(texts, styles) == [Section(None, (0,)), Section(1, (2,)),
                                       Section(3, (4, 5))]


def test_blank_paragraphs_around_a_section_body_are_left_out():
    """Ils espacent un titre de son texte : la réécriture ne les supprime pas."""
    from src.mirai.core.doc_rewrite import Section, sections

    texts = ["Titre", "", "Un.", "", "Deux.", "", "Autre titre"]
    styles = ["Heading 1", "Standard", "Standard", "Standard", "Standard",
              "Standard", "Heading 1"]

    assert sections(texts, styles) == [Section(0, (2, 3, 4))]


def test_a_heading_without_text_below_has_no_section():
    from src.mirai.core.doc_rewrite import sections

    assert sections(["Titre", "", "Sous-titre"],
                    ["Heading 1", "Standard", "Heading 2"]) == []


def test_a_long_section_is_cut_between_paragraphs():
    from src.mirai.core.doc_rewrite import chunks

    texts = ["a" * 40, "b" * 40, "c" * 40]

    assert chunks(texts, (0, 1, 2), 100) == [(0, 1), (2,)]


def test_a_paragraph_longer_than_the_limit_is_sent_alone():
    from src.mirai.core.doc_rewrite import chunks

    texts = ["a" * 10, "b" * 300, "c" * 10]

    assert chunks(texts, (0, 1, 2), 100) == [(0,), (1,), (2,)]


# Tout le corps d'un seul appel : le modèle rend la structure, le style vient
# du document

def test_the_whole_document_prompt_marks_interior_headings():
    from src.mirai.core.doc_rewrite import build_document_prompt

    prompt = build_document_prompt(["Le contexte.", "Analyse", "Les chiffres."],
                                   ["Standard", "Heading 2", "Standard"], "x")

    assert "[P2] # Analyse" in prompt
    assert "précédé de « # »" in prompt


def test_without_interior_heading_the_prompt_does_not_mention_them():
    from src.mirai.core.doc_rewrite import build_document_prompt

    assert "« # »" not in build_document_prompt(["Un.", "Deux."], ["Standard"] * 2, "x")


def test_a_kept_heading_gets_its_heading_style_back():
    from src.mirai.core.doc_rewrite import styled_lines

    lines = styled_lines("LE CONTEXTE.\n# Analyse\nLES CHIFFRES.",
                         ["Le contexte.", "Analyse", "Les chiffres."],
                         ["Text Body", "Heading 2", "Text Body"])

    assert lines == [("LE CONTEXTE.", "Text Body"), ("Analyse", "Heading 2"),
                     ("LES CHIFFRES.", "Text Body")]


def test_a_heading_whose_spelling_the_model_fixed_keeps_its_level():
    from src.mirai.core.doc_rewrite import styled_lines

    lines = styled_lines("Intro.\n# Partie 1\nCorps 1.\n# Détails\nCorps 2.",
                         ["Intro.", "Partie 1", "Corps 1.", "Détials", "Corps 2."],
                         ["Standard", "Heading 1", "Standard", "Heading 2", "Standard"])

    assert [style for _, style in lines] == [
        "Standard", "Heading 1", "Standard", "Heading 2", "Standard"]


def test_a_heading_returned_without_its_marker_stays_a_heading():
    from src.mirai.core.doc_rewrite import styled_lines

    lines = styled_lines("LE CONTEXTE.\nAnalyse\nLES CHIFFRES.",
                         ["Le contexte.", "Analyse", "Les chiffres."],
                         ["Text Body", "Heading 2", "Text Body"])

    assert lines == [("LE CONTEXTE.", "Text Body"), ("Analyse", "Heading 2"),
                     ("LES CHIFFRES.", "Text Body")]


def test_body_text_never_takes_a_heading_style():
    """« En deux paragraphes » : le titre intérieur disparaît, le corps reste du corps."""
    from src.mirai.core.doc_rewrite import styled_lines

    lines = styled_lines("Premier bloc.\nSecond bloc.",
                         ["Le contexte.", "Analyse", "Les chiffres."],
                         ["Standard", "Heading 2", "Standard"])

    assert lines == [("Premier bloc.", "Standard"), ("Second bloc.", "Standard")]


def test_a_line_starting_with_a_hash_is_body_text_when_there_was_no_heading():
    from src.mirai.core.doc_rewrite import styled_lines

    assert styled_lines("#1 des ventes.", ["Premier des ventes."], ["Standard"]) == [
        ("#1 des ventes.", "Standard")]


def test_a_hashtag_under_an_interior_heading_stays_body_text():
    from src.mirai.core.doc_rewrite import styled_lines

    lines = styled_lines("Le contexte.\n# Réseaux\n#JO2024 a généré deux millions de messages.",
                         ["Le contexte.", "Réseaux", "#JO2024 a généré deux millions de messages."],
                         ["Standard", "Heading 2", "Standard"])

    assert lines == [("Le contexte.", "Standard"), ("Réseaux", "Heading 2"),
                     ("#JO2024 a généré deux millions de messages.", "Standard")]
