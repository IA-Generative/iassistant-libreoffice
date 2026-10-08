"""Boucle agentique : run texte, run avec tools, plafond d'itérations, undo,
conversation, observer (journal d'actions), erreurs HTTP."""

import tempfile

from src.mirai.core.conversation import ConversationStore
from src.mirai.core.llm_client import StepResult
from src.mirai.core.orchestrator import Orchestrator, RunObserver
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.sinks import PaletteSink
from src.mirai.core.tool_calls import ToolCall, ToolResult, ToolSpec
from tests.stubs.fake_shell import FakeShell


class FakeLLM:
    configured_mode = "native"

    def __init__(self, steps):
        self._steps = list(steps)
        self.seen_messages = []

    def effective_mode(self):
        return "native"

    def step(self, messages, tools=None, on_text_delta=None, cancel_event=None,
             progress=None):
        self.seen_messages.append(list(messages))
        step = self._steps.pop(0)
        if step.text and not step.tool_calls and on_text_delta:
            on_text_delta(step.text)
            step.streamed = True
        return step

    def encode_tool_exchange(self, step, results):
        return [{"role": "assistant", "content": "<tool-exchange>"}]


class _Ctx:
    def __init__(self, shell, app="writer"):
        self.app = app
        self.shell = shell
        self.undo_begun = []
        self.undo_ended = 0
        self.model = None
        self.controller = None

    def undo_begin(self, label):
        self.undo_begun.append(label)

    def undo_end(self):
        self.undo_ended += 1


class _RecordingObserver(RunObserver):
    def __init__(self):
        self.events = []

    def on_run_start(self, mode):
        self.events.append(("start", mode))

    def on_tool_calls(self, calls):
        self.events.append(("proposed", [c.name for c in calls]))

    def on_tool_result(self, call, result, duration_ms):
        self.events.append(("result", call.name, result.ok))

    def on_final(self, text):
        self.events.append(("final", text))

    def on_error(self, code, message):
        self.events.append(("error", code))


def _registry():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="writer_probe", description="sonde",
        parameters={"type": "object", "properties": {}},
        handler=lambda ctx, args: ToolResult(call_id="", ok=True, content="vu"),
        apps=("writer",),
    ))
    return registry


def test_text_only_run():
    shell = FakeShell()
    ctx = _Ctx(shell)
    observer = _RecordingObserver()
    sink = PaletteSink()
    orchestrator = Orchestrator(FakeLLM([StepResult(text="Réponse.")]),
                                _registry(), ctx, observer=observer)
    result = orchestrator.run_agentic("question", sink)
    assert result.ok and result.iterations == 1
    assert sink.text == "Réponse."
    assert ("final", "Réponse.") in observer.events
    assert ctx.undo_ended == 1
    # Le span AssistantRun est émis par le worker de la palette, pas par l'orchestrateur.
    spans = [s for s, _ in shell.telemetry_events]
    assert "AssistantRun" not in spans


def test_orchestrator_emits_no_span_itself():
    """Tout ce que le span portait est dans le RunResult : ok, iterations,
    reason. Un second émetteur ferait diverger deux formats du même span."""
    shell = FakeShell()
    ctx = _Ctx(shell)
    orchestrator = Orchestrator(FakeLLM([StepResult(text="ok")]),
                                _registry(), ctx)
    result = orchestrator.run_agentic("x", PaletteSink())
    assert result.ok
    assert shell.telemetry_events == []


def test_tool_call_then_final():
    shell = FakeShell()
    ctx = _Ctx(shell)
    observer = _RecordingObserver()
    llm = FakeLLM([
        StepResult(tool_calls=[ToolCall(id="c1", name="writer_probe", arguments={})]),
        StepResult(text="Fini."),
    ])
    orchestrator = Orchestrator(llm, _registry(), ctx, observer=observer)
    result = orchestrator.run_agentic("fais un truc", PaletteSink())
    assert result.ok and result.iterations == 2
    assert ("proposed", ["writer_probe"]) in observer.events
    assert ("result", "writer_probe", True) in observer.events
    # l'échange outil a été réinjecté dans les messages du 2e step
    assert any(m.get("content") == "<tool-exchange>"
               for m in llm.seen_messages[1])


def test_max_iterations_terminates():
    shell = FakeShell()
    ctx = _Ctx(shell)
    observer = _RecordingObserver()
    endless = [StepResult(tool_calls=[ToolCall(id=f"c{i}", name="writer_probe",
                                               arguments={})])
               for i in range(10)]
    orchestrator = Orchestrator(FakeLLM(endless), _registry(), ctx,
                                observer=observer, max_iterations=3)
    result = orchestrator.run_agentic("boucle", PaletteSink())
    assert not result.ok and result.reason == "max_iterations"
    assert ("error", "max_iterations") in observer.events
    assert ctx.undo_ended == 1


def test_step_error_stops_run_with_message():
    shell = FakeShell()
    ctx = _Ctx(shell)
    observer = _RecordingObserver()
    orchestrator = Orchestrator(FakeLLM([StepResult(error="http_429")]),
                                _registry(), ctx, observer=observer)
    result = orchestrator.run_agentic("x", PaletteSink())
    assert not result.ok and result.reason == "http_429"
    assert "Quota" in result.text
    assert ("error", "http_429") in observer.events


def test_conversation_recorded_and_injected():
    shell = FakeShell()
    ctx = _Ctx(shell)
    store = ConversationStore(tempfile.mkdtemp())
    store.append("user", "question précédente")
    store.append("assistant", "réponse précédente")
    llm = FakeLLM([StepResult(text="Nouvelle réponse.")])
    orchestrator = Orchestrator(llm, _registry(), ctx, conversation=store)
    orchestrator.run_agentic("nouvelle question", PaletteSink())
    # contexte injecté
    first_messages = llm.seen_messages[0]
    contents = [m["content"] for m in first_messages]
    assert "réponse précédente" in contents
    # tour enregistré
    entries = store.load()
    assert entries[-1]["text"] == "Nouvelle réponse."
    assert entries[-2]["text"] == "nouvelle question"


def test_conversation_not_polluted_by_failed_run():
    shell = FakeShell()
    ctx = _Ctx(shell)
    store = ConversationStore(tempfile.mkdtemp())
    orchestrator = Orchestrator(FakeLLM([StepResult(error="network_error")]),
                                _registry(), ctx, conversation=store)
    orchestrator.run_agentic("x", PaletteSink())
    assert store.load() == []


# Run stérile : rien produit, rien fait
#
# Un run sans texte ni appel d'outil doit être un échec : sinon l'écran reste
# muet pour l'utilisateur et la télémétrie porte `assistant.ok=true`.

def test_empty_response_is_a_failure():
    shell = FakeShell()
    ctx = _Ctx(shell)
    orchestrator = Orchestrator(FakeLLM([StepResult(text="")]),
                                _registry(), ctx)
    result = orchestrator.run_agentic("résume le document", PaletteSink())
    assert not result.ok
    assert result.reason == "empty_response"
    assert result.iterations == 1
    assert "n'a rien produit" in result.text


def test_whitespace_only_response_is_empty():
    shell = FakeShell()
    ctx = _Ctx(shell)
    orchestrator = Orchestrator(FakeLLM([StepResult(text="  \n\t ")]),
                                _registry(), ctx)
    result = orchestrator.run_agentic("x", PaletteSink())
    assert not result.ok and result.reason == "empty_response"


def test_empty_response_notifies_observer():
    shell = FakeShell()
    ctx = _Ctx(shell)
    observer = _RecordingObserver()
    orchestrator = Orchestrator(FakeLLM([StepResult(text="")]),
                                _registry(), ctx, observer=observer)
    orchestrator.run_agentic("x", PaletteSink())
    assert ("error", "empty_response") in observer.events
    assert not [e for e in observer.events if e[0] == "final"]


def test_empty_response_does_not_pollute_conversation():
    shell = FakeShell()
    ctx = _Ctx(shell)
    store = ConversationStore(tempfile.mkdtemp())
    orchestrator = Orchestrator(FakeLLM([StepResult(text="")]),
                                _registry(), ctx, conversation=store)
    orchestrator.run_agentic("x", PaletteSink())
    assert store.load() == []


def test_empty_final_after_tool_stays_a_success():
    """Un outil a modifié le document : un texte de clôture vide est légitime.

    C'est la contre-épreuve de la garde — elle ne doit pas transformer un
    travail réellement effectué en échec.
    """
    shell = FakeShell()
    ctx = _Ctx(shell)
    llm = FakeLLM([
        StepResult(tool_calls=[ToolCall(id="c1", name="writer_probe", arguments={})]),
        StepResult(text=""),
    ])
    orchestrator = Orchestrator(llm, _registry(), ctx)
    result = orchestrator.run_agentic("réécris le document", PaletteSink())
    assert result.ok
    assert result.iterations == 2
    assert result.reason == ""


def test_undo_is_closed_even_on_sterile_run():
    shell = FakeShell()
    ctx = _Ctx(shell)
    orchestrator = Orchestrator(FakeLLM([StepResult(text="")]),
                                _registry(), ctx)
    orchestrator.run_agentic("x", PaletteSink())
    assert ctx.undo_ended == 1
