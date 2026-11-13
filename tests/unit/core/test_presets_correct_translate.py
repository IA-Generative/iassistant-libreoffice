"""Corriger et Traduire, rejoués par le moteur (vrai LLMClient, SSE scripté,
faux document) : ce qui est écrit, où, avec quelle mise en forme, et ce que
la palette en dit."""

import pytest

from src.mirai import i18n
from src.mirai.core import presets, prompts
from src.mirai.core.context import ToolContext
from src.mirai.i18n import t as _t
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks
from tests.stubs.fake_writer_body import TABLE, FakeBodyDoc, FakeParagraph

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


FIELD = "￹"


class _FieldParagraph(FakeParagraph):
    """Passage qui contient un champ (numéro de page) : le texte lu montre sa
    valeur, le curseur le compte pour un seul caractère."""

    def getString(self):
        return self.text.replace(FIELD, "12")


def test_correct_writes_the_whole_text_when_a_field_shifts_the_positions():
    doc = FakeBodyDoc(["Avant."])
    doc.selection = _FieldParagraph(doc, "Voir page " + FIELD + ", le délai est fixer.",
                                    "Standard")
    shell = _reply("Voir page 12, le délai est fixé.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.getString() == "Voir page 12, le délai est fixé."
    assert doc.writes_outside_undo == 0


def test_correct_reaches_a_mistake_beyond_what_one_cursor_move_covers():
    """Un curseur Writer avance d'au plus 32 767 caractères à la fois."""
    start = "".join(f"Ligne {number}.\n" for number in range(4000))
    doc = FakeBodyDoc(["Avant."], selection=[(start, BOLD), ("Fin fixer.", None)])
    shell = _reply(start + "Fin fixée.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.runs() == [(start, BOLD), ("Fin fixée.", None)]
    assert doc.writes == 1


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

    assert f"en {language}" in shell.requests[0]["messages"][-1]["content"]


def test_translate_targets_the_language_typed_in_the_prompt():
    doc = FakeBodyDoc(["Avant."], selection="Le délai est fixé.")
    shell = _reply("Die Frist ist festgelegt.")

    message = presets.run_translate(_ctx(doc, shell), shell, "allemand", None)

    assert "en allemand" in shell.requests[0]["messages"][-1]["content"]
    assert doc.selection.getString() == "Die Frist ist festgelegt."
    assert doc.undo.entered == [_t("preset.undo_translate")]
    assert message == _t("preset.done_translate_replaced")


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
    doc = FakeBodyDoc(["Avant."], selection=[("Premier.\n", None), ("Titre", BOLD)])
    shell = _reply("First.\nTitle")

    presets.run_translate(_ctx(doc, shell), shell, "anglais", None)

    assert doc.selection.runs() == [("First.\n", None), ("Title", BOLD)]


def test_correct_still_writes_word_by_word_when_the_model_joins_two_lines():
    doc = FakeBodyDoc(["Avant."], selection=[("Le ", None), ("délai", BOLD),
                                             ("\nest fixer.", None)])
    shell = _reply("Le délai est fixé.")

    presets.run_correct(_ctx(doc, shell), shell, "", None)

    assert doc.selection.runs() == [("Le ", None), ("délai", BOLD), (" est fixé.", None)]
