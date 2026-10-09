"""ThreadGuard : un accès UNO hors du thread propriétaire est noté."""

import threading
from unittest.mock import MagicMock

from tests.stubs.fake_docs import FakeWriterDoc
from tests.stubs.thread_guard import ThreadGuard


def _in_worker(fn):
    thread = threading.Thread(target=fn, name="worker")
    thread.start()
    thread.join(timeout=5)


def test_access_from_the_owner_thread_is_not_recorded():
    doc = ThreadGuard(FakeWriterDoc(selection_text="texte"))
    selection = doc.CurrentController.getSelection().getByIndex(0)
    assert selection.getString() == "texte"
    assert doc.violations == []


def test_reading_a_method_for_the_main_thread_is_not_recorded():
    doc = ThreadGuard(FakeWriterDoc())
    found = {}
    _in_worker(lambda: found.update(method=doc.getUndoManager))
    found["method"]()
    assert doc.violations == []


def test_access_from_another_thread_is_recorded_through_returned_objects():
    service_manager = ThreadGuard(MagicMock(), path="smgr")
    dialog = service_manager.createInstanceWithContext("com.sun.star.awt.Dialog", None)

    _in_worker(lambda: dialog.execute())

    assert service_manager.violations == [
        "smgr.createInstanceWithContext().execute() depuis worker"]


def test_guarded_arguments_reach_the_fake_unwrapped():
    doc = ThreadGuard(FakeWriterDoc(current_paragraph="ligne"))
    cursor = doc.Text.createTextCursor()
    cursor.gotoEndOfParagraph(True)
    doc.CurrentController.select(cursor)
    assert doc.CurrentController.getSelection().getByIndex(0).getString() == "ligne"
