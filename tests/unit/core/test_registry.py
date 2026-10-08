"""Registre de tools : list/call, inconnu, exception, undo, plafond, télémétrie."""


from src.mirai.core.registry import ToolRegistry
from src.mirai.core.tool_calls import ToolResult, ToolSpec
from tests.stubs.fake_shell import FakeShell


class _Ctx:
    def __init__(self):
        self.app = "writer"
        self.shell = FakeShell()
        self.undo_labels = []
        self.undo_ended = 0

    def undo_begin(self, label):
        self.undo_labels.append(label)

    def undo_end(self):
        self.undo_ended += 1


def _make_registry():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="writer_echo", description="écho",
        parameters={"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        handler=lambda ctx, args: ToolResult(call_id="", ok=True,
                                             content="echo:" + args["text"]),
        apps=("writer",),
    ))
    registry.register(ToolSpec(
        name="writer_boom", description="explose",
        parameters={"type": "object", "properties": {}},
        handler=lambda ctx, args: (_ for _ in ()).throw(RuntimeError("kaboom")),
        apps=("writer",), mutates=True,
    ))
    registry.register(ToolSpec(
        name="calc_only", description="calc",
        parameters={"type": "object", "properties": {}},
        handler=lambda ctx, args: ToolResult(call_id="", ok=True, content="ok"),
        apps=("calc",),
    ))
    return registry


def test_list_tools_filters_by_app():
    registry = _make_registry()
    names = [s.name for s in registry.list_tools("writer")]
    assert "writer_echo" in names and "calc_only" not in names


def test_openai_tools_format():
    registry = _make_registry()
    tools = registry.openai_tools("writer")
    assert all(t["type"] == "function" for t in tools)
    assert any(t["function"]["name"] == "writer_echo" for t in tools)


def test_prompt_catalog_mentions_tools_and_types():
    registry = _make_registry()
    catalog = registry.prompt_catalog("writer")
    assert "writer_echo" in catalog and "text: string" in catalog


def test_call_tool_success_and_telemetry():
    registry, ctx = _make_registry(), _Ctx()
    result = registry.call_tool("writer_echo", {"text": "hé"}, ctx, call_id="c1")
    assert result.ok and result.content == "echo:hé" and result.call_id == "c1"
    spans = [s for s, _ in ctx.shell.telemetry_events]
    assert "AssistantToolCall" in spans
    attrs = ctx.shell.telemetry_events[0][1]
    assert attrs["tool.name"] == "writer_echo" and attrs["tool.ok"] is True
    # Jamais d'arguments ni de contenu en télémétrie
    assert "hé" not in str(attrs)


def test_call_unknown_tool():
    registry, ctx = _make_registry(), _Ctx()
    result = registry.call_tool("inexistant", {}, ctx)
    assert not result.ok and "inconnu" in result.error.lower()


def test_wrong_app_tool_is_unknown():
    registry, ctx = _make_registry(), _Ctx()
    result = registry.call_tool("calc_only", {}, ctx)
    assert not result.ok


def test_validation_error_reported():
    registry, ctx = _make_registry(), _Ctx()
    result = registry.call_tool("writer_echo", {}, ctx)
    assert not result.ok and "text" in result.error


def test_handler_exception_never_raises():
    registry, ctx = _make_registry(), _Ctx()
    result = registry.call_tool("writer_boom", {}, ctx)
    assert not result.ok and "kaboom" in result.error


def test_undo_opened_only_for_mutating_tools():
    registry, ctx = _make_registry(), _Ctx()
    registry.call_tool("writer_echo", {"text": "x"}, ctx)
    assert ctx.undo_labels == []
    registry.call_tool("writer_boom", {}, ctx)
    assert len(ctx.undo_labels) == 1


def test_content_capped():
    registry, ctx = _make_registry(), _Ctx()
    ctx.shell.config["tool_result_max_chars"] = 10
    result = registry.call_tool("writer_echo", {"text": "a" * 100}, ctx)
    assert len(result.content) < 50 and "tronqué" in result.content


# Télémétrie enrichie : motif d'échec + coercitions + types

def _tool_span(ctx):
    spans = [(s, a) for s, a in ctx.shell.telemetry_events
             if s == "AssistantToolCall"]
    assert spans, "chaque appel d'outil doit produire son span"
    return spans[-1][1]


def test_tool_span_attributes_are_typed():
    """bool/int réels, pas des chaînes : agrégeables côté Grafana."""
    registry, ctx = _make_registry(), _Ctx()
    registry.call_tool("writer_echo", {"text": "x"}, ctx)
    attrs = _tool_span(ctx)
    assert attrs["tool.ok"] is True
    assert isinstance(attrs["tool.duration_ms"], int)


def test_unknown_tool_reports_its_error_kind():
    registry, ctx = _make_registry(), _Ctx()
    registry.call_tool("inexistant", {}, ctx)
    attrs = _tool_span(ctx)
    assert attrs["tool.ok"] is False
    assert attrs["tool.error_kind"] == "unknown_tool"


def test_invalid_args_report_their_error_kind():
    registry, ctx = _make_registry(), _Ctx()
    registry.call_tool("writer_echo", {}, ctx)   # requis manquant
    assert _tool_span(ctx)["tool.error_kind"] == "invalid_args"


def test_handler_exception_reports_its_error_kind():
    registry, ctx = _make_registry(), _Ctx()
    registry.call_tool("writer_boom", {}, ctx)
    assert _tool_span(ctx)["tool.error_kind"] == "exception"


def test_a_successful_call_carries_no_error_kind():
    registry, ctx = _make_registry(), _Ctx()
    registry.call_tool("writer_echo", {"text": "x"}, ctx)
    assert "tool.error_kind" not in _tool_span(ctx)


def _bounded_registry():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="writer_read", description="lecture bornée",
        parameters={"type": "object", "properties": {
            "count": {"type": "integer", "maximum": 20}}},
        handler=lambda ctx, args: ToolResult(call_id="", ok=True,
                                             content=str(args.get("count"))),
        apps=("writer",),
    ))
    return registry


def test_clamped_arguments_are_counted():
    """Un argument hors bornes est ramené à la borne (99 → 20) au lieu d'être rejeté :
    la coercition doit se voir, sinon impossible de savoir combien d'appels sont
    rattrapés en silence."""
    registry, ctx = _bounded_registry(), _Ctx()
    result = registry.call_tool("writer_read", {"count": 99}, ctx)
    assert result.ok and result.content == "20"
    assert _tool_span(ctx)["tool.args_coerced"] == 1


def test_clean_arguments_are_not_counted_as_coerced():
    registry, ctx = _bounded_registry(), _Ctx()
    registry.call_tool("writer_read", {"count": 5}, ctx)
    assert "tool.args_coerced" not in _tool_span(ctx)


def test_tool_span_still_never_carries_arguments():
    registry, ctx = _bounded_registry(), _Ctx()
    registry.call_tool("writer_read", {"count": 99, "note": "texte du doc"}, ctx)
    assert "texte du doc" not in str(_tool_span(ctx))
