"""Writer menu actions extracted from MainJob.trigger."""

import re

from ..formatting import insert_formatted
from ..i18n import t as _t
from .shared import apply_settings_result

_RE_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)




def _truncate_at_stop_phrase(text, stop_phrases):
    """Cut *text* right before the earliest occurrence of any stop phrase."""
    lower = text.lower()
    cut = len(text)
    for phrase in stop_phrases:
        pos = lower.find(phrase.lower())
        if pos != -1:
            cut = min(cut, pos)
    return text[:cut].rstrip()


def _collect_stream(job, request, api_type, question_patterns=None, stop_phrases=None):
    """Drain a streaming request into a single string (no insertion here).

    Returns (text, asked_question):
    - if *question_patterns* is given and one is found anywhere in the
      response, returns ("", True) — caller decides how to report it;
    - otherwise returns the response with think-blocks stripped and,
      if *stop_phrases* is given, truncated at the first stop phrase.
    """
    accumulated = [""]

    def _callback(chunk_text):
        accumulated[0] += chunk_text

    job.stream_request(request, api_type, _callback)
    full = _strip_think_blocks(accumulated[0])

    if question_patterns:
        lower = full.lower()
        if any(pattern in lower for pattern in question_patterns):
            return "", True

    if stop_phrases:
        full = _truncate_at_stop_phrase(full, stop_phrases)

    return full.strip(), False


def _scroll_to_cursor(controller, cursor):
    """Move the view cursor to the text cursor so the document auto-scrolls."""
    try:
        view_cursor = controller.getViewCursor()
        view_cursor.gotoRange(cursor.getEnd(), False)
    except Exception:
        pass


def _undo_context(model, label):
    """Context manager that groups all Writer insertions into one undo step."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        mgr = None
        try:
            mgr = model.getUndoManager()
            mgr.enterUndoContext(label)
        except Exception:
            mgr = None
        try:
            yield
        finally:
            if mgr is not None:
                try:
                    mgr.leaveUndoContext()
                except Exception:
                    pass

    return _ctx()


_EXTEND_QUESTION_PATTERNS = [
    "puis-je vous", "puis-je t'", "comment puis-je", "en quoi puis-je",
    "que puis-je faire", "puis-je vous aider",
    "pouvez-vous préciser", "pouvez-vous clarifier",
    "could you clarify", "how can i help", "would you like me to",
    "voulez-vous que je", "souhaitez-vous que",
]


def _extend_selection(job, text, selection, text_range, controller=None, model=None):
    job._send_telemetry(
        "ExtendSelection",
        {
            "action": "extend_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    if len(text_range.getString()) <= 0:
        return

    try:
        base_text = text_range.getString()

        # Directive system prompt — prevents conversational responses up-front.
        configured_sp = str(job.get_config("extend_selection_system_prompt", "") or "").strip()
        directive = (
            "Continue DIRECTEMENT le texte fourni par l'utilisateur. "
            "Écris uniquement la suite naturelle, sans question, sans reformulation, "
            "sans introduction. "
            "Écris dans la même langue que le texte fourni."
        )
        system_prompt = (directive + " " + configured_sp) if configured_sp else directive

        max_tokens = job.get_config("extend_selection_max_tokens", 15000)
        request = job.make_api_request(base_text, system_prompt, max_tokens)

        cursor = text.createTextCursorByRange(text_range)
        cursor.collapseToEnd()

        with _undo_context(model, "Générer la suite"):
            text.insertString(cursor, "\n\n---début-du-texte-généré---\n", False)

            generated, asked_question = _collect_stream(
                job, request, "chat", question_patterns=_EXTEND_QUESTION_PATTERNS,
            )

            # Auto-retry with a stronger directive when the model asked a question.
            if asked_question:
                retry_sp = (
                    "Tu dois CONTINUER le texte de l'utilisateur mot après mot, "
                    "comme si tu en étais l'auteur. Il est INTERDIT de poser une question "
                    "ou de reformuler. Commence immédiatement par les mots qui suivent "
                    "naturellement le texte fourni."
                )
                retry_prompt = (
                    "Voici le texte à continuer :\n\n" + base_text
                    + "\n\nÉcris UNIQUEMENT la suite directe, sans aucune introduction."
                )
                retry_request = job.make_api_request(retry_prompt, retry_sp, max_tokens)
                generated, asked_again = _collect_stream(
                    job, retry_request, "chat", question_patterns=_EXTEND_QUESTION_PATTERNS,
                )
                if asked_again:
                    text.insertString(
                        cursor,
                        "\n[Le modèle n'a pas pu continuer le texte."
                        " Essayez de sélectionner plus de contexte.]",
                        False,
                    )
                    generated = ""

            if generated:
                insert_formatted(model, text, cursor, generated)
                _scroll_to_cursor(controller, cursor)

            text.insertString(cursor, "\n---fin-du-texte-généré---\n", False)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


def _edit_selection(job, text, selection, text_range):
    job._send_telemetry(
        "EditSelection",
        {
            "action": "edit_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    try:
        job._show_edit_selection_dialog(text, text_range)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


def _resize_selection(job, text, selection, text_range, controller=None, model=None):
    job._send_telemetry(
        "ResizeSelection",
        {
            "action": "resize_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    try:
        job._show_resize_dialog(text, text_range, controller=controller, model=model)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


def _summarize_selection(job, text, selection, text_range, controller=None, model=None):
    job._send_telemetry(
        "SummarizeSelection",
        {
            "action": "summarize_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    try:
        original_text = text_range.getString()
        if len(original_text.strip()) == 0:
            return

        prompt = (
            """TEXTE À RÉSUMER :
"""
            + original_text
            + """

Crée le résumé le plus court possible qui capture les informations essentielles.
Sois extrêmement concis — utilise le minimum de mots nécessaire.
Ne pose AUCUNE question.
Produis UNIQUEMENT le texte du résumé, sans introduction ni explication.
IMPORTANT : Réponds dans la MÊME LANGUE que le texte original.

RÉSUMÉ :
"""
        )

        system_prompt = (
            "Tu es un résumeur professionnel. Tu crées des résumés ultra-concis "
            "en utilisant le minimum de mots nécessaire tout en préservant "
            "les informations clés."
        )
        max_tokens = int(job.get_config("summarize_selection_max_tokens", 15000))
        request = job.make_api_request(prompt, system_prompt, max_tokens)

        cursor = text.createTextCursorByRange(text_range)
        cursor.collapseToEnd()

        with _undo_context(model, "Résumer"):
            text.insertString(cursor, "\n\n---début-du-résumé---\n", False)

            summary, _ = _collect_stream(job, request, "chat", stop_phrases=["[END]", "---END---"])
            if summary:
                insert_formatted(model, text, cursor, summary)
                _scroll_to_cursor(controller, cursor)

            text.insertString(cursor, "\n---fin-du-résumé---\n", False)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


def _simplify_selection(job, text, selection, text_range, controller=None, model=None):
    job._send_telemetry(
        "SimplifySelection",
        {
            "action": "simplify_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    try:
        original_text = text_range.getString()
        if len(original_text.strip()) == 0:
            return

        prompt = (
            """TEXTE À REFORMULER :
"""
            + original_text
            + """

Réécris ce texte dans un langage clair et simple compréhensible par tous.
Utilise :
- Des phrases courtes
- Des mots courants (évite le jargon et les termes techniques)
- La voix active
- Des exemples concrets quand c'est possible

RÈGLES :
- Garde la MÊME LANGUE que le texte original
- Ne traduis PAS dans une autre langue
- Ne pose AUCUNE question
- N'ajoute AUCUNE explication
- Produis UNIQUEMENT le texte reformulé

VERSION REFORMULÉE :
"""
        )

        system_prompt = (
            "Tu es un expert en langage simplifié. Tu réécris les textes complexes "
            "dans un langage clair et simple accessible à tous. Tu utilises "
            "des phrases courtes et des mots courants."
        )
        configured_sp = str(job.get_config("simplify_selection_system_prompt", "") or "").strip()
        if configured_sp:
            system_prompt = configured_sp + " " + system_prompt
        max_tokens = len(original_text) + job.get_config("simplify_selection_max_tokens", 15000)
        request = job.make_api_request(prompt, system_prompt, max_tokens)

        cursor = text.createTextCursorByRange(text_range)
        cursor.collapseToEnd()

        # Only true conversational questions — NOT response format prefixes like
        # "Voici le texte reformulé" which are normal model output patterns.
        question_patterns = [
            "would you like", "do you want", "should i", "can i help",
            "voulez-vous", "souhaitez-vous", "dois-je", "puis-je",
        ]

        with _undo_context(model, "Reformuler"):
            text.insertString(cursor, "\n\n---reformulation-du-texte---\n", False)

            simplified, asked_question = _collect_stream(
                job, request, "chat",
                question_patterns=question_patterns,
                stop_phrases=["[END]", "---END---"],
            )
            if asked_question:
                text.insertString(cursor, "[Le modèle a posé une question. Veuillez réessayer.]", False)
            elif simplified:
                insert_formatted(model, text, cursor, simplified)
                _scroll_to_cursor(controller, cursor)

            text.insertString(cursor, "\n---fin-de-reformulation---\n", False)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


def _open_mirai_website(job):
    job._send_telemetry("OpenmiraiWebsite", {"action": "open_website"})
    try:
        import webbrowser

        portal_url = job.get_config("portal_url", "")
        if portal_url:
            webbrowser.open(portal_url)
        else:
            webbrowser.open("https://mirai.interieur.gouv.fr")
    except Exception as e:
        job._log(f"Error opening website: {str(e)}")


def _open_documentation(job):
    job._send_telemetry("OpenDocumentation", {"action": "open_documentation"})
    try:
        import webbrowser

        doc_url = job.get_config("doc_url", "")
        if doc_url:
            webbrowser.open(doc_url)
            return
        portal_url = job.get_config("portal_url", "")
        if portal_url:
            webbrowser.open(portal_url)
    except Exception as e:
        job._log(f"Error opening documentation: {str(e)}")


def _open_settings(job, selection):
    job._send_telemetry("OpenSettings", {"action": "open_settings"})
    try:
        result = job.settings_box("Settings")
        apply_settings_result(job, result)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ":error: " + str(e))


def _get_writer_selection(job, model):
    text = model.Text
    ctrl = model.CurrentController
    selection = ctrl.getSelection()
    count = 0
    try:
        count = int(selection.getCount())
    except Exception:
        count = 1
    if count <= 0:
        job._log("[writer-selection] empty selection container")
        return text, ctrl, selection, None, ""
    text_range = selection.getByIndex(0)
    selected_text = text_range.getString()
    job._log(
        "[writer-selection] ranges=%s selected_chars=%s empty=%s"
        % (count, len(selected_text or ""), not bool((selected_text or "").strip()))
    )
    return text, ctrl, selection, text_range, selected_text


def _correct_selection(job, text, selection, text_range, controller=None, model=None):
    job._send_telemetry(
        "CorrectSelection",
        {
            "action": "correct_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    try:
        original_text = text_range.getString()
        if not original_text.strip():
            return

        prompt = (
            "TEXTE À CORRIGER :\n"
            + original_text
            + """

Corrige les fautes d'orthographe, de grammaire et de syntaxe de ce texte.
Garde le sens, le style et la structure d'origine.

RÈGLES :
- Garde la MÊME LANGUE que le texte original
- Ne change PAS le sens ou le registre
- Ne reformule PAS inutilement, corrige uniquement les erreurs
- Ne pose AUCUNE question
- N'ajoute AUCUNE explication
- Produis UNIQUEMENT le texte corrigé

TEXTE CORRIGÉ :
"""
        )

        system_prompt = (
            "Tu es un correcteur orthographique et grammatical expert. "
            "Tu corriges les fautes d'orthographe, de grammaire et de syntaxe "
            "en préservant le sens, le style et la langue du texte original. "
            "Tu ne reformules pas — tu corriges uniquement."
        )
        max_tokens = len(original_text) + int(job.get_config("correct_selection_max_tokens", 4000))
        request = job.make_api_request(prompt, system_prompt, max_tokens)

        cursor = text.createTextCursorByRange(text_range)
        cursor.collapseToEnd()

        with _undo_context(model, "Corriger"):
            text.insertString(cursor, "\n\n---début-de-correction---\n", False)

            corrected, _ = _collect_stream(job, request, "chat", stop_phrases=["[END]", "---END---"])
            if corrected:
                insert_formatted(model, text, cursor, corrected)
                _scroll_to_cursor(controller, cursor)

            text.insertString(cursor, "\n---fin-de-correction---\n", False)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


def _translate_selection(job, text, selection, text_range, controller=None, model=None):
    job._send_telemetry(
        "TranslateSelection",
        {
            "action": "translate_selection",
            "text_length": str(len(text_range.getString())),
        },
    )

    try:
        original_text = text_range.getString()
        if not original_text.strip():
            return

        prompt = (
            "TEXTE À TRADUIRE :\n"
            + original_text
            + """

Traduis ce texte. Si le texte est en français, traduis-le en anglais. Sinon, traduis-le en français.

RÈGLES :
- Traduis fidèlement le sens, sans paraphraser
- Garde le registre (formel/informel) du texte original
- Ne pose AUCUNE question
- N'ajoute AUCUNE explication ni note du traducteur
- Produis UNIQUEMENT le texte traduit

TRADUCTION :
"""
        )

        system_prompt = (
            "Tu es un traducteur professionnel expert. "
            "Tu traduis fidèlement entre le français et l'anglais "
            "en respectant le sens, le registre et le style du texte original. "
            "Tu produis uniquement la traduction, sans commentaire."
        )
        max_tokens = len(original_text) + int(job.get_config("translate_selection_max_tokens", 4000))
        request = job.make_api_request(prompt, system_prompt, max_tokens)

        cursor = text.createTextCursorByRange(text_range)
        cursor.collapseToEnd()

        with _undo_context(model, "Traduire"):
            text.insertString(cursor, "\n\n---début-de-traduction---\n", False)

            translated, _ = _collect_stream(job, request, "chat", stop_phrases=["[END]", "---END---"])
            if translated:
                insert_formatted(model, text, cursor, translated)
                _scroll_to_cursor(controller, cursor)

            text.insertString(cursor, "\n---fin-de-traduction---\n", False)
    except Exception as e:
        text_range = selection.getByIndex(0)
        text_range.setString(text_range.getString() + ": " + str(e))


_WRITER_TEXT_ACTIONS = (
    "ExtendSelection", "EditSelection", "SummarizeSelection", "SimplifySelection",
    "ResizeSelection", "CorrectSelection", "TranslateSelection",
)


def handle_writer_action(job, args, model):
    if not hasattr(model, "Text"):
        return False
    if args not in _WRITER_TEXT_ACTIONS:
        # Les actions non textuelles sont traitées en amont par la coquille
        # (voir MainJob._handle_shell_action) : les laisser passer ici les
        # exposerait de nouveau au `return True` sur sélection vide ci-dessous.
        return False

    job._log("Processing Writer document")
    text, ctrl, selection, text_range, selected_text = _get_writer_selection(job, model)
    if text_range is None:
        # Sélection non résoluble : le dire. Auparavant on rendait la main en
        # silence, et l'utilisateur ne pouvait pas distinguer « rien à traiter »
        # d'une panne.
        job._log(f"[writer] {args} : aucune plage de texte exploitable")
        job._show_message(
            "Rien à traiter",
            "Placez le curseur dans un paragraphe ou sélectionnez du texte, "
            "puis relancez l'action.")
        return True

    if args == "ExtendSelection":
        _extend_selection(job, text, selection, text_range, controller=ctrl, model=model)
    elif args == "EditSelection":
        _edit_selection(job, text, selection, text_range)
    elif args == "SummarizeSelection":
        _summarize_selection(job, text, selection, text_range, controller=ctrl, model=model)
    elif args == "SimplifySelection":
        _simplify_selection(job, text, selection, text_range, controller=ctrl, model=model)
    elif args == "ResizeSelection":
        _resize_selection(job, text, selection, text_range, controller=ctrl, model=model)
    elif args == "CorrectSelection":
        _correct_selection(job, text, selection, text_range, controller=ctrl, model=model)
    elif args == "TranslateSelection":
        _translate_selection(job, text, selection, text_range, controller=ctrl, model=model)

    return True
