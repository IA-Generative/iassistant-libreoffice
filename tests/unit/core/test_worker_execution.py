"""Modèle d'exécution : worker + marshalling + annulation.

Ces tests verrouillent la propriété qui justifie toute la refonte : pendant un
run, **rien** ne touche UNO depuis le thread de fond, et le thread principal
n'est jamais immobilisé.
"""

import threading

import pytest

from src.mirai.core import presets
from src.mirai.core.context import ToolContext
from src.mirai.core.orchestrator import Orchestrator, RunObserver
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.sinks import PaletteSink
from src.mirai.core.tool_calls import ToolCall, ToolResult, ToolSpec
from src.mirai.core.ui_thread import DirectDispatcher
from tests.stubs.fake_docs import FakeWriterDoc
from tests.stubs.fake_shell import FakeShell, FakeSSEResponse, text_chunks
from tests.stubs.fake_writer_body import FakeBodyDoc
from tests.stubs.thread_pinned_dispatcher import ThreadPinnedDispatcher


class RecordingDispatcher(DirectDispatcher):
    """Exécute sur place, mais retient QUI a demandé quoi.

    Chaque appel note le thread appelant : c'est ce qui permet d'affirmer que
    le document n'est touché que via le dispatcher, jamais directement.
    """

    def __init__(self):
        super().__init__()
        self.calls = []

    def call(self, fn, timeout=30.0):
        self.calls.append(threading.current_thread().name)
        return super().call(fn, timeout=timeout)


class FakeDoc:
    """Document minimal : texte écrit."""

    def __init__(self):
        self.text = ""

    def write(self, chunk):
        self.text += chunk


class _StepResult:
    def __init__(self, text="", tool_calls=None, error=None):
        self.text = text
        self.tool_calls = tool_calls or []
        self.error = error
        self.streamed = False


class ScriptedLLM:
    configured_mode = "native"

    def __init__(self, steps):
        self._steps = list(steps)
        self.cancel_events = []

    def effective_mode(self):
        return "native"

    def step(self, messages, tools=None, on_text_delta=None, cancel_event=None,
             progress=None):
        self.cancel_events.append(cancel_event)
        step = self._steps.pop(0)
        if step.text and not step.tool_calls and on_text_delta:
            on_text_delta(step.text)
            step.streamed = True
        return step

    def encode_tool_exchange(self, step, results):
        return [{"role": "tool", "content": r.content} for r in results]


def _registry(on_call=None):
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="writer_insert_text", description="test", apps=("writer",),
        parameters={"type": "object", "properties": {}},
        handler=on_call or (
            lambda ctx, args: ToolResult(call_id="", ok=True, content="ok"))))
    return registry


def _context(shell, dispatcher, doc=None):
    return ToolContext(doc or object(), object(), "writer", shell,
                       dispatcher=dispatcher)


# Marshalling

def test_tools_are_executed_through_the_dispatcher():
    """Un tool touche le document : il doit passer par le thread principal."""
    dispatcher = RecordingDispatcher()
    shell = FakeShell()
    ctx = _context(shell, dispatcher)
    llm = ScriptedLLM([
        _StepResult(tool_calls=[ToolCall(id="1", name="writer_insert_text",
                                         arguments={})]),
        _StepResult(text="Fini."),
    ])

    orchestrator = Orchestrator(llm, _registry(), ctx, dispatcher=dispatcher)
    result = orchestrator.run_agentic("vas-y", PaletteSink())

    assert result.ok
    assert dispatcher.calls, "l'exécution du tool doit passer par dispatcher.call"


def test_undo_context_is_closed_through_the_dispatcher():
    dispatcher = RecordingDispatcher()
    ctx = _context(FakeShell(), dispatcher)
    llm = ScriptedLLM([_StepResult(text="ok")])

    Orchestrator(llm, _registry(), ctx, dispatcher=dispatcher).run_agentic(
        "x", PaletteSink())

    assert dispatcher.calls, "undo_end doit passer par le dispatcher"



def test_undo_context_is_opened_on_the_main_thread(thread_guard):
    """enterUndoContext depuis un worker = écriture VCL sans SolarMutex."""
    dispatcher = ThreadPinnedDispatcher()
    fake = FakeWriterDoc()
    ctx = _context(FakeShell(), dispatcher, doc=thread_guard(fake, dispatcher.thread))

    def worker():
        ctx.undo_begin("Test")
        ctx.undo_end()

    thread = threading.Thread(target=worker, name="mirai-run")
    thread.start()
    thread.join(timeout=5)
    dispatcher.close()

    assert fake.undo.entered == ["Test"]
    assert fake.undo.left == 1


# Presets Writer : lancés comme par la palette, dans un worker

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


def _run_guarded(thread_guard, fake, shell, run):
    """Lance run(ctx) dans un worker ; document et contrôleur n'acceptent que
    le thread principal simulé."""
    dispatcher = ThreadPinnedDispatcher()
    ctx = ToolContext(thread_guard(fake, dispatcher.thread),
                      thread_guard(fake.controller, dispatcher.thread, "controller"),
                      "writer", shell, dispatcher=dispatcher)
    try:
        return _in_worker(lambda: run(ctx))
    finally:
        dispatcher.close()


TARGETS = pytest.mark.parametrize("selection,paragraph", [
    ("Un texte administratif sélectionné.", ""),
    ("", "Le paragraphe sous le curseur."),
], ids=["selection", "current_paragraph"])


WRITER_PRESETS = pytest.mark.parametrize("runner", ["run_summarize", "run_simplify",
                                                   "run_shorten", "run_lengthen"])
APPEND_MODES = pytest.mark.parametrize("append_mode", [True, False],
                                       ids=["append", "replace"])


@WRITER_PRESETS
@APPEND_MODES
def test_writer_presets_on_a_selection_touch_the_document_only_on_the_main_thread(
        thread_guard, runner, append_mode):
    selection = "Un texte administratif sélectionné."
    fake = FakeWriterDoc(selection_text=selection)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Texte ", "produit."))])

    _run_guarded(thread_guard, fake, shell, lambda ctx: getattr(presets, runner)(
        ctx, shell, "", None, append_mode=append_mode))

    assert selection in shell.requests[0]["messages"][-1]["content"]
    assert "Texte produit." in (fake.inserted if append_mode else fake.replaced)
    assert fake.undo.entered and fake.undo.left == 1


@WRITER_PRESETS
@APPEND_MODES
def test_writer_presets_on_the_whole_document_touch_it_only_on_the_main_thread(
        thread_guard, runner, append_mode):
    fake = FakeBodyDoc(["Titre", "Un paragraphe administratif dense."],
                       ["Heading 1", "Standard"])
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks("Texte ", "produit."))])

    _run_guarded(thread_guard, fake, shell, lambda ctx: getattr(presets, runner)(
        ctx, shell, "", None, append_mode=append_mode))

    assert "Un paragraphe administratif dense." in shell.requests[0]["messages"][-1]["content"]
    assert "Texte produit." in fake.paragraphs
    assert fake.undo.entered and fake.undo.left == 1


@TARGETS
def test_extend_touches_the_document_only_on_the_main_thread(
        thread_guard, selection, paragraph):
    fake = FakeWriterDoc(selection_text=selection, current_paragraph=paragraph)
    shell = FakeShell(responses=[FakeSSEResponse(text_chunks(" et la suite."))])

    _run_guarded(thread_guard, fake, shell,
                 lambda ctx: presets.run_extend(ctx, shell, "", None))

    assert shell.requests[0]["messages"][-1]["content"] == (selection or paragraph)
    assert " et la suite." in fake.inserted


# Annulation

def test_cancel_event_is_passed_down_to_the_llm():
    cancel = threading.Event()
    dispatcher = DirectDispatcher()
    ctx = _context(FakeShell(), dispatcher)
    llm = ScriptedLLM([_StepResult(text="ok")])

    Orchestrator(llm, _registry(), ctx, dispatcher=dispatcher,
                 cancel_event=cancel).run_agentic("x", PaletteSink())

    assert llm.cancel_events == [cancel], (
        "sans propagation, « Arrêter » n'interromprait pas la lecture du flux")


def test_cancelled_run_stops_before_calling_the_llm():
    cancel = threading.Event()
    cancel.set()
    ctx = _context(FakeShell(), DirectDispatcher())
    llm = ScriptedLLM([_StepResult(text="ne doit pas être consommé")])

    result = Orchestrator(llm, _registry(), ctx, cancel_event=cancel).run_agentic(
        "x", PaletteSink())

    assert result.ok is False
    assert result.reason == "cancelled"
    assert llm.cancel_events == [], "aucun aller LLM ne doit partir"


def test_cancel_between_tool_calls_skips_the_rest():
    cancel = threading.Event()
    executed = []

    def _handler(ctx, args):
        executed.append(args)
        cancel.set()          # on annule pendant le premier outil
        return ToolResult(call_id="", ok=True, content="ok")

    ctx = _context(FakeShell(), DirectDispatcher())
    llm = ScriptedLLM([
        _StepResult(tool_calls=[
            ToolCall(id="1", name="writer_insert_text", arguments={"n": 1}),
            ToolCall(id="2", name="writer_insert_text", arguments={"n": 2}),
        ]),
        _StepResult(text="jamais atteint"),
    ])

    Orchestrator(llm, _registry(_handler), ctx, cancel_event=cancel).run_agentic(
        "x", PaletteSink())

    assert len(executed) == 1, "le second outil ne doit pas s'exécuter"


# Fermeture pendant un run

def test_closed_dispatcher_interrupts_the_run():
    """Fermer la palette pendant une génération ne doit rien casser."""
    from src.mirai.core.ui_thread import DispatcherClosed

    dispatcher = DirectDispatcher()
    ctx = _context(FakeShell(), dispatcher)
    dispatcher.close()

    try:
        ctx.on_main(lambda: "trop tard")
    except DispatcherClosed:
        return
    raise AssertionError("un dispatcher fermé doit lever DispatcherClosed")


def test_observer_interface_is_optional():
    """Un run sans observer ne doit pas planter (RunObserver par défaut)."""
    ctx = _context(FakeShell(), DirectDispatcher())
    llm = ScriptedLLM([_StepResult(text="ok")])
    result = Orchestrator(llm, _registry(), ctx, observer=RunObserver()).run_agentic(
        "x", PaletteSink())
    assert result.ok
