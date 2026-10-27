"""Presets/chips de la palette.

Deux modes :
- "pipeline" : Python pilote, le LLM n'est qu'une fonction texte (marqueurs,
  stop phrases, retry sur question, colonne Résultat IA…), robuste petits
  modèles.
- "agentic"  : le LLM pilote les tools (edit, formule, analyse libre).

Chaque preset émet son span de télémétrie avec {"via": "palette"} pour
préserver les tableaux de bord existants.
"""

import dataclasses
import json
import os
import re
from functools import partial

from ..i18n import t as _t
from . import doc_rewrite, whole_document
from .calc_functions import formula_refusal
from .llm_client import LLMClient
from .orchestrator import error_message
from .prompts import PIPELINE_TEXT_SYSTEM
from .sinks import CalcCellSink, WriterInsertSink, WriterReplaceSink, WriterTargetSink
from .text_filters import (
    EXTEND_QUESTION_PATTERNS,
    SIMPLIFY_QUESTION_PATTERNS,
    STOP_PHRASES,
    patterns_absent_from,
)
from .tools import calc_tools


@dataclasses.dataclass
class Preset:
    id: str
    label_key: str               # cle i18n du libellé de chip (avec pictogramme)
    apps: tuple
    mode: str                     # "pipeline" | "agentic"
    needs_input: bool = False     # requiert une instruction tapée dans le prompt
    input_hint_key: str = ""
    # pipeline : fn(ctx, shell, user_text, tee, cancel_event=None,
    #               dispatcher=None, append_mode=…) -> str
    runner: object = None
    build_extra: object = None    # agentic : fn(ctx, shell, user_text) -> str
    sink_spec: str = "palette"    # agentic : "palette" | "replace_selection" | "auto_edit"
    prompt_template: object = None  # agentic : fn(user_text) -> prompt utilisateur

    @property
    def label(self):
        return _t(self.label_key)

    @property
    def input_hint(self):
        return _t(self.input_hint_key)


def _legacy_telemetry(ctx, span, attrs=None):
    payload = {"via": "palette"}
    payload.update(attrs or {})
    ctx.shell.telemetry(span, payload)


def _read_target(ctx, current_paragraph=False):
    """Cible d'une action directe, lue en un seul passage sur le thread
    principal avant l'appel au modèle : la sélection si elle existe, sinon,
    avec `current_paragraph`, le PARAGRAPHE COURANT (sous le curseur).

    Rend (texte, plage) ; Writer fait suivre à la plage les modifications du
    document, c'est elle qui recevra le résultat. ("", None) si aucun texte
    n'est ciblable : les actions portent alors sur tout le document.
    ("", _NOT_TEXT) si la sélection n'est pas du texte : il n'y a rien à
    faire."""
    return ctx.on_main(lambda: _read_target_on_main(ctx, current_paragraph))


# Sélection qui n'est pas du texte : cellules de tableau, cadre, image.
_NOT_TEXT = object()


def _read_target_on_main(ctx, current_paragraph):
    try:
        rng = ctx.controller.getSelection().getByIndex(0)
        text = rng.getString()
        if text.strip():
            return text, rng
        if not current_paragraph:
            return "", None
        cursor = rng.getText().createTextCursorByRange(rng)
        cursor.gotoStartOfParagraph(False)
        cursor.gotoEndOfParagraph(True)
        paragraph = cursor.getString()
    except Exception:
        return "", _NOT_TEXT
    return (paragraph, cursor) if paragraph.strip() else ("", None)


def _text_client(shell, max_tokens):
    return LLMClient(shell, max_tokens=max_tokens)


def text_sink(ctx, append_mode, header, footer, tee=None, target=None,
              cancel_event=None, **kwargs):
    """Où va le texte produit : à la suite entre marqueurs, ou en remplacement.

    Les deux usages sont légitimes et les utilisateurs ne s'accordent pas :
    remplacer va plus vite, ajouter permet de comparer avant de décider. Le
    choix vient de la case « Ajouter à la suite » de la palette ; ce helper est
    le SEUL endroit qui le traduit en destination, pour qu'aucun preset ne
    puisse l'ignorer.

    Avec `target` (plage lue par `_read_target`), le texte ne fait que
    s'afficher pendant le flux et n'est écrit qu'une fois la réponse complète.
    """
    if target is not None:
        return WriterTargetSink(ctx, target, append_mode, header, footer,
                                tee=tee, cancel_event=cancel_event, **kwargs)
    if append_mode:
        return WriterInsertSink(ctx, header, footer, tee=tee, **kwargs)
    return WriterReplaceSink(ctx, tee=tee, **kwargs)


def report_question(sink):
    """Le modèle a posé une question au lieu de produire le texte. Le message
    de repli suit la destination du sink : dans le document après la sélection
    pour l'ancien sink d'ajout, dans la palette pour les autres."""
    sink.insert_message("[Le modèle a posé une question. Veuillez réessayer.]")


def _outcome(sink, done_message):
    """Message de fin d'un runner Writer, selon ce que le sink a retenu."""
    if sink.question_detected:
        return _t("preset.question_asked")
    if not sink.accumulated.strip():
        return error_message("empty_response")
    if sink.stopped:
        return error_message("cancelled")
    if sink.target_changed:
        return _t("preset.target_changed")
    return done_message


def _system(specific):
    """Système par défaut + consigne spécifique."""
    return PIPELINE_TEXT_SYSTEM + " " + specific if specific else PIPELINE_TEXT_SYSTEM


def run_extend(ctx, shell, user_text, tee, cancel_event=None,
               dispatcher=None):
    base_text, target = _read_target(ctx, current_paragraph=True)
    _legacy_telemetry(ctx, "ExtendSelection",
                      {"action": "extend_selection",
                       "text_length": str(len(base_text))})
    if not base_text:
        return _t("sel.no_target")

    configured = str(shell.get_config("extend_selection_system_prompt", "") or "").strip()
    directive = (
        "Continue DIRECTEMENT le texte fourni par l'utilisateur. "
        "Écris uniquement la suite naturelle, sans question, sans reformulation, "
        "sans introduction."
    )
    system_prompt = _system((directive + " " + configured) if configured else directive)
    max_tokens = shell.get_config("extend_selection_max_tokens", 15000)
    llm = _text_client(shell, max_tokens)

    sink = text_sink(
        ctx, True, "\n\n---début-du-texte-généré---\n", "\n---fin-du-texte-généré---\n",
        tee=tee, target=target, original=base_text, cancel_event=cancel_event,
        undo_label=_t("preset.undo_extend"),
        question_patterns=EXTEND_QUESTION_PATTERNS)

    step = llm.step([{"role": "system", "content": system_prompt},
                     {"role": "user", "content": base_text}],
                    on_text_delta=sink.stream_delta,
                    cancel_event=cancel_event)
    if step.error:
        return error_message(step.error)

    if sink.question_detected:
        # Retry unique avec directive renforcée ; son texte ne se colle pas à
        # ce que le premier essai avait déjà affiché.
        if sink.shown:
            sink.insert_message("\n")
        sink.done = False
        sink.question_detected = False
        sink.accumulated = ""
        retry_system = _system(
            "Tu dois CONTINUER le texte de l'utilisateur mot après mot, "
            "comme si tu en étais l'auteur. Il est INTERDIT de poser une "
            "question ou de reformuler. Commence immédiatement par les mots "
            "qui suivent naturellement le texte fourni.")
        retry_prompt = ("Voici le texte à continuer :\n\n" + base_text
                        + "\n\nÉcris UNIQUEMENT la suite directe, sans "
                          "aucune introduction.")
        step = llm.step([{"role": "system", "content": retry_system},
                         {"role": "user", "content": retry_prompt}],
                        on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
        if step.error:
            return error_message(step.error)
    sink.finish(step.text, step.streamed)
    return _outcome(sink, _t("preset.done_extend"))


_SUMMARY_SYSTEM = (
    "Tu es un résumeur professionnel. Tu crées des résumés ultra-concis "
    "en utilisant le minimum de mots nécessaire tout en préservant "
    "les informations clés. Tu réponds TOUJOURS dans la même langue "
    "que le texte fourni.")


def _summary_prompt(text):
    return (
        "TEXTE À RÉSUMER :\n" + text + "\n\n"
        "Crée le résumé le plus court possible qui capture les informations essentielles.\n"
        "Sois extrêmement concis — utilise le minimum de mots nécessaire.\n"
        "Ne pose AUCUNE question.\n"
        "Produis UNIQUEMENT le texte du résumé, sans introduction ni explication.\n"
        "IMPORTANT : Réponds dans la MÊME LANGUE que le texte original.\n\n"
        "RÉSUMÉ :\n"
    )


def run_summarize(ctx, shell, user_text, tee, cancel_event=None,
                  dispatcher=None, append_mode=True):
    original, target = _read_target(ctx)
    if target is _NOT_TEXT:
        return _t("sel.no_target")
    if target is None:
        return _summarize_document(ctx, shell, tee, cancel_event)
    _legacy_telemetry(ctx, "SummarizeSelection",
                      {"action": "summarize_selection",
                       "text_length": str(len(original))})

    system_prompt = _system(_SUMMARY_SYSTEM)
    max_tokens = int(shell.get_config("summarize_selection_max_tokens", 15000))
    llm = _text_client(shell, max_tokens)

    sink = text_sink(ctx, append_mode,
                     "\n\n---début-du-résumé---\n", "\n---fin-du-résumé---\n",
                     tee=tee, target=target, original=original,
                     cancel_event=cancel_event,
                     undo_label=_t("preset.undo_summarize"),
                     stop_phrases=STOP_PHRASES,
                     question_patterns=patterns_absent_from(
                         original, EXTEND_QUESTION_PATTERNS))

    step = llm.step([{"role": "system", "content": system_prompt},
                     {"role": "user", "content": _summary_prompt(original)}],
                    on_text_delta=sink.stream_delta,
                    cancel_event=cancel_event)
    if step.error:
        return error_message(step.error)
    sink.finish(step.text, step.streamed)
    return _outcome(sink, _t("preset.done_summarize") if append_mode
                    else _t("preset.done_summarize_replaced"))


_SIMPLIFY_SYSTEM = (
    "Tu es un expert en langage simplifié. Tu réécris les textes complexes "
    "dans un langage clair et simple accessible à tous. Tu utilises TOUJOURS "
    "la même langue que le texte fourni. Tu utilises des phrases courtes "
    "et des mots courants.")


def _simplify_system(shell):
    configured = str(shell.get_config("simplify_selection_system_prompt", "") or "").strip()
    return _system((configured + " " + _SIMPLIFY_SYSTEM) if configured else _SIMPLIFY_SYSTEM)


def run_simplify(ctx, shell, user_text, tee, cancel_event=None,
                 dispatcher=None, append_mode=True):
    original, target = _read_target(ctx)
    if target is _NOT_TEXT:
        return _t("sel.no_target")
    if target is None:
        return _simplify_document(ctx, shell, tee, cancel_event, append_mode)
    _legacy_telemetry(ctx, "SimplifySelection",
                      {"action": "simplify_selection",
                       "text_length": str(len(original))})

    prompt = (
        "TEXTE À REFORMULER :\n" + original + "\n\n"
        "Réécris ce texte dans un langage clair et simple compréhensible par tous.\n"
        "Utilise :\n- Des phrases courtes\n- Des mots courants (évite le jargon "
        "et les termes techniques)\n- La voix active\n- Des exemples concrets "
        "quand c'est possible\n\n"
        "RÈGLES :\n- Garde la MÊME LANGUE que le texte original\n"
        "- Ne traduis PAS dans une autre langue\n- Ne pose AUCUNE question\n"
        "- N'ajoute AUCUNE explication\n- Produis UNIQUEMENT le texte reformulé\n\n"
        "VERSION REFORMULÉE :\n"
    )
    max_tokens = len(original) + int(shell.get_config("simplify_selection_max_tokens", 15000))
    llm = _text_client(shell, max_tokens)

    sink = text_sink(ctx, append_mode,
                     "\n\n---reformulation-du-texte---\n",
                     "\n---fin-de-reformulation---\n", tee=tee,
                     target=target, original=original, cancel_event=cancel_event,
                     undo_label=_t("preset.undo_simplify"),
                     question_patterns=patterns_absent_from(
                         original, SIMPLIFY_QUESTION_PATTERNS),
                     stop_phrases=STOP_PHRASES)

    step = llm.step([{"role": "system", "content": _simplify_system(shell)},
                     {"role": "user", "content": prompt}],
                    on_text_delta=sink.stream_delta,
                    cancel_event=cancel_event)
    if step.error:
        return error_message(step.error)
    sink.finish(step.text, step.streamed)
    return _outcome(sink, _t("preset.done_simplify") if append_mode
                    else _t("preset.done_simplify_replaced"))


_RESIZE_SYSTEM = (
    "Tu réécris des textes en ajustant leur longueur sans en changer le "
    "sens ni la langue. Tu réponds uniquement avec le texte réécrit.")


def _run_resize(ctx, shell, ratio, undo_label, tee, cancel_event=None,
                append_mode=False):
    original, target_range = _read_target(ctx)
    if target_range is _NOT_TEXT:
        return _t("sel.no_target")
    if target_range is None:
        return _resize_document(ctx, shell, ratio, undo_label, tee, cancel_event,
                                append_mode)
    _legacy_telemetry(ctx, "ResizeSelection",
                      {"action": "resize_selection",
                       "text_length": str(len(original))})

    word_count = len(original.split())
    target = max(1, int(round(word_count * ratio)))
    goal = "plus courte" if ratio < 1 else "plus longue"
    prompt = (
        f"TEXTE ({word_count} mots) :\n{original}\n\n"
        f"Réécris ce texte en une version {goal} d'environ {target} mots.\n"
        "Conserve le sens, le ton et la MÊME LANGUE que l'original.\n"
        "Produis UNIQUEMENT le texte réécrit, sans introduction ni commentaire."
    )
    llm = _text_client(shell, max(1000, target * 4))

    sink = text_sink(ctx, append_mode,
                     "\n\n---début-du-texte-ajusté---\n",
                     "\n---fin-du-texte-ajusté---\n", tee=tee,
                     target=target_range, original=original,
                     cancel_event=cancel_event, undo_label=undo_label,
                     question_patterns=patterns_absent_from(
                         original, EXTEND_QUESTION_PATTERNS))
    step = llm.step([{"role": "system", "content": _system(_RESIZE_SYSTEM)},
                     {"role": "user", "content": prompt}],
                    on_text_delta=sink.stream_delta,
                    cancel_event=cancel_event)
    if step.error:
        return error_message(step.error)
    sink.finish(step.text, step.streamed)
    return _outcome(sink, _t("preset.resized", target=target))


def run_shorten(ctx, shell, user_text, tee, cancel_event=None,
                dispatcher=None, append_mode=False):
    return _run_resize(ctx, shell, 0.65, _t("preset.undo_shorten"), tee, cancel_event,
                       append_mode=append_mode)


def run_lengthen(ctx, shell, user_text, tee, cancel_event=None,
                 dispatcher=None, append_mode=False):
    return _run_resize(ctx, shell, 1.4, _t("preset.undo_lengthen"), tee, cancel_event,
                       append_mode=append_mode)


# Sans sélection, les actions Writer portent sur tout le document : le modèle
# reçoit chaque section à part, et le résultat n'est écrit qu'une fois la
# dernière réponse reçue (voir whole_document).

def _read_document(ctx, span, action):
    """Tout le document, lu sur le thread principal avant l'appel au modèle."""
    snapshot = ctx.on_main(lambda: whole_document.read(ctx))
    _legacy_telemetry(ctx, span, {"action": action,
                                  "text_length": str(len(snapshot.text))})
    return snapshot


def _write_document(ctx, snapshot, result, apply, undo_label, cancel_event,
                    done_message):
    """Bilan d'une action sur tout le document : `apply` n'écrit que si le
    modèle a tout rendu, d'un seul passage sur le thread principal."""
    if result.question:
        return _t("preset.question_asked")
    if result.error:
        return error_message(result.error)
    outcome = ctx.on_main(lambda: whole_document.write(
        ctx, snapshot, apply, undo_label, cancel_event))
    if outcome == "stopped":
        return error_message("cancelled")
    if outcome == "changed":
        return _t("preset.target_changed")
    return done_message


def _write_rewrite(ctx, snapshot, result, append_mode, undo_label, markers,
                   cancel_event, done_message):
    """Écrit les sections réécrites à leur place, ou en un bloc après le
    dernier paragraphe entre les `markers` (début, fin)."""
    if not (result.error or result.question or result.drafts):
        return _t("palette.headings_only")

    def apply():
        if append_mode:
            whole_document.append_block(
                ctx, snapshot, whole_document.rewritten_lines(snapshot, result.drafts),
                *markers)
        else:
            whole_document.replace(ctx, snapshot, result.drafts)

    return _write_document(ctx, snapshot, result, apply, undo_label, cancel_event,
                           done_message)


def _summarize_document(ctx, shell, tee, cancel_event):
    """Le résumé de tout le document, inséré après son dernier paragraphe,
    jamais à sa place."""
    snapshot = _read_document(ctx, "SummarizeSelection", "summarize_selection")
    if snapshot.empty:
        return _t("palette.empty_document")
    model = whole_document.Model(
        _text_client(shell, int(shell.get_config("summarize_selection_max_tokens", 15000))),
        _system(_SUMMARY_SYSTEM), cancel_event=cancel_event,
        question_patterns=patterns_absent_from(snapshot.text, EXTEND_QUESTION_PATTERNS),
        stop_phrases=STOP_PHRASES)
    result = whole_document.summarize(model, snapshot, _summary_prompt,
                                      whole_document.max_chars(shell), tee)
    lines = [(line.strip(), "") for line in result.text.splitlines() if line.strip()]
    return _write_document(
        ctx, snapshot, result,
        lambda: whole_document.append_block(ctx, snapshot, lines,
                                            "---début-du-résumé---", "---fin-du-résumé---"),
        _t("preset.undo_summarize"), cancel_event, _t("preset.done_summarize_document"))


_SIMPLIFY_INSTRUCTION = (
    "Réécris ce texte dans un langage clair et simple compréhensible par tous : "
    "phrases courtes, mots courants (évite le jargon et les termes techniques), "
    "voix active, exemples concrets quand c'est possible. Garde la MÊME LANGUE "
    "que le texte original, ne traduis pas.")


def _simplify_document(ctx, shell, tee, cancel_event, append_mode):
    snapshot = _read_document(ctx, "SimplifySelection", "simplify_selection")
    if snapshot.empty:
        return _t("palette.empty_document")
    limit = whole_document.max_chars(shell)
    model = whole_document.Model(
        _text_client(shell, limit + int(shell.get_config("simplify_selection_max_tokens", 15000))),
        _simplify_system(shell), cancel_event=cancel_event,
        question_patterns=patterns_absent_from(snapshot.text, SIMPLIFY_QUESTION_PATTERNS),
        stop_phrases=STOP_PHRASES)
    result = whole_document.rewrite(model, snapshot, lambda _originals: _SIMPLIFY_INSTRUCTION,
                                    limit, tee)
    done = (_t("preset.done_simplify_document") if append_mode
            else _t("preset.done_simplify_document_replaced"))
    return _write_rewrite(ctx, snapshot, result, append_mode, _t("preset.undo_simplify"),
                          ("---reformulation-du-texte---", "---fin-de-reformulation---"),
                          cancel_event, done)


def _resize_document(ctx, shell, ratio, undo_label, tee, cancel_event, append_mode):
    snapshot = _read_document(ctx, "ResizeSelection", "resize_selection")
    if snapshot.empty:
        return _t("palette.empty_document")
    goal = "plus courte" if ratio < 1 else "plus longue"

    def instruction_for(originals):
        words = max(1, int(round(len(" ".join(originals).split()) * ratio)))
        return (f"Réécris ce texte en une version {goal} d'environ {words} mots. "
                "Conserve le sens et le ton.")

    limit = whole_document.max_chars(shell)
    # Le plus long morceau compte environ un mot pour cinq caractères.
    model = whole_document.Model(
        _text_client(shell, max(1000, int(round(limit / 5 * ratio)) * 4)),
        _system(_RESIZE_SYSTEM), cancel_event=cancel_event,
        question_patterns=patterns_absent_from(snapshot.text, EXTEND_QUESTION_PATTERNS))
    result = whole_document.rewrite(model, snapshot, instruction_for, limit, tee)
    body_words = sum(len(text.split()) for text, style in zip(
        snapshot.texts, snapshot.styles, strict=True) if not doc_rewrite.is_heading(style))
    return _write_rewrite(ctx, snapshot, result, append_mode, undo_label,
                          ("---début-du-texte-ajusté---", "---fin-du-texte-ajusté---"),
                          cancel_event,
                          _t("preset.resized", target=max(1, int(round(body_words * ratio)))))


@dataclasses.dataclass
class _TransformPlan:
    """Ce que Transformer lit de la feuille avant le premier appel au modèle."""
    sheet: object
    col_range: range
    row_count: int
    sources: list        # (ligne, texte source) des lignes non vides
    out_col: int
    header: str          # en-tête à poser avec la première écriture, puis ""


def _read_transform(ctx):
    """Feuille, lignes sources et colonne de sortie. Thread principal exclusivement."""
    sheet = ctx.controller.ActiveSheet
    col_range, row_range = calc_tools.used_part(
        sheet, ctx.controller.getSelection().getRangeAddress())
    sources = []
    for row in row_range:
        source_text = _row_text(sheet, col_range, row)
        if source_text:
            sources.append((row, source_text))
    out_col, header = col_range.stop, ""
    if sources:
        out_col, needs_header = calc_tools.find_free_output_column(
            sheet, col_range, row_range)
        header = calc_tools.next_result_header(sheet) if needs_header else ""
    return _TransformPlan(sheet, col_range, len(row_range), sources, out_col, header)


SOURCE_CHANGED = "source modifiée pendant le run"


def _row_text(sheet, col_range, row):
    parts = [sheet.getCellByPosition(col, row).getString() for col in col_range]
    return " | ".join(p for p in parts if p)


def _write_result_row(plan, row, source_text, text):
    """Écrit le résultat d'une ligne, précédé de l'en-tête à la première
    écriture du run, et rend la raison d'un refus. Une ligne qui ne porte
    plus la source lue avant le run n'est pas écrite : le résultat ne lui
    correspond plus. Thread principal exclusivement."""
    sheet = plan.sheet
    if _row_text(sheet, plan.col_range, row) != source_text:
        return SOURCE_CHANGED
    if plan.header:
        try:
            header_cell = sheet.getCellByPosition(plan.out_col, 0)
            header_cell.setString(plan.header)
            calc_tools.apply_dominant_header_style(header_cell, sheet, plan.col_range)
        except Exception:
            pass
        plan.header = ""
    target_cell = sheet.getCellByPosition(plan.out_col, row)
    try:
        target_cell.setPropertyValue("IsTextWrapped", True)
    except Exception:
        pass
    refusal = calc_tools.safe_set_string(target_cell, text)
    try:
        row_obj = sheet.getRows().getByIndex(row)
        row_obj.OptimalHeight = True
        if row_obj.Height > 2500:
            row_obj.Height = 2500
            row_obj.OptimalHeight = False
    except Exception:
        pass
    return refusal


def _fit_result_column(plan):
    """Thread principal exclusivement."""
    try:
        col_obj = plan.sheet.getColumns().getByIndex(plan.out_col)
        col_obj.OptimalWidth = True
        if col_obj.Width < 8000:
            col_obj.Width = 8000
        elif col_obj.Width > 15000:
            col_obj.Width = 15000
    except Exception:
        pass


def _transform_message(out_col, transformed, refused, skipped):
    message = _t("preset.done_transform", count=transformed,
                 col=calc_tools.col_letter(out_col))
    if refused:
        message += " " + _t("preset.refused_transform", count=refused)
    if skipped:
        message += " " + _t("preset.skipped_transform", count=skipped)
    return message


def run_transform(ctx, shell, user_text, tee, cancel_event=None,
                  dispatcher=None, append_mode=True):
    if not user_text.strip():
        return _t("preset.need_transform_input")
    plan = ctx.on_main(lambda: _read_transform(ctx))
    _legacy_telemetry(ctx, "TransformToColumn",
                      {"context": "calc", "rows": str(plan.row_count)})
    if not plan.sources:
        return _t("preset.need_transform_values")

    system_prompt = _system(
        "Tu es un assistant de transformation de données. "
        "Pour chaque valeur fournie, applique l'instruction demandée. "
        "Réponds UNIQUEMENT avec le résultat transformé, sans explication ni "
        "ponctuation autour. N'utilise aucun formatage markdown "
        "(pas de **, *, _, #, `, etc.).")
    llm = _text_client(shell, 2000)

    # Chaque ligne est écrite dès sa réponse complète, dans un seul contexte
    # d'annulation ouvert à la première écriture : un Ctrl+Z retire le run.
    transformed = refused = skipped = 0
    interrupted = ""
    try:
        for row, source_text in plan.sources:
            prompt = ("VALEUR SOURCE :\n" + source_text
                      + "\n\nINSTRUCTION : " + user_text + "\n\nRÉSULTAT :")
            sink = CalcCellSink(ctx, partial(_write_result_row, plan, row, source_text),
                                tee=tee)
            step = llm.step([{"role": "system", "content": system_prompt},
                             {"role": "user", "content": prompt}],
                            on_text_delta=sink.stream_delta,
                            cancel_event=cancel_event)
            if step.error:
                interrupted = step.error
                # Un arrêt n'écrit rien pour la ligne en cours ; une erreur la signale.
                if step.error != "cancelled":
                    ctx.undo_begin(_t("preset.undo_transform"))
                    ctx.on_main(partial(_write_result_row, plan, row, source_text,
                                        "#ERREUR: " + step.error))
                break
            ctx.undo_begin(_t("preset.undo_transform"))
            sink.finish(step.text, step.streamed)
            if sink.written:
                transformed += 1
            elif sink.refusal == SOURCE_CHANGED:
                skipped += 1
            elif sink.refusal:
                refused += 1
        if transformed or refused:
            ctx.on_main(lambda: _fit_result_column(plan))
    finally:
        ctx.undo_end()

    if interrupted:
        return error_message(interrupted)
    return _transform_message(plan.out_col, transformed, refused, skipped)


def _read_table(ctx):
    """Feuille, colonnes et lignes utilisées de la sélection, texte du tableau
    et présence de données. Thread principal exclusivement."""
    sheet = ctx.controller.ActiveSheet
    cols, rows = calc_tools.used_part(
        sheet, ctx.controller.getSelection().getRangeAddress())
    lines = []
    has_data = False
    for r in rows:
        cells = [sheet.getCellByPosition(c, r).getString() for c in cols]
        has_data = has_data or any(cells)
        lines.append(" | ".join(cells))
    return sheet, cols, rows, "\n".join(lines).strip(), has_data


def _write_analysis(sheet, cols, rows, text):
    """Écrit l'analyse dans une cellule fusionnée deux lignes sous le tableau
    et rend la raison d'un refus. Thread principal exclusivement."""
    out_row = rows.stop + 1
    target_cell = sheet.getCellByPosition(cols.start, out_row)
    try:
        merged = sheet.getCellRangeByPosition(
            cols.start, out_row, cols.stop - 1, out_row)
        merged.merge(True)
    except Exception:
        pass
    try:
        target_cell.setPropertyValue("IsTextWrapped", True)
    except Exception:
        pass
    refusal = calc_tools.safe_set_string(target_cell, text)
    try:
        sheet.getRows().getByIndex(out_row).OptimalHeight = True
    except Exception:
        pass
    return refusal


def run_analyze(ctx, shell, user_text, tee, cancel_event=None,
                dispatcher=None, append_mode=True):
    sheet, cols, rows, table_text, has_data = ctx.on_main(lambda: _read_table(ctx))
    _legacy_telemetry(ctx, "AnalyzeRange", {"context": "calc"})
    if not has_data:
        return _t("preset.need_range")

    prompt = (
        "DONNÉES :\n" + table_text + "\n\n"
        "Analyse ces données : tendances, anomalies, points remarquables.\n"
        "Sois concis et factuel. Réponds dans la même langue que les données.\n"
        + (("Question de l'utilisateur : " + user_text + "\n") if user_text.strip() else "")
        + "ANALYSE :"
    )
    system_prompt = _system(
        "Tu es un analyste de données. Tu produis des analyses courtes, "
        "factuelles et actionnables, en texte brut sans markdown.")
    max_tokens = int(shell.get_config("analyze_range_max_tokens", 4000))
    llm = _text_client(shell, max_tokens)

    sink = CalcCellSink(ctx, partial(_write_analysis, sheet, cols, rows), tee=tee)
    step = llm.step([{"role": "system", "content": system_prompt},
                     {"role": "user", "content": prompt}],
                    on_text_delta=sink.stream_delta,
                    cancel_event=cancel_event)
    if step.error:
        return error_message(step.error)
    ctx.undo_begin(_t("preset.undo_analyze"))
    try:
        sink.finish(step.text, step.streamed)
    finally:
        ctx.undo_end()
    if sink.refusal:
        return _t("preset.refused_analyze")
    if not sink.written:
        return error_message("empty_response")
    return _t("preset.done_analyze")


FORMULA_RULES = (
    "Tu génères des formules LibreOffice Calc valides avec les noms de "
    "fonctions ANGLAIS (AVERAGE, SUM, IF, VLOOKUP, COUNTIF, IFERROR, INDEX, "
    "MATCH…). RÈGLES DE SYNTAXE :\n"
    "- POINT-VIRGULE (;) entre les ARGUMENTS : =IF(A1>0;A1;0)\n"
    "- DEUX-POINTS (:) pour les plages contiguës : C2:F2, A1:A100\n"
    "- Plusieurs plages séparées : opérateur + — JAMAIS =SUM(C2:C9;D2:D9) "
    "(Err:522) ; CORRECT : =SUM(C2:D9) ou =SUM(C2:C9)+SUM(D2:D9)\n"
    "- La formule ne doit JAMAIS référencer la cellule cible (référence circulaire)\n"
    "Méthode : lis la structure (calc_get_sheet_overview), applique la formule "
    "avec calc_set_formula ; si le résultat montre une erreur, corrige et "
    "réapplique ; propose calc_fill_formula_down si la sélection couvre "
    "plusieurs lignes. Termine par une explication d'une phrase."
)

_functions_db_cache = None


def _load_functions_db():
    global _functions_db_cache
    if _functions_db_cache is not None:
        return _functions_db_cache
    here = os.path.dirname(__file__)
    path = os.path.join(here, "..", "..", "..", "config", "calc-functions.json")
    try:
        with open(os.path.normpath(path), encoding="utf-8") as fh:
            data = json.load(fh)
        _functions_db_cache = {k: v for k, v in data.items()
                               if not k.startswith("_")}
    except Exception:
        _functions_db_cache = {}
    return _functions_db_cache


def relevant_functions(user_text, limit=8):
    """Top fonctions Calc pertinentes par recouvrement de mots (simplifié)."""
    db = _load_functions_db()
    if not db:
        return ""
    words = set(re.findall(r"[a-zà-ÿ]{3,}", user_text.lower()))
    scored = []
    for name, info in db.items():
        if formula_refusal(str(info.get("ex", ""))):
            continue
        haystack = (name + " " + str(info.get("desc", ""))
                    + " " + str(info.get("cat", ""))).lower()
        score = sum(1 for w in words if w in haystack)
        if name.lower() in user_text.lower():
            score += 3
        if score:
            scored.append((score, name, info))
    scored.sort(key=lambda item: (-item[0], item[1]))
    lines = [f"{name}: {info.get('syn', '')} — {info.get('desc', '')} "
             f"(ex. {info.get('ex', '')})"
             for _, name, info in scored[:limit]]
    return "\n".join(lines)


def _formula_extra(ctx, shell, user_text):
    sheet_context = ctx.on_main(lambda: calc_tools.build_schema_context(
        ctx.controller.ActiveSheet, ctx.controller.getSelection().getRangeAddress()))
    extra = FORMULA_RULES + "\n\nCONTEXTE DE LA FEUILLE :\n" + sheet_context
    functions = relevant_functions(user_text)
    if functions:
        extra += "\n\nFONCTIONS PERTINENTES :\n" + functions
    return extra


PRESETS = [
    Preset(id="summarize", label_key="preset.summarize", apps=("writer",),
           mode="pipeline",
           runner=run_summarize),
    Preset(id="simplify", label_key="preset.simplify", apps=("writer",),
           mode="pipeline",
           runner=run_simplify),
    Preset(id="shorten", label_key="preset.shorten", apps=("writer",),
           mode="pipeline", runner=run_shorten),
    Preset(id="lengthen", label_key="preset.lengthen", apps=("writer",),
           mode="pipeline", runner=run_lengthen),
    Preset(id="transform", label_key="preset.transform", apps=("calc",),
           mode="pipeline", needs_input=True,
           input_hint_key="preset.transform_hint",
           runner=run_transform),
    Preset(id="formula", label_key="preset.formula", apps=("calc",),
           mode="agentic", needs_input=True,
           input_hint_key="preset.formula_hint",
           build_extra=_formula_extra),
    Preset(id="analyze", label_key="preset.analyze", apps=("calc",),
           mode="pipeline", runner=run_analyze),
]


def presets_for(app):
    return [p for p in PRESETS if app in p.apps]
