"""Golden iso-fonctionnels Calc : transform (colonne Résultat IA), analyse
(cellule fusionnée sous la sélection), formule agentique (via orchestrateur).
Assertions portées de menu_actions/calc.py."""

import threading

import pytest

from src.mirai.core import presets
from src.mirai.core.context import ToolContext
from src.mirai.core.llm_client import LLMClient
from src.mirai.core.orchestrator import Orchestrator, error_message
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.sinks import PaletteSink
from src.mirai.core.tools import register_all
from src.mirai.i18n import t as _t
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet
from tests.stubs.fake_shell import (
    FakeShell,
    FakeSSEResponse,
    StoppedSSEResponse,
    native_tool_call_chunks,
    text_chunks,
)
from tests.unit.core.test_worker_execution import ThreadPinnedDispatcher

_ABSENT = object()


class _CalcUndo:
    """Annulation de Calc réduite à ce que les presets en attendent : chaque
    contexte fermé devient UNE action, qu'un seul Ctrl+Z défait en entier.

    La grille de la feuille lui signale chaque écriture : `writes` les garde
    dans l'ordre, `outside` celles faites hors de tout contexte."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.actions = []
        self.writes = []
        self.outside = []
        self._open = None
        sheet.grid = _JournaledGrid(sheet.grid, self)

    def enterUndoContext(self, label):
        self._open = []

    def leaveUndoContext(self):
        self.actions.append(self._open)
        self._open = None

    def note(self, key, previous):
        self.writes.append(key)
        if self._open is None:
            self.outside.append(key)
        else:
            self._open.append((key, previous))

    def undo(self):
        grid = self.sheet.grid
        for key, previous in reversed(self.actions.pop()):
            if previous is _ABSENT:
                dict.pop(grid, key, None)
            else:
                dict.__setitem__(grid, key, previous)


class _JournaledGrid(dict):
    def __init__(self, cells, undo):
        super().__init__(cells)
        self._undo = undo

    def __setitem__(self, key, value):
        self._undo.note(key, self.get(key, _ABSENT))
        super().__setitem__(key, value)


def _undoable(doc):
    doc.undo = _CalcUndo(doc.sheet)
    return doc.undo


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


def test_stopped_transform_leaves_the_cells_untouched():
    """« Arrêter » pendant la première ligne : ni texte tronqué ni #ERREUR, et
    aucune ligne suivante n'est demandée."""
    cancel = threading.Event()
    sheet = FakeCalcSheet(grid={(0, 0): "pomme", (0, 1): "poire"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    shell = FakeShell(responses=[
        StoppedSSEResponse(text_chunks("APP", "LE"), cancel),
        FakeSSEResponse(text_chunks("PEAR")),
    ])
    message = presets.run_transform(_ctx(doc, shell), shell,
                                    "traduire en anglais", None,
                                    cancel_event=cancel)
    assert sheet.grid.get((1, 0), "") == ""
    assert sheet.grid.get((1, 1), "") == ""
    assert shell.urlopen_calls == 1
    assert message == _t("run.stopped")


def test_failed_transform_flags_the_cell():
    sheet = FakeCalcSheet(grid={(0, 0): "pomme"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A1")
    shell = FakeShell(responses=[OSError("connexion refusée")])
    message = presets.run_transform(_ctx(doc, shell), shell,
                                    "traduire en anglais", None)
    assert sheet.grid[(1, 0)].startswith("#ERREUR")
    assert message == _t("run.err_network")


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
    sheet.formula_errors["=SUM(A2:A9;B2:B9)"] = 522
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


# Lire avant, écrire après : le modèle répond dans le worker, la feuille n'est
# écrite qu'une fois chaque réponse complète.

def test_transform_writes_each_row_once_its_reply_is_complete():
    sheet = FakeCalcSheet(grid={(0, 0): "pomme", (0, 1): "poire"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    undo = _undoable(doc)
    seen = []

    class _Shell(FakeShell):
        def urlopen(self, request, timeout=None):
            seen.append(dict(sheet.grid))
            return super().urlopen(request, timeout)

    shell = _Shell(responses=[FakeSSEResponse(text_chunks("AP", "PLE")),
                              FakeSSEResponse(text_chunks("PE", "AR"))])
    teed = []
    presets.run_transform(_ctx(doc, shell), shell, "traduire en anglais", teed.append)
    assert undo.writes == [(1, 0), (1, 1)]
    assert sheet.grid[(1, 0)] == "APPLE" and sheet.grid[(1, 1)] == "PEAR"
    assert seen[1][(1, 0)] == "APPLE", "la ligne finie est écrite avant la suivante"
    assert "".join(teed) == "APPLE\nPEAR\n"


def test_one_undo_removes_the_whole_transform():
    """L'en-tête et toutes les lignes forment une seule action."""
    original = {(0, 0): "Nom", (1, 0): "Autre", (0, 1): "pomme", (1, 1): "pris",
                (0, 2): "poire"}
    sheet = FakeCalcSheet(grid=original)
    doc = FakeCalcDoc(sheet, selection_ref="A2:A3")
    undo = _undoable(doc)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("APPLE")),
                                 FakeSSEResponse(text_chunks("PEAR"))])
    presets.run_transform(_ctx(doc, shell), shell, "traduire en anglais", None)
    assert sheet.grid[(2, 0)] == "Résultat IA"
    assert sheet.grid[(2, 1)] == "APPLE" and sheet.grid[(2, 2)] == "PEAR"
    assert len(undo.actions) == 1
    undo.undo()
    assert sheet.grid == original


def test_stopped_transform_keeps_the_finished_rows():
    """« Arrêter » pendant la deuxième ligne : la première reste, la deuxième
    et la suivante ne reçoivent rien, et un Ctrl+Z retire le tout."""
    cancel = threading.Event()
    original = {(0, 0): "Nom", (1, 0): "Autre", (0, 1): "pomme", (1, 1): "pris",
                (0, 2): "poire", (0, 3): "prune"}
    sheet = FakeCalcSheet(grid=original)
    doc = FakeCalcDoc(sheet, selection_ref="A2:A4")
    undo = _undoable(doc)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("APPLE")),
                                 StoppedSSEResponse(text_chunks("PE", "AR"), cancel),
                                 FakeSSEResponse(text_chunks("PLUM"))])
    message = presets.run_transform(_ctx(doc, shell), shell, "traduire en anglais",
                                    None, cancel_event=cancel)
    assert message == _t("run.stopped")
    assert sheet.grid[(2, 0)] == "Résultat IA"
    assert sheet.grid[(2, 1)] == "APPLE"
    assert (2, 2) not in undo.writes and (2, 3) not in undo.writes
    assert shell.urlopen_calls == 2
    undo.undo()
    assert sheet.grid == original


def test_transform_stopped_on_its_first_row_leaves_the_sheet_as_it_was():
    cancel = threading.Event()
    original = {(0, 0): "Nom", (1, 0): "Autre", (0, 1): "pomme", (1, 1): "pris"}
    sheet = FakeCalcSheet(grid=original)
    doc = FakeCalcDoc(sheet, selection_ref="A2:A2")
    undo = _undoable(doc)
    shell = FakeShell(responses=[StoppedSSEResponse(text_chunks("APP", "LE"), cancel)])
    presets.run_transform(_ctx(doc, shell), shell, "traduire en anglais", None,
                          cancel_event=cancel)
    assert undo.writes == []
    assert sheet.grid == original


def test_an_empty_reply_is_not_counted_as_transformed():
    sheet = FakeCalcSheet(grid={(0, 0): "x", (0, 1): "y"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("X")),
                                 FakeSSEResponse(text_chunks())])
    message = presets.run_transform(_ctx(doc, shell), shell, "majuscules", None)
    assert message == _t("preset.done_transform", count=1, col="B")
    assert (1, 1) not in sheet.grid


def test_analysis_is_written_once_its_reply_is_complete():
    sheet = FakeCalcSheet(grid={(0, 0): "Ville", (1, 0): "Ventes",
                                (0, 1): "Paris", (1, 1): "100"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:B2")
    undo = _undoable(doc)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Paris ", "domine."))])
    teed = []
    message = presets.run_analyze(_ctx(doc, shell), shell, "", teed.append)
    assert undo.writes == [(0, 3)]
    assert undo.outside == []
    assert sheet.grid[(0, 3)] == "Paris domine."
    assert "".join(teed) == "Paris domine.\n"
    assert message == _t("preset.done_analyze")


def test_stopped_analysis_leaves_the_sheet_untouched():
    cancel = threading.Event()
    original = {(0, 0): "Ville", (1, 0): "Ventes", (0, 1): "Paris", (1, 1): "100"}
    sheet = FakeCalcSheet(grid=original)
    doc = FakeCalcDoc(sheet, selection_ref="A1:B2")
    shell = FakeShell(responses=[
        StoppedSSEResponse(text_chunks("Paris ", "domine."), cancel)])
    message = presets.run_analyze(_ctx(doc, shell), shell, "", None,
                                  cancel_event=cancel)
    assert sheet.grid == original
    assert sheet.merged == []
    assert message == _t("run.stopped")


def test_an_empty_analysis_is_not_announced_as_written():
    original = {(0, 0): "Ville", (0, 1): "Paris"}
    sheet = FakeCalcSheet(grid=original)
    doc = FakeCalcDoc(sheet, selection_ref="A1:A2")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks())])
    message = presets.run_analyze(_ctx(doc, shell), shell, "", None)
    assert message == error_message("empty_response")
    assert sheet.grid == original
    assert sheet.merged == []


# Une sélection de colonnes ou de lignes entières est lue sur le thread
# principal, d'un seul appel : seule sa partie utilisée doit l'être.

def _full_size_sheet(grid):
    """Feuille aux dimensions de Calc : 16 384 colonnes, 1 048 576 lignes."""
    return FakeCalcSheet(grid=grid, num_cols=16384, num_rows=1048576)


def test_a_whole_column_transform_reads_only_the_used_rows():
    sheet = _full_size_sheet({(0, 0): "pomme", (0, 1): "poire", (0, 2): "prune"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A1048576")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(word))
                                 for word in ("APPLE", "PEAR", "PLUM")])
    message = presets.run_transform(_ctx(doc, shell), shell, "traduire en anglais", None)
    assert message == _t("preset.done_transform", count=3, col="B")
    assert [sheet.grid[(1, row)] for row in range(3)] == ["APPLE", "PEAR", "PLUM"]
    assert dict(shell.telemetry_events)["TransformToColumn"]["rows"] == "3"
    assert sheet.cells_asked < 30


def test_a_whole_row_transform_reads_only_the_used_columns():
    sheet = _full_size_sheet({(0, 1): "pomme", (1, 1): "rouge"})
    doc = FakeCalcDoc(sheet, selection_ref="A2:XFD2")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("RED APPLE"))])
    message = presets.run_transform(_ctx(doc, shell), shell, "traduire en anglais", None)
    assert message == _t("preset.done_transform", count=1, col="C")
    assert "VALEUR SOURCE :\npomme | rouge\n" in shell.requests[0]["messages"][-1]["content"]
    assert sheet.grid[(2, 1)] == "RED APPLE"
    assert sheet.cells_asked < 30


def test_a_whole_column_analysis_reads_only_the_used_rows():
    sheet = _full_size_sheet({(0, 0): "Ville", (1, 0): "Ventes",
                            (0, 1): "Paris", (1, 1): "100",
                            (0, 2): "Lyon", (1, 2): "60"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:B1048576")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Paris domine."))])
    message = presets.run_analyze(_ctx(doc, shell), shell, "", None)
    assert message == _t("preset.done_analyze")
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "DONNÉES :\nVille | Ventes\nParis | 100\nLyon | 60\n\n" in prompt
    assert sheet.grid[(0, 4)] == "Paris domine."
    merged_area = sheet.merged[0][0]
    assert (merged_area.StartRow, merged_area.EndColumn) == (4, 1)
    assert sheet.cells_asked < 30


def test_a_selection_inside_the_used_area_is_read_whole():
    """Les lignes vides de la sélection restent lues tant qu'une autre
    colonne de la feuille descend plus bas : l'analyse garde sa place."""
    sheet = FakeCalcSheet(grid={(0, 0): "Ville", (1, 0): "Ventes",
                                (0, 1): "Paris", (1, 1): "100", (3, 9): "note"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:B5")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Paris domine."))])
    presets.run_analyze(_ctx(doc, shell), shell, "", None)
    prompt = shell.requests[0]["messages"][-1]["content"]
    assert "DONNÉES :\nVille | Ventes\nParis | 100\n | \n | \n |\n\n" in prompt
    assert sheet.grid[(0, 6)] == "Paris domine."


@pytest.mark.parametrize("selection", ["A10:A1048576", "C1:C1048576", "A3:A3"],
                         ids=["below", "right", "empty_cells"])
def test_a_selection_without_values_asks_for_filled_cells(selection):
    original = {(0, 0): "pomme", (0, 1): "poire", (0, 3): "prune"}
    sheet = _full_size_sheet(original)
    doc = FakeCalcDoc(sheet, selection_ref=selection)
    shell = FakeShell()
    assert presets.run_transform(_ctx(doc, shell), shell, "majuscules", None) \
        == _t("preset.need_transform_values")
    assert presets.run_analyze(_ctx(doc, shell), shell, "", None) == _t("preset.need_range")
    assert shell.requests == []
    assert sheet.grid == original
    assert sheet.cells_asked < 30


# Les presets Calc lancés comme par la palette, dans un worker : la feuille
# n'est touchée que depuis le thread principal simulé.

def _in_worker(fn):
    """Exécute fn dans un thread « mirai-run » et rend son résultat."""
    outcome = {}

    def target():
        try:
            outcome["value"] = fn()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target, name="mirai-run")
    thread.start()
    thread.join(timeout=10)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _guarded_ctx(thread_guard, doc, shell, dispatcher):
    """Document et contrôleur n'acceptent que le thread principal simulé."""
    return ToolContext(thread_guard(doc, dispatcher.thread),
                       thread_guard(doc.controller, dispatcher.thread, "controller"),
                       "calc", shell, dispatcher=dispatcher)


def test_transform_touches_the_sheet_only_on_the_main_thread(thread_guard):
    dispatcher = ThreadPinnedDispatcher()
    # Colonne B occupée : le résultat va en C, sous un en-tête « Résultat IA ».
    sheet = FakeCalcSheet(grid={(0, 0): "Nom", (1, 0): "Autre", (0, 1): "pomme",
                                (1, 1): "pris", (0, 3): "prune"})
    doc = FakeCalcDoc(sheet, selection_ref="A2:A4")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("APP", "LE")),
                                 FakeSSEResponse(text_chunks("PLUM"))])
    ctx = _guarded_ctx(thread_guard, doc, shell, dispatcher)
    try:
        message = _in_worker(lambda: presets.run_transform(
            ctx, shell, "traduire en anglais", None, dispatcher=dispatcher))
    finally:
        dispatcher.close()

    assert sheet.grid[(2, 0)] == "Résultat IA"
    assert sheet.grid[(2, 1)] == "APPLE"
    assert sheet.grid[(2, 3)] == "PLUM"
    assert message == _t("preset.done_transform", count=2, col="C")


@pytest.mark.parametrize("make_response,expected", [
    (lambda cancel: OSError("connexion refusée"), "#ERREUR: network_error"),
    (lambda cancel: StoppedSSEResponse(text_chunks("APP", "LE"), cancel), None),
], ids=["error", "stopped"])
def test_interrupted_transform_touches_the_sheet_only_on_the_main_thread(
        thread_guard, make_response, expected):
    cancel = threading.Event()
    dispatcher = ThreadPinnedDispatcher()
    sheet = FakeCalcSheet(grid={(0, 0): "pomme"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:A1")
    shell = FakeShell(responses=[make_response(cancel)])
    ctx = _guarded_ctx(thread_guard, doc, shell, dispatcher)
    try:
        _in_worker(lambda: presets.run_transform(
            ctx, shell, "traduire en anglais", None, cancel_event=cancel,
            dispatcher=dispatcher))
    finally:
        dispatcher.close()

    assert sheet.grid.get((1, 0)) == expected


def test_analyze_touches_the_sheet_only_on_the_main_thread(thread_guard):
    dispatcher = ThreadPinnedDispatcher()
    sheet = FakeCalcSheet(grid={(0, 0): "Ville", (1, 0): "Ventes",
                                (0, 1): "Paris", (1, 1): "100"})
    doc = FakeCalcDoc(sheet, selection_ref="A1:B2")
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Paris ", "domine."))])
    ctx = _guarded_ctx(thread_guard, doc, shell, dispatcher)
    try:
        message = _in_worker(lambda: presets.run_analyze(
            ctx, shell, "", None, dispatcher=dispatcher))
    finally:
        dispatcher.close()

    assert "Paris | 100" in shell.requests[0]["messages"][-1]["content"]
    assert sheet.grid[(0, 3)] == "Paris domine."
    assert sheet.merged
    assert message == _t("preset.done_analyze")


def test_formula_preparation_reads_the_sheet_on_the_main_thread(thread_guard):
    dispatcher = ThreadPinnedDispatcher()
    sheet = FakeCalcSheet(grid={(0, 0): "Prix", (1, 0): "Qté",
                                (0, 1): "10", (1, 1): "3"})
    doc = FakeCalcDoc(sheet, selection_ref="C2:C2")
    shell = FakeShell()
    ctx = _guarded_ctx(thread_guard, doc, shell, dispatcher)
    formula = next(p for p in presets.PRESETS if p.id == "formula")
    try:
        extra = _in_worker(lambda: formula.build_extra(ctx, shell, "prix fois quantité"))
    finally:
        dispatcher.close()

    assert "Target cell: C2" in extra
    assert "A='Prix', B='Qté'" in extra
