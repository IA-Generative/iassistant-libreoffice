"""Golden iso-fonctionnels Writer : chaque fonction historique rejouée via le
moteur (vrai LLMClient + SSE scripté + faux document) — marqueurs, stop
phrases, retry sur question, undo unique. Assertions portées des
comportements de menu_actions/writer.py."""

import threading
from types import SimpleNamespace

import pytest

from src.mirai.core import presets
from src.mirai.core.context import ToolContext
from src.mirai.core.orchestrator import error_message
from src.mirai.i18n import t as _t
from tests.stubs.fake_docs import FakeWriterDoc
from tests.stubs.fake_shell import (
    FakeShell,
    FakeSSEResponse,
    ProbedSSEResponse,
    StoppedSSEResponse,
    text_chunks,
)
from tests.stubs.fake_writer_body import FakeBodyDoc

SUMMARY_HEADER = "\n\n---début-du-résumé---\n"
SUMMARY_FOOTER = "\n---fin-du-résumé---\n"
MODES = pytest.mark.parametrize("append_mode", [True, False], ids=["append", "replace"])


def _ctx(doc, shell):
    return ToolContext(doc, doc.controller, "writer", shell)


class _BrokenSSEResponse(FakeSSEResponse):
    """Flux coupé par le réseau juste après le premier chunk."""

    def __iter__(self):
        yield self._lines[0]
        raise ConnectionResetError("connexion interrompue")


def test_extend_inserts_between_legacy_markers():
    doc = FakeWriterDoc(selection_text="Il était une fois")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(" un roi", " généreux."))])
    message = presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "---début-du-texte-généré---" in doc.inserted
    assert " un roi généreux." in doc.inserted
    assert doc.inserted.endswith("\n---fin-du-texte-généré---\n")
    assert doc.undo.entered == ["Générer la suite"]
    assert doc.undo.left == 1
    assert "générée" in message.lower()
    # span legacy avec via=palette
    spans = dict(shell.telemetry_events)
    assert spans["ExtendSelection"]["via"] == "palette"


def test_extend_no_selection_no_paragraph_returns_hint():
    doc = FakeWriterDoc(selection_text="", current_paragraph="")
    shell = FakeShell()
    message = presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "curseur" in message
    assert doc.inserted == ""


def test_extend_targets_current_paragraph_without_selection():
    # Action directe : pas de sélection → le paragraphe sous le curseur est
    # identifié et utilisé comme cible, sans être sélectionné.
    doc = FakeWriterDoc(selection_text="",
                        current_paragraph="Le paragraphe en cours de rédaction")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(" continue ici."))])
    message = presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert " continue ici." in doc.inserted
    assert "générée" in message.lower()
    # le prompt envoyé au LLM est bien le paragraphe
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert prompt == "Le paragraphe en cours de rédaction"


# Sans sélection : tout le document, section par section, écrit une fois la
# dernière réponse reçue.

ARTICLE = ["Rapport", "Le contexte.", "Analyse", "Les chiffres.", "Les causes."]
ARTICLE_STYLES = ["Heading 1", "Standard", "Heading 2", "Standard", "Standard"]


def _article():
    return FakeBodyDoc(ARTICLE, ARTICLE_STYLES)


@MODES
def test_summarize_without_selection_adds_a_summary_after_the_document(append_mode):
    """Le résumé suit le dernier paragraphe, même en mode remplacement : il ne
    prend jamais la place du document."""
    doc = _article()
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Les causes expliquent les chiffres."))])

    message = presets.run_summarize(_ctx(doc, shell), shell, "", None,
                                    append_mode=append_mode)

    assert doc.paragraphs == ARTICLE + ["---début-du-résumé---",
                                        "Les causes expliquent les chiffres.",
                                        "---fin-du-résumé---"]
    assert doc.styles[len(ARTICLE):] == ["Standard"] * 3
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert all(text in prompt for text in ARTICLE)
    assert message == _t("preset.done_summarize_document")


def test_a_long_document_is_summarized_section_by_section_then_as_a_whole():
    doc = _article()
    shell = FakeShell(config={"document_chunk_max_chars": 30}, responses=[
        FakeSSEResponse(text_chunks("Contexte posé.")),
        FakeSSEResponse(text_chunks("Chiffres et causes.")),
        FakeSSEResponse(text_chunks("Résumé final.")),
    ])
    teed = []

    presets.run_summarize(_ctx(doc, shell), shell, "", teed.append)

    final_prompt = shell.requests[-1]["messages"][-1]["content"]
    assert "Contexte posé.\n\nChiffres et causes." in final_prompt
    assert "Les chiffres." not in final_prompt
    assert doc.paragraphs[len(ARTICLE) + 1] == "Résumé final."
    assert "".join(teed) == "Résumé final.\n"


def test_shorten_without_selection_rewrites_each_section_under_its_heading():
    doc = _article()
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Contexte.")),
                                 FakeSSEResponse(text_chunks("Chiffres, causes."))])

    message = presets.run_shorten(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Rapport", "Contexte.", "Analyse", "Chiffres, causes."]
    assert doc.styles == ["Heading 1", "Standard", "Heading 2", "Standard"]
    assert "Les causes." in shell.requests[1]["messages"][-1]["content"]
    assert "Le contexte." not in shell.requests[1]["messages"][-1]["content"]
    assert doc.undo.entered == [_t("preset.undo_shorten")]
    assert doc.writes_outside_undo == 0
    assert message == _t("preset.resized", target=4)


@pytest.mark.parametrize("limit", ["beaucoup", 0, -5])
def test_an_unusable_chunk_limit_falls_back_to_the_default(limit):
    """Une valeur de configuration qui n'est pas un nombre positif : le
    document part comme avec le seuil par défaut, une section par appel."""
    doc = _article()
    shell = FakeShell(config={"document_chunk_max_chars": limit},
                      responses=[FakeSSEResponse(text_chunks("Contexte.")),
                                 FakeSSEResponse(text_chunks("Chiffres, causes."))])

    presets.run_shorten(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Rapport", "Contexte.", "Analyse", "Chiffres, causes."]
    assert len(shell.requests) == 2


def test_simplify_without_selection_asks_the_configured_token_budget():
    """Le budget de la réponse reste celui de la configuration, quelle que
    soit la taille des morceaux : il tient dans le contexte du modèle."""
    doc = _article()
    shell = FakeShell(config={"simplify_selection_max_tokens": 9000},
                      responses=[FakeSSEResponse(text_chunks("Le cadre.")),
                                 FakeSSEResponse(text_chunks("Les nombres."))])

    presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)

    assert [request["max_tokens"] for request in shell.requests] == [9000, 9000]


def test_shorten_removes_the_surplus_paragraphs_while_changes_are_recorded():
    doc = _article()
    doc.record_changes = True
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Contexte.")),
                                 FakeSSEResponse(text_chunks("Chiffres, causes."))])

    presets.run_shorten(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Rapport", "Contexte.", "Analyse", "Chiffres, causes."]


def test_simplify_without_selection_in_append_mode_keeps_the_document():
    doc = _article()
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Le cadre.")),
                                 FakeSSEResponse(text_chunks("Les nombres.\nPourquoi."))])

    message = presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=True)

    assert doc.paragraphs == ARTICLE + [
        "---reformulation-du-texte---", "Rapport", "Le cadre.", "Analyse",
        "Les nombres.", "Pourquoi.", "---fin-de-reformulation---"]
    assert message == _t("preset.done_simplify_document")


@pytest.mark.parametrize("second_reply,expected", [
    (lambda cancel: FakeSSEResponse(text_chunks("Voulez-vous une version plus courte ?")),
     lambda: _t("preset.question_asked")),
    (lambda cancel: OSError("connexion refusée"), lambda: error_message("network_error")),
    (lambda cancel: StoppedSSEResponse(text_chunks("Les ", "nombres."), cancel),
     lambda: _t("run.stopped")),
    (lambda cancel: FakeSSEResponse(text_chunks("Les nombres et", finish="length")),
     lambda: error_message("truncated")),
], ids=["question", "error", "stopped", "truncated"])
def test_a_whole_document_action_cut_short_writes_nothing(second_reply, expected):
    """Une question du modèle, une erreur, un arrêt ou une réponse coupée au
    plafond de jetons sur une section laisse tout le document tel quel, y
    compris les sections déjà réécrites."""
    cancel = threading.Event()
    doc = _article()
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Le cadre.")),
                                 second_reply(cancel)])

    message = presets.run_simplify(_ctx(doc, shell), shell, "", None,
                                   cancel_event=cancel, append_mode=False)

    assert doc.paragraphs == ARTICLE
    assert doc.writes == 0
    assert message == expected()


def test_a_document_edited_during_a_whole_document_action_is_left_alone():
    doc = _article()
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Résumé."))])
    urlopen = shell.urlopen

    def _user_types_then_model_answers(request, timeout=None):
        doc.items[1].text = "Le contexte, revu à la main."
        return urlopen(request, timeout)

    shell.urlopen = _user_types_then_model_answers
    message = presets.run_summarize(_ctx(doc, shell), shell, "", None)

    assert message == _t("preset.target_changed")
    assert len(doc.paragraphs) == len(ARTICLE)
    assert doc.undo.entered == []


def test_an_empty_document_is_reported_without_calling_the_model():
    doc = FakeBodyDoc(["", ""])
    shell = FakeShell()

    message = presets.run_lengthen(_ctx(doc, shell), shell, "", None)

    assert message == _t("palette.empty_document")
    assert shell.requests == []


def test_the_table_of_contents_is_never_rewritten():
    """Writer régénère la table des matières : elle n'est ni envoyée au
    modèle, ni écrite."""
    doc = FakeBodyDoc(["Table des matières", "Rapport\t1", "Rapport", "Le contexte, en détail."],
                      ["Contents Heading", "Contents 1", "Heading 1", "Standard"])
    doc.items[0].index = doc.items[1].index = "Table des matières1"
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Le contexte."))])

    presets.run_shorten(_ctx(doc, shell), shell, "", None)

    assert doc.paragraphs == ["Table des matières", "Rapport\t1", "Rapport", "Le contexte."]
    assert "Rapport\t1" not in shell.requests[0]["messages"][-1]["content"]


@pytest.mark.parametrize("runner", [presets.run_summarize, presets.run_simplify,
                                    presets.run_shorten, presets.run_lengthen],
                         ids=["summarize", "simplify", "shorten", "lengthen"])
def test_a_selection_that_is_not_text_leaves_the_document_alone(runner):
    """Des cellules de tableau sélectionnées : Writer rend un curseur de
    tableau, pas une plage de texte. Rien n'est envoyé ni écrit."""
    doc = _article()
    doc.controller.getSelection = lambda: SimpleNamespace(getRangeName=lambda: "A1:B1")
    shell = FakeShell()

    message = runner(_ctx(doc, shell), shell, "", None, append_mode=False)

    assert message == _t("sel.no_target")
    assert doc.paragraphs == ARTICLE
    assert shell.requests == []


@pytest.mark.parametrize("runner", [presets.run_extend, presets.run_summarize,
                                    presets.run_simplify, presets.run_shorten,
                                    presets.run_lengthen],
                         ids=["extend", "summarize", "simplify", "shorten", "lengthen"])
def test_several_selected_passages_are_refused_without_calling_the_model(runner):
    """Une sélection multiple (Ctrl) : l'action ne traiterait que le premier
    passage. Elle le dit, sans rien envoyer ni écrire."""
    doc = FakeWriterDoc(selection_text="Premier passage.")
    doc.controller.getSelection = lambda: SimpleNamespace(
        getCount=lambda: 2, getByIndex=lambda _i: doc.selection_range)
    shell = FakeShell()

    message = runner(_ctx(doc, shell), shell, "", None)

    assert message == _t("sel.several_ranges")
    assert doc.inserted == "" and doc.replaced is None
    assert shell.requests == []


def test_stopped_shorten_keeps_the_selection():
    """« Arrêter » pendant le flux : la sélection n'est pas remplacée par un
    texte tronqué, et l'arrêt est annoncé comme tel."""
    cancel = threading.Event()
    doc = FakeWriterDoc(selection_text="Un texte bien trop long à raccourcir.")
    shell = FakeShell(responses=[
        StoppedSSEResponse(text_chunks("Un ", "texte ", "court."), cancel)])
    message = presets.run_shorten(_ctx(doc, shell), shell, "", None,
                                  cancel_event=cancel)
    assert doc.replaced is None
    assert message == _t("run.stopped")


def test_extend_question_triggers_single_retry():
    doc = FakeWriterDoc(selection_text="Texte de départ")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("Comment puis-je vous aider ?")),
        FakeSSEResponse(text_chunks("la suite réelle du texte.")),
    ])
    presets.run_extend(_ctx(doc, shell), shell, "", None)
    assert "Comment puis-je" not in doc.inserted
    assert "la suite réelle du texte." in doc.inserted
    # le retry porte la directive renforcée
    retry_system = shell.requests[1]["messages"][0]["content"]
    assert "INTERDIT" in retry_system


def test_extend_stopped_during_its_retry_writes_nothing():
    cancel = threading.Event()
    doc = FakeWriterDoc(selection_text="Texte de départ")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("Comment puis-je vous aider ?")),
        StoppedSSEResponse(text_chunks("la suite ", "réelle."), cancel),
    ])
    message = presets.run_extend(_ctx(doc, shell), shell, "", None, cancel_event=cancel)
    assert doc.inserted == ""
    assert message == _t("run.stopped")


def test_extend_retry_starts_its_own_line_after_text_already_shown():
    """Le premier essai a affiché un début de texte avant de poser sa question :
    le second essai commence sur une nouvelle ligne de la palette."""
    doc = FakeWriterDoc(selection_text="Il était une fois")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("Voici la suite. ", "Pouvez-vous préciser ?")),
        FakeSSEResponse(text_chunks(" un roi", " généreux.")),
    ])
    shown = []
    presets.run_extend(_ctx(doc, shell), shell, "", shown.append)
    assert "".join(shown) == "Voici la suite. \n un roi généreux."
    assert " un roi généreux." in doc.inserted
    assert "Voici la suite." not in doc.inserted


def test_extend_double_question_is_reported_once_and_writes_nothing():
    """Deux questions de suite : le bilan le dit, une seule fois, et la
    palette ne montre aucun message de repli en plus."""
    doc = FakeWriterDoc(selection_text="Texte")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("Voulez-vous que je continue ?")),
        FakeSSEResponse(text_chunks("Souhaitez-vous que je poursuive ?")),
    ])
    shown = []
    message = presets.run_extend(_ctx(doc, shell), shell, "", shown.append)
    assert "".join(shown) == ""
    assert message == _t("preset.question_asked")
    assert doc.inserted == ""


def test_summarize_markers_and_stop_phrase():
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Résumé concis.", " [END] ceci ne doit pas apparaître"))])
    presets.run_summarize(_ctx(doc, shell), shell, "", None)
    assert "---début-du-résumé---" in doc.inserted
    assert "Résumé concis." in doc.inserted
    assert "ne doit pas apparaître" not in doc.inserted
    assert "---fin-du-résumé---" in doc.inserted
    assert doc.undo.entered == ["Résumer"]


def test_summarize_prompt_matches_legacy_template():
    doc = FakeWriterDoc(selection_text="Contenu.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("R."))])
    presets.run_summarize(_ctx(doc, shell), shell, "", None)
    user_prompt = shell.requests[0]["messages"][-1]["content"]
    assert user_prompt.startswith("TEXTE À RÉSUMER :")
    assert "RÉSUMÉ :" in user_prompt
    system = shell.requests[0]["messages"][0]["content"]
    assert system.startswith("/no_thinking")           # défaut hérité
    assert "résumeur professionnel" in system


def test_simplify_question_in_append_mode_writes_nothing():
    doc = FakeWriterDoc(selection_text="Texte complexe.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Voulez-vous une version simplifiée ?"))])
    presets.run_simplify(_ctx(doc, shell), shell, "", None)
    assert doc.inserted == ""
    assert doc.undo.entered == []


def test_a_sink_without_deltas_writes_the_final_reply_in_append_mode():
    """Une demande libre sur une sélection streame vers la palette, puis ne
    passe au sink que le texte final (streamed=True)."""
    doc = FakeWriterDoc(selection_text="Texte.")
    sink = presets.text_sink(_ctx(doc, FakeShell()), True, "<début>", "<fin>")
    sink.finish("Texte modifié.", True)
    assert doc.inserted == "<début>Texte modifié.<fin>"


def test_simplify_question_in_replace_mode_keeps_the_selection():
    doc = FakeWriterDoc(selection_text="Texte complexe.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Voulez-vous une version simplifiée ?"))])
    message = presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced is None
    assert doc.inserted == ""
    assert message == presets._t("preset.question_asked")


@pytest.mark.parametrize("question", ["Pouvez-vous préciser le public visé ?",
                                      "Pouvez-vous clarifier ce point ?",
                                      "Could you clarify the audience?"])
def test_simplify_takes_a_request_for_details_as_a_question(question):
    doc = FakeWriterDoc(selection_text="Texte complexe.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(question))])
    message = presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced is None
    assert doc.inserted == ""
    assert message == presets._t("preset.question_asked")


def test_simplify_without_selection_takes_a_request_for_details_as_a_question():
    doc = _article()
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Pouvez-vous préciser le public visé ?"))])
    message = presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.paragraphs == ARTICLE
    assert doc.writes == 0
    assert message == presets._t("preset.question_asked")


def test_summarize_replace_mode_stops_at_the_stop_phrase():
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Résumé concis.", " [END] ceci ne doit pas apparaître"))])
    message = presets.run_summarize(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced == "Résumé concis."
    assert message == presets._t("preset.done_summarize_replaced")


def test_replace_mode_filters_a_reply_received_in_one_block():
    doc = FakeWriterDoc(selection_text="Texte.")
    sink = presets.text_sink(_ctx(doc, FakeShell()), False, "", "",
                             stop_phrases=["[END]"])
    sink.finish("Résumé. [END] reste", streamed=False)
    assert doc.replaced == "Résumé."


def test_a_sink_without_deltas_writes_the_final_reply_in_replace_mode():
    doc = FakeWriterDoc(selection_text="Texte.")
    sink = presets.text_sink(_ctx(doc, FakeShell()), False, "", "")
    sink.finish("Texte modifié.", True)
    assert doc.replaced == "Texte modifié."


def test_simplify_replace_mode_says_the_selection_was_replaced():
    doc = FakeWriterDoc(selection_text="Texte alambiqué.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Texte simple."))])
    message = presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced == "Texte simple."
    assert message == presets._t("preset.done_simplify_replaced")


def test_simplify_question_in_append_mode_reports_the_question():
    doc = FakeWriterDoc(selection_text="Texte complexe.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Voulez-vous une version simplifiée ?"))])
    message = presets.run_simplify(_ctx(doc, shell), shell, "", None)
    assert message == presets._t("preset.question_asked")


def test_shorten_question_keeps_the_selection():
    doc = FakeWriterDoc(selection_text=" ".join(f"mot{i}" for i in range(20)))
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Voulez-vous que je raccourcisse davantage ?"))])
    message = presets.run_shorten(_ctx(doc, shell), shell, "", None)
    assert doc.replaced is None
    assert message == presets._t("preset.question_asked")


def test_shorten_keeps_a_question_quoted_from_the_text():
    doc = FakeWriterDoc(selection_text=(
        "Comment puis-je obtenir mon attestation ? Il faut se rendre sur le site."))
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(
        "Comment puis-je obtenir mon attestation ? Allez sur le site."))])
    presets.run_shorten(_ctx(doc, shell), shell, "", None)
    assert doc.replaced == "Comment puis-je obtenir mon attestation ? Allez sur le site."


def test_simplify_keeps_a_question_from_the_text_reworded():
    doc = FakeWriterDoc(selection_text=(
        "De quelle manière puis-je contester la décision ? Vous disposez de deux mois."))
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(
        "Comment puis-je contester la décision ? Vous avez deux mois."))])
    presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced == "Comment puis-je contester la décision ? Vous avez deux mois."


def test_simplify_question_patterns_match_whole_words():
    doc = FakeWriterDoc(selection_text="Les demandeurs doivent fournir des pièces.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(
        "Applicants send documents. They should include an ID."))])
    presets.run_simplify(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced == "Applicants send documents. They should include an ID."


def test_a_stop_phrase_split_across_deltas_is_cut_in_replace_mode():
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(
        "Résumé concis.", " [", "END", "]", " reste"))])
    presets.run_summarize(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced == "Résumé concis."


def test_a_reply_without_text_is_not_reported_as_written():
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("[END]"))])
    message = presets.run_summarize(_ctx(doc, shell), shell, "", None, append_mode=False)
    assert doc.replaced is None
    assert message == presets._t("run.err_empty")


def test_summarize_question_in_append_mode_is_reported():
    doc = FakeWriterDoc(selection_text="Un texte.")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("Pouvez-vous préciser le contexte ?"))])
    message = presets.run_summarize(_ctx(doc, shell), shell, "", None)
    assert doc.inserted == ""
    assert message == presets._t("preset.question_asked")


def test_simplify_normal_flow():
    doc = FakeWriterDoc(selection_text="Texte alambiqué.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Texte simple."))])
    presets.run_simplify(_ctx(doc, shell), shell, "", None)
    assert "---reformulation-du-texte---" in doc.inserted
    assert "Texte simple." in doc.inserted
    assert "---fin-de-reformulation---" in doc.inserted


def test_shorten_replaces_selection_and_reselects():
    words = " ".join(f"mot{i}" for i in range(20))
    doc = FakeWriterDoc(selection_text=words)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("version courte."))])
    message = presets.run_shorten(_ctx(doc, shell), shell, "", None)
    assert doc.replaced == "version courte."
    assert doc.selected, "la sélection doit être restaurée pour itérer"
    assert doc.undo.entered == ["Raccourcir"]
    assert "13" in message                     # round(20 × 0.65) = 13
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "environ 13 mots" in prompt


def test_lengthen_ratio():
    words = " ".join(f"mot{i}" for i in range(10))
    doc = FakeWriterDoc(selection_text=words)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("version longue."))])
    presets.run_lengthen(_ctx(doc, shell), shell, "", None)
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "environ 14 mots" in prompt         # round(10 × 1.4) = 14


def test_http_error_returns_user_message_writer():
    import io
    import urllib.error
    doc = FakeWriterDoc(selection_text="Texte.")
    error = urllib.error.HTTPError("http://x", 429, "quota", {},
                                   io.BytesIO(b"{}"))
    shell = FakeShell(responses=[error])
    message = presets.run_summarize(_ctx(doc, shell), shell, "", None)
    assert "Quota" in message
    assert doc.undo.entered == []              # rien d'écrit, rien à annuler


# Lire avant, écrire après : le document ne reçoit que la réponse complète

@MODES
def test_the_document_is_untouched_while_the_model_writes(append_mode):
    """Le texte du modèle s'affiche dans la palette au fil du flux ; le
    document n'est écrit, et l'annulation ouverte, qu'une fois la réponse
    complète."""
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    during = []
    shown = []
    shell = FakeShell(responses=[ProbedSSEResponse(
        text_chunks("Résumé ", "concis."),
        lambda: during.append((doc.inserted, doc.replaced, list(doc.undo.entered),
                               "".join(shown))))])

    presets.run_summarize(_ctx(doc, shell), shell, "", shown.append,
                          append_mode=append_mode)

    assert during == [("", None, [], "Résumé ")]
    assert "".join(shown) == "Résumé concis."
    assert "Résumé concis." in (doc.inserted if append_mode else doc.replaced)


@MODES
def test_the_final_write_is_a_single_undo_action(append_mode):
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    events = []
    doc.undo.enterUndoContext = lambda label: events.append(
        ("enter", label, doc.inserted, doc.replaced))
    doc.undo.leaveUndoContext = lambda: events.append(
        ("leave", doc.inserted, doc.replaced))
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Résumé ", "concis."))])

    presets.run_summarize(_ctx(doc, shell), shell, "", None, append_mode=append_mode)

    written = ((SUMMARY_HEADER + "Résumé concis." + SUMMARY_FOOTER, None) if append_mode
               else ("", "Résumé concis."))
    assert events == [("enter", "Résumer", "", None), ("leave", *written)]


def test_a_stop_leaves_nothing_in_the_document_in_append_mode():
    cancel = threading.Event()
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[
        StoppedSSEResponse(text_chunks("Un ", "résumé."), cancel)])

    message = presets.run_summarize(_ctx(doc, shell), shell, "", None,
                                    cancel_event=cancel)

    assert doc.inserted == ""
    assert doc.undo.entered == []
    assert message == _t("run.stopped")


class _StopBetweenStreamAndWrite:
    """Le clic « Arrêter » est traité par le thread principal entre la fin du
    flux et l'exécution de la tâche d'écriture filée."""

    def __init__(self, cancel_event):
        self._cancel_event = cancel_event
        self._calls = 0

    def call(self, fn, timeout=30.0):
        self._calls += 1
        if self._calls == 2:          # 1 : lecture de la cible ; 2 : l'écriture
            self._cancel_event.set()
        return fn()


@MODES
def test_a_stop_during_the_final_write_leaves_the_document_untouched(append_mode):
    cancel = threading.Event()
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Résumé ", "concis."))])
    ctx = ToolContext(doc, doc.controller, "writer", shell,
                      dispatcher=_StopBetweenStreamAndWrite(cancel))
    shown = []

    message = presets.run_summarize(ctx, shell, "", shown.append, cancel_event=cancel,
                                    append_mode=append_mode)

    assert "".join(shown) == "Résumé concis."       # le flux est allé au bout
    assert (doc.inserted, doc.replaced) == ("", None)
    assert doc.undo.entered == []
    assert message == _t("run.stopped")


@MODES
def test_an_interrupted_reply_leaves_nothing_in_the_document(append_mode):
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    shell = FakeShell(responses=[_BrokenSSEResponse(text_chunks("Un ", "résumé."))])

    message = presets.run_summarize(_ctx(doc, shell), shell, "", None,
                                    append_mode=append_mode)

    assert (doc.inserted, doc.replaced) == ("", None)
    assert message == error_message("network_error")


@MODES
def test_nothing_is_written_when_the_target_changed_during_the_run(append_mode):
    """L'utilisateur retouche le texte visé pendant la génération : rien n'est
    écrit, le résultat reste dans la palette et le message dit pourquoi."""
    doc = FakeWriterDoc(selection_text="Un long texte administratif.")
    edited = "Un long texte administratif, retouché."
    shell = FakeShell(responses=[ProbedSSEResponse(
        text_chunks("Résumé ", "concis."),
        lambda: doc.selection_range.setString(edited))])
    shown = []

    message = presets.run_summarize(_ctx(doc, shell), shell, "", shown.append,
                                    append_mode=append_mode)

    assert (doc.inserted, doc.replaced) == ("", edited)
    assert doc.undo.entered == []
    assert "".join(shown) == "Résumé concis."
    assert message == _t("preset.target_changed")


@MODES
def test_the_outcome_reads_the_legacy_sinks_too(append_mode):
    """Sans cible lue d'avance, `text_sink` rend un ancien sink : le bilan du
    runner le lit sans erreur."""
    doc = FakeWriterDoc(selection_text="Texte.")
    sink = presets.text_sink(_ctx(doc, FakeShell()), append_mode, "", "")
    sink.stream_delta("Résumé.")
    assert presets._outcome(sink, "fait") == "fait"


def test_a_reply_received_in_one_block_is_filtered_before_the_final_write():
    doc = FakeWriterDoc(selection_text="Texte.")
    sink = presets.text_sink(_ctx(doc, FakeShell()), False, "", "",
                             target=doc.selection_range, original="Texte.",
                             stop_phrases=["[END]"])
    sink.finish("Résumé. [END] reste", streamed=False)
    assert doc.replaced == "Résumé."
