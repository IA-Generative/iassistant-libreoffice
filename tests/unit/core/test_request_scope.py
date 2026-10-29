"""Portée d'une demande libre : le modèle apprend, avant la demande, sur quelles
cellules elle porte."""

import pytest

from src.mirai.core.context import ToolContext
from src.mirai.core.llm_client import LLMClient
from src.mirai.core.orchestrator import Orchestrator
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.sinks import PaletteSink
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks


def _request_seen_by_the_model(selection_ref):
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Fait."))])
    doc = FakeCalcDoc(FakeCalcSheet(), selection_ref=selection_ref)
    ctx = ToolContext(doc, doc.controller, "calc", shell)
    Orchestrator(LLMClient(shell), ToolRegistry(), ctx).run_agentic(
        "Remplis ces cellules de noms de fruits.", PaletteSink())
    return shell.requests[0]["messages"][-1]["content"]


@pytest.mark.parametrize("selection_ref, announced", [
    ("A1:A20", ["A1:A20", "20 cellules", "N'agis que sur elle"]),
    ("A11:A20", ["A11:A20", "10 cellules"]),
    ("C3", ["point de départ", "résultat unique s'écrit dans C3",
            "commence en C3"]),
    ("A1:A5,C2:D4", ["A1:A5", "C2:D4", "11 cellules", "N'agis que sur elles"]),
], ids=["range", "lower_range", "single_cell", "several_ranges"])
def test_a_calc_request_names_the_selected_cells(selection_ref, announced):
    request = _request_seen_by_the_model(selection_ref)

    assert "aucune plage" not in request
    for fragment in announced:
        assert fragment in request
    assert request.endswith("Remplis ces cellules de noms de fruits.")
