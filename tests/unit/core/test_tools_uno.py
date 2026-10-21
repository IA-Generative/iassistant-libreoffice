"""Tools UNO restants : carte du document, find/replace, plages Calc, fill down."""

import pytest

from src.mirai.core.context import ToolContext
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.tools import register_all
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet, FakeWriterDoc
from tests.stubs.fake_shell import FakeShell


def _writer_ctx(doc):
    return ToolContext(doc, doc.controller, "writer", FakeShell())


def _calc_ctx(doc):
    return ToolContext(doc, doc.controller, "calc", FakeShell())


def _registry():
    return register_all(ToolRegistry())


def test_writer_get_selection():
    doc = FakeWriterDoc(selection_text="texte choisi")
    result = _registry().call_tool("writer_get_selection", {}, _writer_ctx(doc))
    assert result.ok and "texte choisi" in result.content
    assert result.data["char_count"] == 12


def test_writer_get_selection_empty():
    doc = FakeWriterDoc(selection_text="  ")
    result = _registry().call_tool("writer_get_selection", {}, _writer_ctx(doc))
    assert result.ok and "vide" in result.content


def test_writer_document_map_numbered_and_truncated():
    doc = FakeWriterDoc(paragraphs=["Premier paragraphe.", "", "Troisième."])
    result = _registry().call_tool("writer_get_document_map", {}, _writer_ctx(doc))
    assert "[P1] Premier paragraphe." in result.content
    assert "[P2] (vide)" in result.content
    assert "[P3] Troisième." in result.content

    long_doc = FakeWriterDoc(paragraphs=["x" * 400 for _ in range(10)])
    result = _registry().call_tool("writer_get_document_map",
                                   {"max_chars": 900}, _writer_ctx(long_doc))
    assert result.data["truncated"] is True
    assert "tronqué" in result.content


@pytest.mark.parametrize("paragraph_count, config, args", [
    (200, {}, {}),
    (200, {}, {"max_chars": 20000}),
    (200, {"tool_result_max_chars": 1500}, {}),
    (56, {}, {}),
], ids=["defaut", "max_chars_au_dela_du_plafond", "plafond_configure_bas",
        "tient_sans_ses_reperes"])
def test_writer_document_map_large_document_keeps_its_end(paragraph_count, config, args):
    """Sur un grand document, le modèle reçoit encore la fin de la carte.

    Le registre plafonne chaque résultat à `tool_result_max_chars` : la carte
    doit tenir dessous avec ses repères de fin, sans quoi le modèle perd le
    rappel d'étape suivante et ne sait plus jusqu'où il a lu.
    """
    def map_of(paragraphs):
        doc = FakeWriterDoc(paragraphs=paragraphs)
        ctx = ToolContext(doc, doc.controller, "writer", FakeShell(config))
        return _registry().call_tool("writer_get_document_map", args, ctx)

    reminder = map_of(["Court."]).content.splitlines()[-1]
    paragraphs = [f"Paragraphe {i} " + "x" * 85 for i in range(1, paragraph_count + 1)]

    result = map_of(paragraphs)

    assert result.content.endswith(reminder)
    assert result.data["truncated"] is True
    assert "FIN DU DOCUMENT" not in result.content
    assert "tronqué" in result.content


def test_writer_insert_text_after_selection():
    doc = FakeWriterDoc(selection_text="ancre")
    result = _registry().call_tool("writer_insert_text", {"text": "ajout"},
                                   _writer_ctx(doc))
    assert result.ok and doc.inserted == "ajout"


def test_writer_replace_selection():
    doc = FakeWriterDoc(selection_text="ancien")
    ctx = _writer_ctx(doc)
    result = _registry().call_tool("writer_replace_selection",
                                   {"text": "nouveau"}, ctx)
    assert result.ok and doc.replaced == "nouveau"
    assert ctx._undo_open  # tool mutant → contexte undo ouvert (fermé par le run)


def test_writer_find_replace_counts_and_misses():
    doc = FakeWriterDoc(paragraphs=["le chat dort", "le chien court"])
    result = _registry().call_tool(
        "writer_find_replace",
        {"pairs": [{"find": "chat", "replace": "lynx"},
                   {"find": "absent", "replace": "x"}]},
        _writer_ctx(doc))
    assert result.ok
    assert doc.paragraphs[0] == "le lynx dort"
    assert result.data["applied"] == 1
    assert result.data["not_found"] == ["absent"]


def test_calc_read_range():
    sheet = FakeCalcSheet(grid={(0, 0): "a", (1, 0): "b", (0, 1): "c"})
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool("calc_read_range", {"range": "A1:B2"},
                                   _calc_ctx(doc))
    assert result.ok
    assert result.content == "a | b\nc | "


def test_calc_write_cells():
    sheet = FakeCalcSheet()
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_write_cells",
        {"cells": [{"ref": "B2", "value": "**gras**"}]}, _calc_ctx(doc))
    assert result.ok
    assert sheet.grid[(1, 1)] == "gras"       # markdown nettoyé


def test_calc_set_formula_reports_error_token():
    sheet = FakeCalcSheet()
    sheet.formula_results["=VLOOKUP(Z1;A1:B2;2;0)"] = "#N/A"
    sheet.formula_errors["=VLOOKUP(Z1;A1:B2;2;0)"] = 32767
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "```\n=VLOOKUP(Z1;A1:B2;2;0)\n```"},
        _calc_ctx(doc))
    assert not result.ok
    assert "#N/A" in result.content
    assert sheet.formulas[(2, 0)] == "=VLOOKUP(Z1;A1:B2;2;0)"   # fences nettoyées


def test_calc_set_formula_reports_calc_error_code():
    sheet = FakeCalcSheet()
    sheet.formula_results["=SQRT(-1)"] = "Err:502"
    sheet.formula_errors["=SQRT(-1)"] = 502
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "=SQRT(-1)"}, _calc_ctx(doc))
    assert not result.ok
    assert result.error == "Err:502"


@pytest.mark.parametrize("formula, shown", [('="#tag"', "#tag"), ('="Err:1"', "Err:1")])
def test_calc_set_formula_accepts_a_text_result_that_looks_like_an_error(formula, shown):
    sheet = FakeCalcSheet()
    sheet.formula_results[formula] = shown
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": formula}, _calc_ctx(doc))
    assert result.ok
    assert shown in result.content


def _fill_down(sheet, to_row):
    return _registry().call_tool(
        "calc_fill_formula_down", {"from_ref": "C2", "to_row": to_row},
        _calc_ctx(FakeCalcDoc(sheet)))


def test_calc_fill_formula_down_stops_at_empty_row():
    sheet = FakeCalcSheet(grid={(0, 1): "10", (0, 2): "20", (0, 3): "30"})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 10)
    assert result.ok
    assert sorted(sheet.formulas) == [(2, 1), (2, 2), (2, 3)]   # ligne 5 vide
    assert "2 ligne(s)" in result.content


def test_calc_fill_formula_down_includes_to_row():
    sheet = FakeCalcSheet(grid={(0, r): str(r) for r in range(1, 9)})
    sheet.formulas[(2, 1)] = "=A2*2"
    result = _fill_down(sheet, 4)
    assert result.ok
    assert sorted(sheet.formulas) == [(2, 1), (2, 2), (2, 3)]   # jusqu'à C4


@pytest.mark.parametrize("formula", [
    "=LOG10(A2)",
    "=DAYS360(A2;B2)",
    "=ATAN2(A2;B2)",
    "=COM.SUN.STAR.SHEET.ADDIN.ANALYSIS.GETDEC2HEX(A2)",
    '=IF(A2="Q1";1;0)',
    "=A2*$B$1",
])
def test_calc_fill_formula_down_lets_calc_adjust_the_copy(formula):
    # Calc décale les références relatives de la cellule copiée et laisse
    # noms de fonction, littéraux et lignes ancrées par $ intacts. Le tool
    # n'écrit donc aucune formule de son cru.
    sheet = FakeCalcSheet(grid={(0, 1): "1", (0, 2): "2"})
    sheet.formulas[(2, 1)] = formula
    result = _fill_down(sheet, 3)
    assert result.ok
    assert set(sheet.formulas.values()) == {formula}
    assert sheet.copies == [((2, 1), (2, 2))]


@pytest.mark.parametrize("to_row, grid, protected", [
    (2, {(0, 1): "1", (0, 2): "2"}, False),     # to_row = ligne source
    (5, {(0, 1): "1"}, False),                  # ligne 3 sans données
    (5, {(0, 1): "1", (0, 2): "2"}, True),      # Calc refuse la copie
])
def test_calc_fill_formula_down_fails_when_no_row_is_copied(to_row, grid, protected):
    sheet = FakeCalcSheet(grid=grid)
    sheet.formulas[(2, 1)] = "=A2*2"
    sheet.protected = protected
    result = _fill_down(sheet, to_row)
    assert not result.ok
    assert "C2" in result.error
    assert list(sheet.formulas) == [(2, 1)]


def test_calc_get_selection_overview():
    sheet = FakeCalcSheet(grid={(0, 0): "Nom", (0, 1): "Alice"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    result = _registry().call_tool("calc_get_selection", {}, _calc_ctx(doc))
    assert result.ok and "A1:A2" in result.content
    assert result.data["n_rows"] == 2
