"""Corriger et Traduire, rejoués par le moteur (vrai LLMClient, SSE scripté,
faux document) : ce qui est écrit, où, avec quelle mise en forme, et ce que
la palette en dit."""

import pytest

from src.mirai import i18n
from src.mirai.core import presets, prompts
from src.mirai.core.context import ToolContext
from src.mirai.i18n import t as _t
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks
from tests.stubs.fake_writer_body import (
    BREAK,
    TABLE,
    WINDOWS_BREAK,
    Anchored,
    FakeBodyDoc,
)

BOLD = "gras"
ITALIC = "italique"


def _ctx(doc, shell):
    return ToolContext(doc, doc.controller, "writer", shell)


def _reply(text):
    return FakeShell(responses=[FakeSSEResponse(text_chunks(text))])


def test_correct_replaces_only_the_wrong_word_of_the_selection():
    """Le mot en gras, que la correction ne touche pas, reste en gras."""
    doc = FakeBodyDoc(["Avant."], selection=[("Le ", None), ("délai", BOLD),
                                             (" est fixer.", None)])
    shell = _reply("Le délai est fixé.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.runs() == [("Le ", None), ("délai", BOLD), (" est fixé.", None)]
    assert doc.paragraphs == ["Avant."]
    assert doc.undo.entered == [_t("preset.undo_correct")]
    assert doc.writes_outside_undo == 0
    assert message == _t("preset.done_correct_replaced")


@pytest.mark.parametrize("anchored", [Anchored("TextField", "12"), Anchored("Annotation")],
                         ids=["field", "comment"])
def test_correct_writes_nothing_when_an_object_shifts_the_positions(anchored):
    """Un champ qui affiche « 12 », un commentaire qui ne se lit pas : chacun
    n'occupe qu'une position. Les corrections tomberaient à côté, et récrire
    le passage d'un coup effacerait l'objet."""
    doc = FakeBodyDoc(["Avant."], selection=[("Objet", None), anchored,
                                             (" : Madame je vous informe.", None)])
    original = doc.selection.getString()
    shell = _reply(original.replace("Madame", "Madame,"))

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == original
    assert doc.selection.anchored() == [anchored.kind]
    assert doc.writes == 0
    assert message == _t("preset.objects_kept")


def test_correct_keeps_a_footnote_glued_to_the_word_it_fixes():
    """La note se lit comme son numéro, collé au mot : « delai1 »."""
    doc = FakeBodyDoc(["Avant."], selection=[("Le delai", None), Anchored("Footnote", "1"),
                                             (" est fixé.", None)])
    shell = _reply("Le délai1 est fixé.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == "Le délai1 est fixé."
    assert doc.selection.anchored() == ["Footnote"]
    assert message == _t("preset.done_correct_replaced")


def test_translate_keeps_a_footnote_whose_number_ends_the_translated_line():
    doc = FakeBodyDoc(["Avant."], selection=[("Le délai est fixé par décret.", None),
                                             Anchored("Footnote", "1")])
    shell = _reply("The deadline is set by decree.1")

    presets.run_translate(_ctx(doc, shell), shell, "anglais", None)

    assert doc.selection.getString() == "The deadline is set by decree.1"
    assert doc.selection.anchored() == ["Footnote"]


def test_translate_writes_nothing_when_a_line_would_lose_its_footnote():
    doc = FakeBodyDoc(["Avant."], selection=[("Le délai est fixé", None),
                                             Anchored("Footnote", "1"), (" par décret.", None)])
    shell = _reply("The deadline is set by decree.")

    message = presets.run_translate(_ctx(doc, shell), shell, "anglais", None)

    assert doc.selection.getString() == "Le délai est fixé1 par décret."
    assert doc.selection.anchored() == ["Footnote"]
    assert message == _t("preset.objects_kept")


def test_correct_reaches_a_mistake_beyond_what_one_cursor_move_covers():
    """Un curseur Writer avance d'au plus 32 767 caractères à la fois."""
    start = "".join(f"Ligne {number}.\n" for number in range(4000))
    doc = FakeBodyDoc(["Avant."], selection=[(start, BOLD), ("Fin fixer.", None)])
    shell = _reply(start + "Fin fixée.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.runs() == [(start, BOLD), ("Fin fixée.", None)]
    assert doc.writes == 1


def test_correct_does_not_walk_the_selection_again_for_each_fixed_word():
    """L'écriture se fait sur le thread principal, qui fige LibreOffice : elle
    ne repart pas du début de la sélection pour chaque mot corrigé."""
    text = "\n".join(f"Ligne {number} fixer." for number in range(300))
    doc = FakeBodyDoc(["Avant."], selection=text)
    shell = _reply(text.replace("fixer", "fixée"))

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == text.replace("fixer", "fixée")
    assert doc.moved <= 4 * len(text)


def test_correct_keeps_the_non_breaking_spaces_the_reply_turns_into_spaces():
    """L'autocorrection française met une espace insécable avant « : » ; le
    modèle peut rendre une espace simple."""
    doc = FakeBodyDoc(["Avant."],
                      selection="Le délai est fixer\u00a0: demain, soit 10\u202f000 euros.")
    shell = _reply("Le délai est fixé : demain, soit 10 000 euros.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == "Le délai est fixé\u00a0: demain, soit 10\u202f000 euros."


def test_correct_writes_nothing_when_the_reply_is_cut_short():
    """Une réponse arrêtée par le plafond de jetons : la fin manquante
    supprimerait la fin de la sélection."""
    doc = FakeBodyDoc(["Avant."], selection="Le délai est fixer. Les pièces sont jointes.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Le délai est fixé. Les pièces", finish="length"))])

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == "Le délai est fixer. Les pièces sont jointes."
    assert doc.writes == 0
    assert message == _t("run.err_truncated")


@pytest.mark.parametrize("append_mode", [True, False], ids=["append", "replace"])
def test_correct_writes_nothing_when_the_selection_has_no_mistake(append_mode):
    """Une réponse identique au texte, aux espaces près, ne laisse rien à
    annuler."""
    doc = FakeBodyDoc(["Avant."], selection="Le délai est fixé.")
    shell = _reply("Le délai  est fixé.\n")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None,
                                  append_mode=append_mode)

    assert doc.selection.getString() == "Le délai est fixé."
    assert doc.writes == 0
    assert doc.undo.entered == []
    assert message == _t("preset.no_error")


# Sans sélection : chaque paragraphe non vide, titres compris, est envoyé sur
# sa propre ligne et récrit à sa place.

def test_correct_without_selection_corrects_every_paragraph_in_place():
    doc = FakeBodyDoc(["Raport", [("Le ", None), ("délai", BOLD), (" est fixer.", None)],
                       "", [("Tout va bien.", ITALIC)]],
                      ["Heading 1", "Standard", "Standard", "Standard"])
    shell = _reply("Rapport\nLe délai est fixé.\nTout va bien.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Rapport", "Le délai est fixé.", "", "Tout va bien."]
    assert doc.items[1].runs() == [("Le ", None), ("délai", BOLD), (" est fixé.", None)]
    assert doc.items[3].runs() == [("Tout va bien.", ITALIC)]
    assert doc.styles == ["Heading 1", "Standard", "Standard", "Standard"]
    assert doc.undo.entered == [_t("preset.undo_correct")]
    assert doc.writes_outside_undo == 0
    assert message == _t("preset.done_correct_document_replaced")


@pytest.mark.parametrize("append_mode", [True, False], ids=["append", "replace"])
def test_correct_without_selection_writes_nothing_when_no_paragraph_has_a_mistake(append_mode):
    doc = FakeBodyDoc(["Rapport", "Tout va bien."], ["Heading 1", "Standard"])
    shell = _reply("Rapport\nTout va  bien.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None, append_mode=append_mode)

    assert doc.writes == 0
    assert message == _t("preset.no_error")


def test_correct_without_selection_in_append_mode_adds_the_corrected_text_after_it():
    """Le document reste tel quel ; le texte corrigé le suit en entier, chaque
    paragraphe dans le style du sien, pour comparer avant de choisir."""
    doc = FakeBodyDoc(["Raport", "Tout va bien.", "Le délai est fixer."],
                      ["Heading 1", "Standard", "Standard"])
    shell = _reply("Rapport\nTout va bien.\nLe délai est fixé.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None, append_mode=True)

    assert doc.paragraphs == ["Raport", "Tout va bien.", "Le délai est fixer.",
                              "---début-du-texte-corrigé---", "Rapport", "Tout va bien.",
                              "Le délai est fixé.", "---fin-du-texte-corrigé---"]
    assert doc.styles[3:] == ["Standard", "Heading 1", "Standard", "Standard", "Standard"]
    assert doc.undo.entered == [_t("preset.undo_correct")]
    assert message == _t("preset.done_correct_document")


@pytest.mark.parametrize("reply", ["Rapport\nLe délai est fixé.", "Rapport\nLe délai\nest fixé.\nFin."],
                         ids=["fewer", "more"])
def test_correct_without_selection_writes_nothing_when_the_reply_has_another_line_count(reply):
    """Les lignes ne sauraient plus à quel paragraphe revenir."""
    doc = FakeBodyDoc(["Raport", "Le délai est fixer.", "Fin."], ["Heading 1", "Standard", "Standard"])
    shell = _reply(reply)

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Raport", "Le délai est fixer.", "Fin."]
    assert doc.writes == 0
    assert message == _t("run.err_paragraphs")


def test_correct_without_selection_keeps_the_indentation_of_each_line():
    """Un bloc d'adresse aligné par tabulations reste aligné."""
    doc = FakeBodyDoc(["\t\t\tMonsieur Dupont\n\t\t\t75000 Pari", "Fin."])
    shell = _reply("Monsieur Dupont\n75000 Paris\nFin.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["\t\t\tMonsieur Dupont\n\t\t\t75000 Paris", "Fin."]


def test_correct_without_selection_leaves_a_paragraph_with_a_comment_as_is_and_says_so():
    doc = FakeBodyDoc([[("Objet", None), Anchored("Annotation"), (" : le délai est fixer.", None)],
                       "Les pièces sont jointe."])
    shell = _reply("Objet : le délai est fixé.\nLes pièces sont jointes.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.items[0].getString() == "Objet : le délai est fixer."
    assert doc.items[0].anchored() == ["Annotation"]
    assert doc.items[1].getString() == "Les pièces sont jointes."
    assert message == (_t("preset.done_correct_document_replaced") + " "
                       + _t("preset.paragraphs_kept", count=1))


def test_correct_without_selection_writes_nothing_when_each_fix_would_damage_an_object():
    doc = FakeBodyDoc([[("Objet", None), Anchored("Annotation"), (" : le délai est fixer.", None)]])
    shell = _reply("Objet : le délai est fixé.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.writes == 0
    assert message == _t("preset.paragraphs_kept", count=1)


def test_correct_without_selection_leaves_tables_aside_and_says_so():
    doc = FakeBodyDoc(["Raport", TABLE, "Le délai est fixer."], ["Heading 1", "", "Standard"])
    shell = _reply("Rapport\nLe délai est fixé.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Rapport", "Le délai est fixé."]
    assert doc.tables == 1
    assert message == (_t("preset.done_correct_document_replaced") + " "
                       + _t("palette.tables_left", count=1))


@pytest.mark.parametrize("selection", ["Le délai est fixer.", ""], ids=["selection", "document"])
def test_correct_is_counted_under_its_own_action_in_telemetry(selection):
    doc = FakeBodyDoc(["Le délai est fixer."], selection=selection)
    shell = _reply("Le délai est fixé.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    [(span, attrs)] = shell.telemetry_events
    assert (span, attrs["plugin.action"], attrs["action"]) == (
        "CorrectSelection", "correct", "correct_selection")


# Traduire : vers la langue tapée dans le champ, sinon vers celle de
# l'interface ; un texte déjà dans cette langue n'est pas écrit.

@pytest.fixture
def interface_language():
    previous = i18n.get_locale()
    yield i18n.set_locale
    i18n.set_locale(previous)


@pytest.mark.parametrize("locale,language", [("fr", "français"), ("en", "anglais"),
                                             ("zh", "chinois")])
def test_translate_without_a_typed_language_targets_the_interface_language(
        interface_language, locale, language):
    interface_language(locale)
    doc = FakeBodyDoc(["Avant."], selection="Das ist gut.")
    shell = _reply("Traduit.")

    presets.run_translate(_ctx(doc, shell), shell, "", None)

    assert f"Langue cible : {language}." in shell.requests[0]["messages"][-1]["content"]


def test_translate_targets_the_language_typed_in_the_prompt():
    doc = FakeBodyDoc(["Avant."], selection="Le délai est fixé.")
    shell = _reply("Die Frist ist festgelegt.")

    message = presets.run_translate(_ctx(doc, shell), shell, "allemand", None)

    assert "Langue cible : allemand." in shell.requests[0]["messages"][-1]["content"]
    assert doc.selection.getString() == "Die Frist ist festgelegt."
    assert doc.undo.entered == [_t("preset.undo_translate")]
    assert message == _t("preset.done_translate_replaced")


def test_translate_reads_a_typed_phrase_as_the_target_language():
    """« en anglais » tapé dans le champ ne donne pas « traduis en en anglais »."""
    doc = FakeBodyDoc(["Avant."], selection="Le délai est fixé.")
    shell = _reply("The deadline is set.")

    presets.run_translate(_ctx(doc, shell), shell, "en anglais", None)

    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "Langue cible : en anglais." in prompt
    assert "en en " not in prompt


def test_translate_never_asks_the_model_to_keep_the_language():
    """La règle des autres actions, « réponds dans la même langue », ferait
    rendre le texte tel quel."""
    doc = FakeBodyDoc(["Avant."], selection="The deadline is set.")
    shell = _reply("Le délai est fixé.")

    presets.run_translate(_ctx(doc, shell), shell, "", None)

    system = shell.requests[0]["messages"][0]["content"]
    assert prompts.SAME_LANGUAGE_RULE not in system
    assert prompts.DOCUMENT_DATA_RULE in system


@pytest.mark.parametrize("append_mode", [True, False], ids=["append", "replace"])
def test_translate_writes_nothing_and_asks_for_a_language_when_already_translated(append_mode):
    doc = FakeBodyDoc(["Avant."], selection="Le délai est fixé.")
    shell = _reply("Le délai est fixé.")

    message = presets.run_translate(_ctx(doc, shell), shell, "", None, append_mode=append_mode)

    assert doc.writes == 0
    assert message == _t("preset.already_in_language")


def test_translate_takes_a_question_in_the_text_for_a_translation():
    """« Could you clarify » traduit « Pouvez-vous préciser » : c'est le texte,
    pas une question du modèle."""
    doc = FakeBodyDoc(["Avant."], selection="Pouvez-vous préciser le délai ?")
    shell = _reply("Could you clarify the deadline?")

    message = presets.run_translate(_ctx(doc, shell), shell, "anglais", None)

    assert doc.selection.getString() == "Could you clarify the deadline?"
    assert message == _t("preset.done_translate_replaced")


def test_translate_without_selection_translates_headings_and_paragraphs_in_place():
    doc = FakeBodyDoc(["Report", "", "The deadline is set."], ["Heading 1", "Standard", "Standard"])
    shell = _reply("Rapport\nLe délai est fixé.")

    message = presets.run_translate(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Rapport", "", "Le délai est fixé."]
    assert doc.styles == ["Heading 1", "Standard", "Standard"]
    assert doc.undo.entered == [_t("preset.undo_translate")]
    assert message == _t("preset.done_translate_document_replaced")


def test_translate_without_selection_keeps_the_paragraphs_already_translated():
    doc = FakeBodyDoc([[("Rapport", BOLD)], "The deadline is set."], ["Heading 1", "Standard"])
    shell = _reply("Rapport\nLe délai est fixé.")

    presets.run_translate(_ctx(doc, shell), shell, "", None)

    assert doc.items[0].runs() == [("Rapport", BOLD)]
    assert doc.paragraphs == ["Rapport", "Le délai est fixé."]


def test_translate_without_selection_asks_for_a_language_when_all_is_translated():
    doc = FakeBodyDoc(["Rapport", "Le délai est fixé."], ["Heading 1", "Standard"])
    shell = _reply("Rapport\nLe délai est fixé.")

    message = presets.run_translate(_ctx(doc, shell), shell, "", None)

    assert doc.writes == 0
    assert message == _t("preset.already_in_language")


def test_translate_without_selection_in_append_mode_adds_the_translation_after_it():
    doc = FakeBodyDoc(["Report", "The deadline is set."], ["Heading 1", "Standard"])
    shell = _reply("Rapport\nLe délai est fixé.")

    message = presets.run_translate(_ctx(doc, shell), shell, "", None, append_mode=True)

    assert doc.paragraphs == ["Report", "The deadline is set.", "---début-de-la-traduction---",
                              "Rapport", "Le délai est fixé.", "---fin-de-la-traduction---"]
    assert doc.styles[2:] == ["Standard", "Heading 1", "Standard", "Standard"]
    assert message == _t("preset.done_translate_document")


@pytest.mark.parametrize("selection", ["The deadline.", ""], ids=["selection", "document"])
def test_translate_is_counted_under_its_own_action_in_telemetry(selection):
    doc = FakeBodyDoc(["The deadline."], selection=selection)
    shell = _reply("Le délai.")

    presets.run_translate(_ctx(doc, shell), shell, "", None)

    [(span, attrs)] = shell.telemetry_events
    assert (span, attrs["plugin.action"], attrs["action"]) == (
        "TranslateSelection", "translate", "translate_selection")


@pytest.mark.parametrize("runner", [presets.run_correct, presets.run_translate],
                         ids=["correct", "translate"])
def test_a_document_of_tables_only_is_reported_without_calling_the_model(runner):
    doc = FakeBodyDoc(["", TABLE, ""])
    shell = FakeShell()

    message = runner(_ctx(doc, shell), shell, "", None)

    assert message == _t("palette.tables_only")
    assert shell.requests == []


# Un saut de ligne manuel (Maj+Entrée) apparaît comme « \n » dans le texte
# d'un paragraphe, et une sélection sur plusieurs paragraphes les sépare
# aussi par « \n ».

@pytest.mark.parametrize("runner", [presets.run_correct, presets.run_translate],
                         ids=["correct", "translate"])
def test_a_paragraph_with_line_breaks_keeps_them_without_selection(runner):
    """Chaque ligne part à part, les lignes vides restent à leur place."""
    doc = FakeBodyDoc(["Adresse :\n\n75000 Pari", "Fin."])
    shell = _reply("Adresse :\n75000 Paris\nFin.")

    runner(_ctx(doc, shell), shell, "", None)

    assert "[P2] 75000 Pari\n" in shell.requests[0]["messages"][-1]["content"]
    assert doc.paragraphs == ["Adresse :\n\n75000 Paris", "Fin."]


def test_translate_writes_a_selection_of_several_paragraphs_line_by_line():
    """Writer fond en un seul paragraphe une plage récrite d'un coup : écrite
    ligne à ligne, chaque paragraphe garde sa place et sa mise en forme."""
    doc = FakeBodyDoc(["Avant."], selection=[("Premier.", None), BREAK, ("Titre", BOLD)])
    shell = _reply("First.\nTitle")

    presets.run_translate(_ctx(doc, shell), shell, "anglais", None)

    assert doc.selection.runs() == [("First.\n", None), ("Title", BOLD)]
    assert doc.selection.anchored() == ["ParagraphBreak"]


def test_correct_keeps_the_paragraphs_of_a_selection_read_with_windows_breaks():
    """Sous Windows, Writer sépare les paragraphes d'une plage lue par
    « \\r\\n », qui n'occupe qu'une position."""
    doc = FakeBodyDoc(["Avant."], selection=[("Titre", None), WINDOWS_BREAK, ("Le ", None),
                                             ("délai", BOLD), (" est fixer.", None)])
    shell = _reply("Titre\nLe délai est fixé.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.runs() == [("Titre\r\nLe ", None), ("délai", BOLD), (" est fixé.", None)]
    assert doc.selection.anchored() == ["ParagraphBreak"]


@pytest.mark.parametrize("selection", [[("Le délai est fixer.", None), BREAK],
                                       [("\tLe délai est fixer. ", None)]],
                         ids=["paragraph-break", "tab-and-space"])
def test_correct_keeps_the_blanks_at_the_edges_of_the_selection(selection):
    """Sélectionner un paragraphe jusqu'au début du suivant prend son saut de
    paragraphe ; le modèle rend un texte sans blancs de bord."""
    doc = FakeBodyDoc(["Avant."], selection=selection)
    original = doc.selection.getString()
    shell = _reply("Le délai est fixé.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == original.replace("fixer", "fixé")
    assert doc.selection.anchored() == [part.kind for part in selection
                                        if isinstance(part, Anchored)]


def test_correct_ignores_a_blank_line_added_by_the_reply():
    doc = FakeBodyDoc(["Avant."], selection=[("Titre", None), BREAK, ("Le ", None),
                                             ("délai", BOLD), (" est fixer.", None)])
    shell = _reply("Titre\n\nLe délai est fixé.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.runs() == [("Titre\nLe ", None), ("délai", BOLD), (" est fixé.", None)]
    assert doc.selection.anchored() == ["ParagraphBreak"]


def test_correct_writes_nothing_when_the_reply_joins_two_lines_of_the_selection():
    """Écrire une espace à la place du saut de paragraphe fondrait les deux
    paragraphes en un seul."""
    doc = FakeBodyDoc(["Avant."], selection=[("Le ", None), ("délai", BOLD), BREAK,
                                             ("est fixer.", None)])
    shell = _reply("Le délai est fixé.")

    message = presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == "Le délai\nest fixer."
    assert doc.selection.anchored() == ["ParagraphBreak"]
    assert doc.writes == 0
    assert message == _t("run.err_paragraphs")
