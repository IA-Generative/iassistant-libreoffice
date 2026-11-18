"""CalcCellSink : ce que la palette reçoit d'une réponse destinée à une cellule."""

from src.mirai.core.context import ToolContext
from src.mirai.core.sinks import CalcCellSink
from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet
from tests.stubs.fake_shell import FakeShell


def _sink(seen, written):
    doc = FakeCalcDoc(FakeCalcSheet())
    ctx = ToolContext(doc, doc.controller, "calc", FakeShell())
    return CalcCellSink(ctx, written.append, tee=seen.append)


def test_a_streamed_reply_ends_its_line_in_the_palette():
    seen, written = [], []
    sink = _sink(seen, written)
    sink.stream_delta("APP")
    sink.stream_delta("LE")
    sink.finish("APPLE", streamed=True)
    assert seen == ["APP", "LE", "\n"]
    assert written == ["APPLE"]


def test_a_reply_received_in_one_block_still_reaches_the_palette():
    seen, written = [], []
    sink = _sink(seen, written)
    sink.finish("APPLE", streamed=False)
    assert seen == ["APPLE", "\n"]
    assert written == ["APPLE"]


def test_an_empty_reply_leaves_no_blank_line_in_the_palette():
    seen, written = [], []
    sink = _sink(seen, written)
    sink.finish("", streamed=True)
    assert seen == []
    assert written == []
    assert sink.written is False
