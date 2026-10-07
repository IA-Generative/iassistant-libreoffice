"""Modèle d'exécution : worker + marshalling + annulation.

Ces tests verrouillent la propriété qui justifie toute la refonte : pendant un
run, **rien** ne touche UNO depuis le thread de fond, et le thread principal
n'est jamais immobilisé.
"""

import queue
import threading

from src.mirai.core.context import ToolContext
from src.mirai.core.orchestrator import Orchestrator, RunObserver
from src.mirai.core.registry import ToolRegistry
from src.mirai.core.sinks import PaletteSink, WriterInsertSink, WriterReplaceSink
from src.mirai.core.tool_calls import ToolCall, ToolResult, ToolSpec
from src.mirai.core.ui_thread import DirectDispatcher
from tests.stubs.fake_shell import FakeShell


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
    """Document minimal qui refuse d'être touché hors thread principal."""

    def __init__(self, allowed_thread):
        self.allowed = allowed_thread
        self.violations = []
        self.text = ""

    def _check(self):
        current = threading.current_thread()
        if current is not self.allowed:
            self.violations.append(current.name)

    def write(self, chunk):
        self._check()
        self.text += chunk

    def getUndoManager(self):
        self._check()
        return self

    def enterUndoContext(self, _label):
        self._check()

    def leaveUndoContext(self):
        self._check()


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


class ThreadPinnedDispatcher:
    """Dispatcher qui exécute réellement sur UN thread désigné.

    Il tient le rôle du thread principal de LibreOffice : une file, un thread
    de service, et des appels qui attendent leur résultat. C'est ce qui permet
    de prouver le marshalling au lieu de le supposer.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._closed = False
        self.thread = threading.Thread(target=self._serve, name="main-uno",
                                       daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, result_queue = item
            try:
                value = fn()
                if result_queue is not None:
                    result_queue.put(("ok", value))
            except Exception as exc:
                if result_queue is not None:
                    result_queue.put(("error", exc))

    def post(self, fn):
        if self._closed:
            return False
        self._queue.put((fn, None))
        return True

    def call(self, fn, timeout=30.0):
        if self._closed:
            raise RuntimeError("fermé")
        result_queue = queue.Queue(maxsize=1)
        self._queue.put((fn, result_queue))
        status, payload = result_queue.get(timeout=timeout)
        if status == "error":
            raise payload
        return payload

    def close(self):
        self._closed = True
        self._queue.put(None)


def test_writer_sink_never_touches_the_document_from_a_worker():
    """Le test qui donne son sens à la refonte.

    Le run tourne dans un thread worker ; le document note tout accès venu
    d'ailleurs que du thread principal simulé. La liste des violations doit
    rester vide — c'est exactement la garantie que l'ancien code n'avait pas.
    """
    dispatcher = ThreadPinnedDispatcher()
    doc = FakeDoc(allowed_thread=dispatcher.thread)
    ctx = _context(FakeShell(), dispatcher, doc=doc)

    class DocSink(WriterInsertSink):
        def _start_on_main(self):
            self._started = True

        def _insert_on_main(self, text):
            doc.write(text)

    sink = DocSink(ctx, "", "")

    def worker():
        sink.stream_delta("bon")
        sink.stream_delta("jour")

    thread = threading.Thread(target=worker, name="mirai-run")
    thread.start()
    thread.join(timeout=5)
    dispatcher.close()

    assert doc.text == "bonjour"
    assert doc.violations == [], (
        "le document a été touché depuis un thread de fond : "
        f"{doc.violations}")


def test_undo_context_is_opened_on_the_main_thread():
    """enterUndoContext depuis un worker = écriture VCL sans SolarMutex."""
    dispatcher = ThreadPinnedDispatcher()
    doc = FakeDoc(allowed_thread=dispatcher.thread)
    ctx = _context(FakeShell(), dispatcher, doc=doc)

    def worker():
        ctx.undo_begin("Test")
        ctx.undo_end()

    thread = threading.Thread(target=worker, name="mirai-run")
    thread.start()
    thread.join(timeout=5)
    dispatcher.close()

    assert doc.violations == [], f"undo touché hors thread principal : {doc.violations}"


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


def test_replace_sink_writes_on_the_main_thread():
    """Le remplacement de la sélection ne doit jamais partir du worker."""
    dispatcher = ThreadPinnedDispatcher()
    written = {}

    class _Target:
        def setString(self, text):
            written["text"] = text
            written["thread"] = threading.current_thread()

    target = _Target()

    class _Selection:
        def getByIndex(self, _index):
            return target

    class _Controller:
        def getSelection(self):
            return _Selection()

        def select(self, _range):
            pass

    ctx = _context(FakeShell(), dispatcher)
    ctx.controller = _Controller()
    sink = WriterReplaceSink(ctx)

    def worker():
        sink.stream_delta("texte final")
        sink.finish("", streamed=True)

    thread = threading.Thread(target=worker, name="mirai-run")
    thread.start()
    thread.join(timeout=5)
    dispatcher.close()

    assert written["text"] == "texte final"
    assert written["thread"] is dispatcher.thread, (
        "setString a été appelé depuis le worker au lieu du thread principal")


def test_observer_interface_is_optional():
    """Un run sans observer ne doit pas planter (RunObserver par défaut)."""
    ctx = _context(FakeShell(), DirectDispatcher())
    llm = ScriptedLLM([_StepResult(text="ok")])
    result = Orchestrator(llm, _registry(), ctx, observer=RunObserver()).run_agentic(
        "x", PaletteSink())
    assert result.ok
