"""Les fonctions historiques en presets/chips de la palette.

Deux modes :
- "pipeline" : Python pilote, le LLM n'est qu'une fonction texte — iso-
  fonctionnalité stricte avec les actions historiques (marqueurs, stop
  phrases, retry sur question, colonne Résultat IA…), robuste petits modèles.
- "agentic"  : le LLM pilote les tools (edit, formule, analyse libre).

Chaque preset émet le span télémétrie historique avec {"via": "palette"} pour
préserver les tableaux de bord existants.
"""

import dataclasses
import json
import os
import re

from ..i18n import t as _t
from .llm_client import LLMClient
from .prompts import LEGACY_TEXT_SYSTEM
from .sinks import CalcCellSink, WriterInsertSink, WriterReplaceSink
from .text_filters import (
    EXTEND_QUESTION_PATTERNS,
    SIMPLIFY_QUESTION_PATTERNS,
    STOP_PHRASES,
)
from .tools import calc_tools


@dataclasses.dataclass
class Preset:
    id: str
    label_key: str               # cle i18n du libellé de chip (avec pictogramme)
    apps: tuple
    mode: str                     # "pipeline" | "agentic"
    legacy_span: str = ""         # span télémétrie historique
    needs_selection: bool = False
    needs_input: bool = False     # requiert une instruction tapée dans le prompt
    input_hint_key: str = ""
    runner: object = None         # pipeline : fn(ctx, shell, user_text, tee) -> str
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


def _selection_text(ctx):
    try:
        return ctx.controller.getSelection().getByIndex(0).getString()
    except Exception:
        return ""


def _target_selection_text(ctx):
    """Texte cible d'une action directe : la sélection si elle existe, sinon
    le PARAGRAPHE COURANT (identifié sous le curseur, puis sélectionné pour
    que les sinks opèrent dessus). Retourne "" si aucun texte ciblable."""
    try:
        rng = ctx.controller.getSelection().getByIndex(0)
    except Exception:
        return ""
    text = rng.getString()
    if text.strip():
        return text
    try:
        cursor = rng.getText().createTextCursorByRange(rng)
        cursor.gotoStartOfParagraph(False)
        cursor.gotoEndOfParagraph(True)
        paragraph = cursor.getString()
        if not paragraph.strip():
            return ""
        ctx.controller.select(cursor)
        return paragraph
    except Exception:
        return ""


def _text_client(shell, max_tokens):
    return LLMClient(shell, max_tokens=max_tokens)


def text_sink(ctx, append_mode, header, footer, tee=None, **kwargs):
    """Où va le texte produit : à la suite entre marqueurs, ou en remplacement.

    Les deux usages sont légitimes et les utilisateurs ne s'accordent pas :
    remplacer va plus vite, ajouter permet de comparer avant de décider. Le
    choix vient de la case « Ajouter à la suite » de la palette ; ce helper est
    le SEUL endroit qui le traduit en destination, pour qu'aucun preset ne
    puisse l'ignorer — c'est précisément ce qui était arrivé, la case ne
    pilotant plus rien depuis le retrait du preset qui l'utilisait.
    """
    if append_mode:
        return WriterInsertSink(ctx, header, footer, tee=tee, **kwargs)
    return WriterReplaceSink(ctx, tee=tee)


def _system(specific):
    """Système hérité de make_api_request : défaut + spécifique."""
    return LEGACY_TEXT_SYSTEM + " " + specific if specific else LEGACY_TEXT_SYSTEM


# ── Presets pipeline Writer ─────────────────────────────────────────────

def run_extend(ctx, shell, user_text, tee, cancel_event=None,
               dispatcher=None):
    base_text = _target_selection_text(ctx)
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

    sink = WriterInsertSink(
        ctx, "\n\n---début-du-texte-généré---\n", "\n---fin-du-texte-généré---\n",
        question_patterns=EXTEND_QUESTION_PATTERNS, tee=tee)

    ctx.undo_begin(_t("preset.undo_extend"))
    try:
        step = llm.step([{"role": "system", "content": system_prompt},
                         {"role": "user", "content": base_text}],
                        on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
        if step.error:
            from .orchestrator import error_message
            return error_message(step.error)

        if sink.question_detected:
            # Retry unique avec directive renforcée (comportement historique).
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
            if sink.question_detected:
                sink.insert_message(
                    "\n[Le modèle n'a pas pu continuer le texte."
                    " Essayez de sélectionner plus de contexte.]")
        sink.finish(step.text, step.streamed)
        return _t("preset.done_extend")
    finally:
        ctx.undo_end()


def run_summarize(ctx, shell, user_text, tee, cancel_event=None,
                  dispatcher=None, append_mode=True):
    original = _target_selection_text(ctx)
    _legacy_telemetry(ctx, "SummarizeSelection",
                      {"action": "summarize_selection",
                       "text_length": str(len(original))})
    if not original.strip():
        return _t("sel.no_target")

    prompt = (
        "TEXTE À RÉSUMER :\n" + original + "\n\n"
        "Crée le résumé le plus court possible qui capture les informations essentielles.\n"
        "Sois extrêmement concis — utilise le minimum de mots nécessaire.\n"
        "Ne pose AUCUNE question.\n"
        "Produis UNIQUEMENT le texte du résumé, sans introduction ni explication.\n"
        "IMPORTANT : Réponds dans la MÊME LANGUE que le texte original.\n\n"
        "RÉSUMÉ :\n"
    )
    system_prompt = _system(
        "Tu es un résumeur professionnel. Tu crées des résumés ultra-concis "
        "en utilisant le minimum de mots nécessaire tout en préservant "
        "les informations clés. Tu réponds TOUJOURS dans la même langue "
        "que le texte fourni.")
    max_tokens = int(shell.get_config("summarize_selection_max_tokens", 15000))
    llm = _text_client(shell, max_tokens)

    sink = text_sink(ctx, append_mode,
                     "\n\n---début-du-résumé---\n", "\n---fin-du-résumé---\n",
                     tee=tee, stop_phrases=STOP_PHRASES)

    ctx.undo_begin(_t("preset.undo_summarize"))
    try:
        step = llm.step([{"role": "system", "content": system_prompt},
                         {"role": "user", "content": prompt}],
                        on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
        if step.error:
            from .orchestrator import error_message
            return error_message(step.error)
        sink.finish(step.text, step.streamed)
        return _t("preset.done_summarize")
    finally:
        ctx.undo_end()


def run_simplify(ctx, shell, user_text, tee, cancel_event=None,
                 dispatcher=None, append_mode=True):
    original = _target_selection_text(ctx)
    _legacy_telemetry(ctx, "SimplifySelection",
                      {"action": "simplify_selection",
                       "text_length": str(len(original))})
    if not original.strip():
        return _t("sel.no_target")

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
    base_system = (
        "Tu es un expert en langage simplifié. Tu réécris les textes complexes "
        "dans un langage clair et simple accessible à tous. Tu utilises TOUJOURS "
        "la même langue que le texte fourni. Tu utilises des phrases courtes "
        "et des mots courants.")
    configured = str(shell.get_config("simplify_selection_system_prompt", "") or "").strip()
    system_prompt = _system((configured + " " + base_system) if configured else base_system)
    max_tokens = len(original) + int(shell.get_config("simplify_selection_max_tokens", 15000))
    llm = _text_client(shell, max_tokens)

    def _on_question(active_sink):
        # Historique : signaler l'échec sans toucher au texte déjà inséré.
        active_sink.insert_message(
            "[Le modèle a posé une question. Veuillez réessayer.]")

    sink = text_sink(ctx, append_mode,
                     "\n\n---reformulation-du-texte---\n",
                     "\n---fin-de-reformulation---\n", tee=tee,
                     question_patterns=SIMPLIFY_QUESTION_PATTERNS,
                     on_question=_on_question, stop_phrases=STOP_PHRASES)

    ctx.undo_begin(_t("preset.undo_simplify"))
    try:
        step = llm.step([{"role": "system", "content": system_prompt},
                         {"role": "user", "content": prompt}],
                        on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
        if step.error:
            from .orchestrator import error_message
            return error_message(step.error)
        sink.finish(step.text, step.streamed)
        return _t("preset.done_simplify")
    finally:
        ctx.undo_end()


def _run_resize(ctx, shell, ratio, undo_label, tee, cancel_event=None,
                append_mode=False):
    original = _target_selection_text(ctx)
    _legacy_telemetry(ctx, "ResizeSelection",
                      {"action": "resize_selection",
                       "text_length": str(len(original))})
    if not original.strip():
        return _t("sel.no_target")

    word_count = len(original.split())
    target = max(1, int(round(word_count * ratio)))
    goal = "plus courte" if ratio < 1 else "plus longue"
    prompt = (
        f"TEXTE ({word_count} mots) :\n{original}\n\n"
        f"Réécris ce texte en une version {goal} d'environ {target} mots.\n"
        "Conserve le sens, le ton et la MÊME LANGUE que l'original.\n"
        "Produis UNIQUEMENT le texte réécrit, sans introduction ni commentaire."
    )
    system_prompt = _system(
        "Tu réécris des textes en ajustant leur longueur sans en changer le "
        "sens ni la langue. Tu réponds uniquement avec le texte réécrit.")
    llm = _text_client(shell, max(1000, target * 4))

    sink = text_sink(ctx, append_mode,
                     "\n\n---début-du-texte-ajusté---\n",
                     "\n---fin-du-texte-ajusté---\n", tee=tee)
    ctx.undo_begin(undo_label)
    try:
        step = llm.step([{"role": "system", "content": system_prompt},
                         {"role": "user", "content": prompt}],
                        on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
        if step.error:
            from .orchestrator import error_message
            return error_message(step.error)
        sink.finish(step.text, step.streamed)
        return _t("preset.resized", target=target)
    finally:
        ctx.undo_end()


def run_shorten(ctx, shell, user_text, tee, cancel_event=None,
                dispatcher=None, append_mode=False):
    return _run_resize(ctx, shell, 0.65, _t("preset.undo_shorten"), tee, cancel_event,
                       append_mode=append_mode)


def run_lengthen(ctx, shell, user_text, tee, cancel_event=None,
                 dispatcher=None, append_mode=False):
    return _run_resize(ctx, shell, 1.4, _t("preset.undo_lengthen"), tee, cancel_event,
                       append_mode=append_mode)


# ── Presets pipeline Calc ───────────────────────────────────────────────

def run_transform(ctx, shell, user_text, tee, cancel_event=None,
                  dispatcher=None, append_mode=True):
    sheet = ctx.controller.ActiveSheet
    area = ctx.controller.getSelection().getRangeAddress()
    col_range = range(area.StartColumn, area.EndColumn + 1)
    row_range = range(area.StartRow, area.EndRow + 1)
    _legacy_telemetry(ctx, "TransformToColumn",
                      {"context": "calc", "rows": str(len(row_range))})
    if not user_text.strip():
        return _t("preset.need_transform_input")

    system_prompt = _system(
        "Tu es un assistant de transformation de données. "
        "Pour chaque valeur fournie, applique l'instruction demandée. "
        "Réponds UNIQUEMENT avec le résultat transformé, sans explication ni "
        "ponctuation autour. N'utilise aucun formatage markdown "
        "(pas de **, *, _, #, `, etc.).")
    llm = _text_client(shell, 2000)

    out_col, needs_header = calc_tools.find_free_output_column(
        sheet, col_range, row_range)
    if needs_header:
        try:
            header_cell = sheet.getCellByPosition(out_col, 0)
            header_cell.setString(calc_tools.next_result_header(sheet))
            calc_tools.apply_dominant_header_style(header_cell, sheet, col_range)
        except Exception:
            pass

    transformed = 0
    ctx.undo_begin(_t("preset.undo_transform"))
    try:
        for row in row_range:
            parts = [sheet.getCellByPosition(col, row).getString()
                     for col in col_range]
            source_text = " | ".join(p for p in parts if p)
            if not source_text:
                continue
            target_cell = sheet.getCellByPosition(out_col, row)
            target_cell.setString("")
            try:
                target_cell.setPropertyValue("IsTextWrapped", True)
            except Exception:
                pass
            prompt = ("VALEUR SOURCE :\n" + source_text
                      + "\n\nINSTRUCTION : " + user_text + "\n\nRÉSULTAT :")
            sink = CalcCellSink(target_cell, tee=tee)
            step = llm.step([{"role": "system", "content": system_prompt},
                             {"role": "user", "content": prompt}],
                            on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
            if step.error:
                from .orchestrator import error_message
                target_cell.setString("#ERREUR: " + step.error)
                return error_message(step.error)
            sink.finish(step.text, step.streamed)
            transformed += 1
            try:
                row_obj = sheet.getRows().getByIndex(row)
                row_obj.OptimalHeight = True
                if row_obj.Height > 2500:
                    row_obj.Height = 2500
                    row_obj.OptimalHeight = False
            except Exception:
                pass

        try:
            col_obj = sheet.getColumns().getByIndex(out_col)
            col_obj.OptimalWidth = True
            if col_obj.Width < 8000:
                col_obj.Width = 8000
            elif col_obj.Width > 15000:
                col_obj.Width = 15000
        except Exception:
            pass
        return _t("preset.done_transform", count=transformed,
                  col=calc_tools.col_letter(out_col))
    finally:
        ctx.undo_end()


def run_analyze(ctx, shell, user_text, tee, cancel_event=None,
                dispatcher=None, append_mode=True):
    sheet = ctx.controller.ActiveSheet
    area = ctx.controller.getSelection().getRangeAddress()
    _legacy_telemetry(ctx, "AnalyzeRange", {"context": "calc"})

    rows = []
    has_data = False
    for r in range(area.StartRow, area.EndRow + 1):
        cells = [sheet.getCellByPosition(c, r).getString()
                 for c in range(area.StartColumn, area.EndColumn + 1)]
        has_data = has_data or any(cells)
        rows.append(" | ".join(cells))
    table_text = "\n".join(rows).strip()
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

    out_row = area.EndRow + 2
    target_cell = sheet.getCellByPosition(area.StartColumn, out_row)
    ctx.undo_begin(_t("preset.undo_analyze"))
    try:
        try:
            merged = sheet.getCellRangeByPosition(
                area.StartColumn, out_row, area.EndColumn, out_row)
            merged.merge(True)
        except Exception:
            pass
        try:
            target_cell.setPropertyValue("IsTextWrapped", True)
        except Exception:
            pass
        sink = CalcCellSink(target_cell, tee=tee)
        step = llm.step([{"role": "system", "content": system_prompt},
                         {"role": "user", "content": prompt}],
                        on_text_delta=sink.stream_delta,
                        cancel_event=cancel_event)
        if step.error:
            from .orchestrator import error_message
            return error_message(step.error)
        sink.finish(step.text, step.streamed)
        try:
            sheet.getRows().getByIndex(out_row).OptimalHeight = True
        except Exception:
            pass
        return _t("preset.done_analyze")
    finally:
        ctx.undo_end()


# ── Presets agentiques ──────────────────────────────────────────────────

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
    candidates = [
        os.path.join(here, "..", "..", "..", "config", "calc-functions.json"),
        os.path.join(here, "..", "..", "config", "calc-functions.json"),
    ]
    for candidate in candidates:
        try:
            with open(os.path.normpath(candidate), encoding="utf-8") as fh:
                data = json.load(fh)
            _functions_db_cache = {k: v for k, v in data.items()
                                   if not k.startswith("_")}
            return _functions_db_cache
        except Exception:
            continue
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
    sheet = ctx.controller.ActiveSheet
    area = ctx.controller.getSelection().getRangeAddress()
    extra = FORMULA_RULES + "\n\nCONTEXTE DE LA FEUILLE :\n" \
        + calc_tools.build_schema_context(sheet, area)
    functions = relevant_functions(user_text)
    if functions:
        extra += "\n\nFONCTIONS PERTINENTES :\n" + functions
    return extra


def _edit_extra(ctx, shell, user_text):
    selection = _selection_text(ctx)
    if selection.strip():
        return (
            "L'utilisateur demande une modification du texte SÉLECTIONNÉ. "
            "Lis-le avec writer_get_selection si besoin. Réponds directement "
            "avec la VERSION MODIFIÉE complète du texte sélectionné (texte "
            "brut, sans commentaire) : elle remplacera la sélection. "
            "N'utilise writer_find_replace que pour des retouches ponctuelles."
        )
    return (
        "La sélection est vide : la modification porte sur le DOCUMENT ENTIER. "
        "Lis-le avec writer_get_document_map, puis applique des remplacements "
        "ciblés avec writer_find_replace (texte exact). Termine par un court "
        "récapitulatif des changements."
    )


def _edit_prompt(user_text):
    return "INSTRUCTIONS DE MODIFICATION :\n" + user_text


PRESETS = [
    Preset(id="summarize", label_key="preset.summarize", apps=("writer",),
           mode="pipeline",
           legacy_span="SummarizeSelection", needs_selection=True,
           runner=run_summarize),
    Preset(id="simplify", label_key="preset.simplify", apps=("writer",),
           mode="pipeline",
           legacy_span="SimplifySelection", needs_selection=True,
           runner=run_simplify),
    Preset(id="shorten", label_key="preset.shorten", apps=("writer",),
           mode="pipeline",
           legacy_span="ResizeSelection", needs_selection=True, runner=run_shorten),
    Preset(id="lengthen", label_key="preset.lengthen", apps=("writer",),
           mode="pipeline",
           legacy_span="ResizeSelection", needs_selection=True, runner=run_lengthen),
    Preset(id="transform", label_key="preset.transform", apps=("calc",),
           mode="pipeline",
           legacy_span="TransformToColumn", needs_selection=True, needs_input=True,
           input_hint_key="preset.transform_hint",
           runner=run_transform),
    Preset(id="formula", label_key="preset.formula", apps=("calc",),
           mode="agentic",
           legacy_span="GenerateFormula", needs_input=True,
           input_hint_key="preset.formula_hint",
           build_extra=_formula_extra),
    Preset(id="analyze", label_key="preset.analyze", apps=("calc",),
           mode="pipeline",
           legacy_span="AnalyzeRange", needs_selection=True, runner=run_analyze),
]


def presets_for(app):
    return [p for p in PRESETS if app in p.apps]


def get_preset(preset_id):
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    return None
