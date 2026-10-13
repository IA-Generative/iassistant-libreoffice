"""Tools UNO restants : carte du document, find/replace, plages Calc, fill down."""

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
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_set_formula", {"ref": "C1", "formula": "```\n=VLOOKUP(Z1;A1:B2;2;0)\n```"},
        _calc_ctx(doc))
    assert not result.ok
    assert "#N/A" in result.content
    assert sheet.formulas[(2, 0)] == "=VLOOKUP(Z1;A1:B2;2;0)"   # fences nettoyées


def test_calc_fill_formula_down_stops_at_empty_row():
    sheet = FakeCalcSheet(grid={(0, 1): "10", (0, 2): "20", (0, 3): "30"})
    sheet.formulas[(2, 1)] = "=A2*2"
    doc = FakeCalcDoc(sheet)
    result = _registry().call_tool(
        "calc_fill_formula_down", {"from_ref": "C2", "to_row": 10},
        _calc_ctx(doc))
    assert result.ok
    assert sheet.formulas[(2, 2)] == "=A3*2"
    assert sheet.formulas[(2, 3)] == "=A4*2"
    assert (2, 4) not in sheet.formulas       # ligne 5 vide → arrêt
    assert "2 ligne(s)" in result.content


def test_calc_get_selection_overview():
    sheet = FakeCalcSheet(grid={(0, 0): "Nom", (0, 1): "Alice"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    result = _registry().call_tool("calc_get_selection", {}, _calc_ctx(doc))
    assert result.ok and "A1:A2" in result.content
    assert result.data["n_rows"] == 2
