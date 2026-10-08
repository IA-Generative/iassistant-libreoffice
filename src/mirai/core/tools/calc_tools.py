"""Tools Calc : lectures de contexte (sélection, plage, aperçu de feuille) et
mutations courtes (écriture de cellules, colonne « Résultat IA » non destructive
à droite de la sélection, formule et recopie vers le bas).
"""

import re
from collections import Counter

from ..calc_functions import formula_refusal
from ..text_filters import strip_markdown
from ..tool_calls import ToolResult, ToolSpec

RESULT_HEADER_BASE = "Résultat IA"

_HEADER_STYLE_PROPS = (
    "CharHeight", "CharWeight", "CharPosture", "CharColor",
    "CellBackColor", "CharUnderline", "CharFontName",
    "HoriJustify", "VertJustify", "IsTextWrapped",
)


def col_letter(col):
    """Index de colonne 0-based → lettre (0→A, 25→Z, 26→AA…)."""
    result = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def range_ref(area):
    return (f"{col_letter(area.StartColumn)}{area.StartRow + 1}:"
            f"{col_letter(area.EndColumn)}{area.EndRow + 1}")


def safe_set_string(cell, text):
    """setString sans perdre une apostrophe initiale (marqueur texte Calc)."""
    if text.startswith("'"):
        try:
            cell_text = cell.getText()
            cursor = cell_text.createTextCursor()
            cursor.gotoStart(False)
            cursor.gotoEnd(True)
            cell_text.insertString(cursor, text, True)
            return
        except Exception:
            pass
    cell.setString(text)


def cell_by_ref(sheet, ref):
    """Cellule UNO depuis une référence type 'C4'."""
    return sheet.getCellRangeByName(ref).getCellByPosition(0, 0)


def get_cell_error(cell):
    try:
        value = cell.getString()
        if value and value.startswith("#"):
            return value
    except Exception:
        pass
    return ""


def clean_formula(text):
    """Nettoie la sortie LLM en une formule Calc : retire fences/backticks,
    garde la première ligne commençant par '=', force le '=' initial."""
    text = re.sub(r"```[a-z]*", "", text).replace("`", "")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("="):
            return line
    text = text.strip()
    if text and not text.startswith("="):
        text = "=" + text
    return text


def collect_headers(sheet, num_cols):
    headers = {}
    for c in range(min(num_cols, 26)):
        value = sheet.getCellByPosition(c, 0).getString()
        if value:
            headers[col_letter(c)] = value
    return headers


def find_last_data_row(sheet, num_cols, num_rows):
    for r in range(min(num_rows, 200) - 1, 0, -1):
        for c in range(min(num_cols, 26)):
            if sheet.getCellByPosition(c, r).getString():
                return r
    return 0


def build_schema_context(sheet, area):
    """Description concise de la table pour le contexte formule."""
    target_col, target_row = area.StartColumn, area.StartRow
    lines = [f"Target cell: {col_letter(target_col)}{target_row + 1}"]
    try:
        num_cols = sheet.getColumns().Count
        num_rows = sheet.getRows().Count
        headers = collect_headers(sheet, num_cols)
        if headers:
            joined = ", ".join(f"{k}={v!r}" for k, v in list(headers.items())[:15])
            lines.append(f"Column headers (row 1): {joined}")
        last_row = find_last_data_row(sheet, num_cols, num_rows)
        if last_row > 0 and headers:
            lines.append(f"Data range: row 2 to row {last_row + 1} ({last_row} data rows)")
        if target_row > 0:
            row_values = {}
            for c in range(min(num_cols, 26)):
                letter = col_letter(c)
                if letter in headers:
                    value = sheet.getCellByPosition(c, target_row).getString()
                    if value:
                        row_values[headers[letter]] = value
            if row_values:
                joined = ", ".join(f"{k}={v!r}" for k, v in list(row_values.items())[:10])
                lines.append(f"Current row values: {joined}")
    except Exception:
        pass
    return "\n".join(lines)


def next_result_header(sheet):
    """Prochain libellé libre de la série « Résultat IA », « ×2 », « ×3 »…"""
    try:
        num_cols = sheet.getColumns().Count
    except Exception:
        num_cols = 1024
    existing = set()
    for c in range(min(int(num_cols), 1024)):
        try:
            value = sheet.getCellByPosition(c, 0).getString()
        except Exception:
            break
        if value.startswith(RESULT_HEADER_BASE):
            match = re.match(
                r"^" + re.escape(RESULT_HEADER_BASE) + r"(?:\s+×(\d+))?$", value)
            if match:
                existing.add(int(match.group(1)) if match.group(1) else 1)
    if not existing:
        return RESULT_HEADER_BASE
    n = 2
    while n in existing:
        n += 1
    return f"{RESULT_HEADER_BASE} ×{n}"


def apply_dominant_header_style(target_cell, sheet, col_range):
    """Copie le style d'en-tête modal des colonnes sources."""
    samples = [sheet.getCellByPosition(c, 0) for c in col_range]
    for prop in _HEADER_STYLE_PROPS:
        values = []
        for cell in samples:
            try:
                values.append(cell.getPropertyValue(prop))
            except Exception:
                pass
        if not values:
            continue
        dominant = Counter(str(v) for v in values).most_common(1)[0][0]
        for value in values:
            if str(value) == dominant:
                try:
                    target_cell.setPropertyValue(prop, value)
                except Exception:
                    pass
                break


def find_free_output_column(sheet, col_range, row_range):
    """(index_colonne, besoin_en_tête) : première colonne libre à droite."""
    first = col_range.stop
    check_rows = list(row_range) + [0]
    for offset in range(50):
        candidate = first + offset
        try:
            occupied = any(
                sheet.getCellByPosition(candidate, r).getString()
                for r in check_rows
            )
        except Exception:
            return first, False
        if not occupied:
            return candidate, (candidate != first)
    return first, False


def _selection_area(ctx):
    return ctx.controller.getSelection().getRangeAddress()


def _active_sheet(ctx):
    return ctx.controller.ActiveSheet


def get_selection(ctx, args):
    sheet = _active_sheet(ctx)
    area = _selection_area(ctx)
    n_rows = area.EndRow - area.StartRow + 1
    n_cols = area.EndColumn - area.StartColumn + 1
    headers = collect_headers(sheet, sheet.getColumns().Count)
    sample_rows = []
    for r in range(area.StartRow, min(area.EndRow + 1, area.StartRow + 5)):
        cells = [sheet.getCellByPosition(c, r).getString()
                 for c in range(area.StartColumn, area.EndColumn + 1)]
        sample_rows.append(" | ".join(cells))
    content = (
        f"Sélection : {range_ref(area)} ({n_rows} ligne(s) × {n_cols} colonne(s))\n"
        + (f"En-têtes (ligne 1) : {headers}\n" if headers else "")
        + "Aperçu :\n" + "\n".join(sample_rows)
    )
    return ToolResult(call_id="", ok=True, content=content, data={
        "range_ref": range_ref(area), "n_rows": n_rows, "n_cols": n_cols,
        "headers": headers,
    })


def read_range(ctx, args):
    sheet = _active_sheet(ctx)
    max_cells = int(args.get("max_cells", 500))
    target = sheet.getCellRangeByName(args["range"])
    area = target.getRangeAddress()
    rows = []
    count = 0
    for r in range(area.StartRow, area.EndRow + 1):
        cells = []
        for c in range(area.StartColumn, area.EndColumn + 1):
            count += 1
            if count > max_cells:
                rows.append(" | ".join(cells))
                body = "\n".join(rows)
                return ToolResult(
                    call_id="", ok=True,
                    content=body + f"\n[... tronqué à {max_cells} cellules ...]")
            cells.append(sheet.getCellByPosition(c, r).getString())
        rows.append(" | ".join(cells))
    return ToolResult(call_id="", ok=True, content="\n".join(rows) or "(plage vide)")


def get_sheet_overview(ctx, args):
    sheet = _active_sheet(ctx)
    area = _selection_area(ctx)
    return ToolResult(call_id="", ok=True, content=build_schema_context(sheet, area))


def write_cells(ctx, args):
    sheet = _active_sheet(ctx)
    written = 0
    errors = []
    for entry in args["cells"]:
        ref = str(entry.get("ref", "")).strip()
        value = str(entry.get("value", ""))
        if not ref:
            continue
        try:
            safe_set_string(cell_by_ref(sheet, ref), strip_markdown(value))
            written += 1
        except Exception as exc:
            errors.append(f"{ref}: {exc}")
    content = f"{written} cellule(s) écrite(s)."
    if errors:
        content += " Erreurs : " + " | ".join(errors)
    return ToolResult(call_id="", ok=not errors, content=content,
                      error="; ".join(errors))


def write_result_column(ctx, args):
    """Colonne « Résultat IA » non destructive à droite de la sélection."""
    sheet = _active_sheet(ctx)
    area = _selection_area(ctx)
    values = args["values"]
    header = str(args.get("header", "") or "")
    col_range = range(area.StartColumn, area.EndColumn + 1)
    row_range = range(area.StartRow, area.EndRow + 1)

    out_col, needs_header = find_free_output_column(sheet, col_range, row_range)
    if needs_header:
        try:
            header_cell = sheet.getCellByPosition(out_col, 0)
            header_cell.setString(header or next_result_header(sheet))
            apply_dominant_header_style(header_cell, sheet, col_range)
        except Exception:
            pass

    written = 0
    for offset, value in enumerate(values):
        row = area.StartRow + offset
        if row > area.EndRow:
            break
        target = sheet.getCellByPosition(out_col, row)
        try:
            target.setPropertyValue("IsTextWrapped", True)
        except Exception:
            pass
        safe_set_string(target, strip_markdown(str(value)))
        written += 1
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

    return ToolResult(
        call_id="", ok=True,
        content=f"{written} résultat(s) écrit(s) en colonne {col_letter(out_col)}.",
        data={"output_column": col_letter(out_col)},
    )


def set_formula(ctx, args):
    sheet = _active_sheet(ctx)
    formula = clean_formula(str(args["formula"]))
    refusal = formula_refusal(formula)
    if refusal:
        return ToolResult(call_id="", ok=False, content="", error=(
            f"Formule refusée, {refusal}. N'utilise que des fonctions de calcul, "
            "sans accès réseau, fichier ni référence externe."))
    cell = cell_by_ref(sheet, args["ref"])
    cell.setFormula(formula)
    error_token = get_cell_error(cell)
    value = cell.getString()
    if error_token:
        content = (f"Formule appliquée en {args['ref']} mais la cellule affiche "
                   f"{error_token} — corrige la formule.")
    else:
        content = f"Formule appliquée en {args['ref']}. Valeur affichée : {value!r}"
    return ToolResult(call_id="", ok=not error_token, content=content,
                      data={"error": error_token, "value": value},
                      error=error_token)


def fill_formula_down(ctx, args):
    """Recopie la formule vers le bas en décalant les références de ligne,
    arrêt à la première ligne vide."""
    sheet = _active_sheet(ctx)
    from_ref = str(args["from_ref"]).strip()
    to_row = int(args["to_row"])  # 1-based inclus
    source = sheet.getCellRangeByName(from_ref).getRangeAddress()
    out_col, start_row = source.StartColumn, source.StartRow
    formula = sheet.getCellByPosition(out_col, start_row).getFormula()
    if not formula.startswith("="):
        return ToolResult(call_id="", ok=False, content="",
                          error=f"{from_ref} ne contient pas de formule.")
    refusal = formula_refusal(formula)
    if refusal:
        return ToolResult(call_id="", ok=False, content="",
                          error=f"Formule de {from_ref} non recopiée, {refusal}.")
    num_cols = sheet.getColumns().Count
    filled = 0
    for row_idx in range(start_row + 1, to_row):
        left = range(min(out_col, num_cols))
        right = range(out_col + 1, min(num_cols, out_col + 5))
        check = left if out_col > 0 else right
        if not any(sheet.getCellByPosition(c, row_idx).getString() for c in check):
            break
        delta = row_idx - start_row

        def _shift(match, d=delta):
            return match.group(1) + str(int(match.group(2)) + d)

        adjusted = re.sub(r"(\$?[A-Z]+\$?)(\d+)", _shift, formula)
        try:
            sheet.getCellByPosition(out_col, row_idx).setFormula(adjusted)
            filled += 1
        except Exception:
            pass
    return ToolResult(call_id="", ok=True,
                      content=f"Formule recopiée sur {filled} ligne(s).")


def register(registry):
    registry.register(ToolSpec(
        name="calc_get_selection",
        description="Décrit la sélection courante : plage, dimensions, en-têtes, aperçu.",
        parameters={"type": "object", "properties": {}},
        handler=get_selection, apps=("calc",),
    ))
    registry.register(ToolSpec(
        name="calc_read_range",
        description="Lit une plage (ex. 'A1:C20') en tableau texte délimité par ' | '.",
        parameters={"type": "object", "properties": {
            "range": {"type": "string"},
            "max_cells": {"type": "integer", "default": 500, "minimum": 1, "maximum": 5000},
        }, "required": ["range"]},
        handler=read_range, apps=("calc",),
    ))
    registry.register(ToolSpec(
        name="calc_get_sheet_overview",
        description=("Structure de la feuille : cellule cible, en-têtes ligne 1, "
                     "étendue des données, valeurs de la ligne courante."),
        parameters={"type": "object", "properties": {}},
        handler=get_sheet_overview, apps=("calc",),
    ))
    registry.register(ToolSpec(
        name="calc_write_cells",
        description="Écrit des valeurs texte dans des cellules précises.",
        parameters={"type": "object", "properties": {
            "cells": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"},
                "value": {"type": "string"},
            }, "required": ["ref", "value"]}},
        }, "required": ["cells"]},
        handler=write_cells, apps=("calc",), mutates=True,
    ))
    registry.register(ToolSpec(
        name="calc_write_result_column",
        description=("Écrit une liste de résultats dans la première colonne libre à "
                     "droite de la sélection (non destructif, en-tête automatique)."),
        parameters={"type": "object", "properties": {
            "values": {"type": "array", "items": {"type": "string"}},
            "header": {"type": "string", "default": ""},
        }, "required": ["values"]},
        handler=write_result_column, apps=("calc",), mutates=True,
    ))
    registry.register(ToolSpec(
        name="calc_set_formula",
        description=("Applique une formule Calc dans une cellule (séparateur ';', "
                     "plages 'A1:A10'). Seules les fonctions de calcul sont "
                     "permises : ni réseau, ni fichier, ni référence externe. "
                     "Retourne l'erreur affichée (#…, Err:…) le cas échéant pour "
                     "te permettre de corriger."),
        parameters={"type": "object", "properties": {
            "ref": {"type": "string"},
            "formula": {"type": "string"},
        }, "required": ["ref", "formula"]},
        handler=set_formula, apps=("calc",), mutates=True,
    ))
    registry.register(ToolSpec(
        name="calc_fill_formula_down",
        description=("Recopie la formule d'une cellule vers le bas jusqu'à la ligne "
                     "donnée (1-based, exclue), en décalant les références ; "
                     "s'arrête à la première ligne sans données."),
        parameters={"type": "object", "properties": {
            "from_ref": {"type": "string"},
            "to_row": {"type": "integer", "minimum": 2},
        }, "required": ["from_ref", "to_row"]},
        handler=fill_formula_down, apps=("calc",), mutates=True,
    ))
