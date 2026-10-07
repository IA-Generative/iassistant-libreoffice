"""Golden iso-fonctionnels Calc : transform (colonne Résultat IA), analyse
(cellule fusionnée sous la sélection), formule agentique (via orchestrateur).
Assertions portées de menu_actions/calc.py."""

from src.mirai.core import presets
from src.mirai.core.context import ToolContext
from src.mirai.core.llm_client import LLMClient
from src.mirai.core.orchestrator import Orchestrator
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.sinks import PaletteSink
from src.mirai.core.tools import register_all
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet
from tests.stubs.fake_shell import (
    FakeShell,
    FakeSSEResponse,
    native_tool_call_chunks,
    text_chunks,
)


def _ctx(doc, shell):
    return ToolContext(doc, doc.controller, "calc", shell)


def test_transform_writes_adjacent_free_column():
    sheet = FakeCalcSheet(grid={(0, 0): "pomme", (0, 1): "poire", (0, 2): "prune"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A3")
    shell = FakeShell(responses=[
        FakeSSEResponse(text_chunks("APPLE")),
        FakeSSEResponse(text_chunks("PEAR")),
        FakeSSEResponse(text_chunks("PLUM")),
    ])
    message = presets.run_transform(_ctx(doc, shell), shell,
                                    "traduire en anglais", None)
    assert sheet.grid[(1, 0)] == "APPLE"
    assert sheet.grid[(1, 1)] == "PEAR"
    assert sheet.grid[(1, 2)] == "PLUM"
    assert "3 ligne(s)" in message
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert prompt.startswith("VALEUR SOURCE :")
    assert "INSTRUCTION : traduire en anglais" in prompt
    spans = dict(shell.telemetry_events)
    assert spans["TransformToColumn"]["via"] == "palette"


def test_transform_occupied_column_gets_result_header():
    sheet = FakeCalcSheet(grid={(0, 1): "a", (1, 1): "déjà pris",
                                (0, 0): "Nom", (1, 0): "Autre"})
    doc = FakeCalcDoc(sheet, selection_ref="A2:A2")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("X"))])
    presets.run_transform(_ctx(doc, shell), shell, "majuscules", None)
    # colonne B occupée → C reçoit l'en-tête « Résultat IA » et le résultat
    assert sheet.grid[(2, 0)] == "Résultat IA"
    assert sheet.grid[(2, 1)] == "X"


def test_transform_skips_empty_rows():
    sheet = FakeCalcSheet(grid={(0, 0): "seul"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A3")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("SEUL"))])
    message = presets.run_transform(_ctx(doc, shell), shell, "majuscule", None)
    assert "1 ligne(s)" in message
    assert (1, 1) not in sheet.grid and (1, 2) not in sheet.grid


def test_transform_without_instruction():
    sheet = FakeCalcSheet(grid={(0, 0): "x"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A1")
    shell = FakeShell()
    message = presets.run_transform(_ctx(doc, shell), shell, "  ", None)
    assert "instruction" in message.lower()


def test_analyze_writes_merged_cell_below_selection():
    sheet = FakeCalcSheet(grid={(0, 0): "Ville", (1, 0): "Ventes",
                                (0, 1): "Paris", (1, 1): "100",
                                (0, 2): "Lyon", (1, 2): "60"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:B3")
    shell = FakeShell(responses=[FakeSSEResponse(
        text_chunks("**Tendance** : Paris domine."))])
    message = presets.run_analyze(_ctx(doc, shell), shell, "", None)
    # 2 lignes sous la sélection (EndRow=2 → ligne 4, index 4)
    assert sheet.grid[(0, 4)] == "Tendance : Paris domine."   # markdown nettoyé
    assert sheet.merged, "la ligne de résultat doit être fusionnée"
    merged_area = sheet.merged[0][0]
    assert (merged_area.StartColumn, merged_area.EndColumn) == (0, 1)
    assert "sous la sélection" in message
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "Paris | 100" in prompt


def test_analyze_empty_selection():
    sheet = FakeCalcSheet()
    doc = FakeCalcDoc(sheet, selection_ref="A1:B2")
    shell = FakeShell()
    message = presets.run_analyze(_ctx(doc, shell), shell, "", None)
    assert "Sélectionnez" in message


def test_formula_agentic_applies_and_explains():
    sheet = FakeCalcSheet(grid={(0, 0): "Prix", (1, 0): "Qté",
                                (0, 1): "10", (1, 1): "3"})
    sheet.formula_results["=A2*B2"] = "30"
    doc = FakeCalcDoc(sheet, selection_ref="C2:C2")
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[
            FakeSSEResponse(native_tool_call_chunks(
                "calc_set_formula", '{"ref": "C2", "formula": "=A2*B2"}')),
            FakeSSEResponse(text_chunks("Multiplication du prix par la quantité.")),
        ])
    ctx = _ctx(doc, shell)
    registry = register_all(ToolRegistry())
    preset = next(p for p in presets.PRESETS if p.id == "formula")
    extra = preset.build_extra(ctx, shell, "prix fois quantité")
    sink = PaletteSink()
    orchestrator = Orchestrator(LLMClient(shell), registry, ctx)
    result = orchestrator.run_agentic("prix fois quantité", sink,
                                      preset_extra=extra)
    assert result.ok
    assert sheet.formulas[(2, 1)] == "=A2*B2"
    assert sheet.grid[(2, 1)] == "30"
    assert "Multiplication" in sink.text
    # le contexte de feuille est bien injecté dans le prompt système
    system = shell.requests[0]["messages"][0]["content"]
    assert "Target cell: C2" in system
    assert "POINT-VIRGULE" in system


def test_formula_error_feedback_loop():
    sheet = FakeCalcSheet(grid={(0, 1): "5"})
    sheet.formula_results["=SUM(A2:A9;B2:B9)"] = "Err:522"
    sheet.formula_results["=SUM(A2:B9)"] = "5"
    doc = FakeCalcDoc(sheet, selection_ref="C2:C2")
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[
            FakeSSEResponse(native_tool_call_chunks(
                "calc_set_formula",
                '{"ref": "C2", "formula": "=SUM(A2:A9;B2:B9)"}')),
            FakeSSEResponse(native_tool_call_chunks(
                "calc_set_formula",
                '{"ref": "C2", "formula": "=SUM(A2:B9)"}', call_id="call_2")),
            FakeSSEResponse(text_chunks("Corrigé.")),
        ])
    ctx = _ctx(doc, shell)
    registry = register_all(ToolRegistry())
    orchestrator = Orchestrator(LLMClient(shell), registry, ctx)
    result = orchestrator.run_agentic("somme", PaletteSink())
    assert result.ok and result.iterations == 3
    assert sheet.grid[(2, 1)] == "5"
