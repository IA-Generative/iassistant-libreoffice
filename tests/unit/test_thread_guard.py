"""ThreadGuard : un accès UNO hors du thread propriétaire est noté."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.stubs.fake_docs import FakeCalcDoc, FakeCalcSheet, FakeCursor, FakeWriterDoc
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


@pytest.mark.parametrize("fake", [FakeWriterDoc, MagicMock])
def test_reading_a_method_for_the_main_thread_is_not_recorded(fake):
    doc = ThreadGuard(fake())
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


def test_property_read_on_a_mock_from_another_thread_is_recorded():
    service_manager = ThreadGuard(MagicMock(), path="smgr")
    dialog = service_manager.createInstanceWithContext("com.sun.star.awt.Dialog", None)

    _in_worker(lambda: dialog.Model)

    assert service_manager.violations == [
        "smgr.createInstanceWithContext().Model depuis worker"]


def test_reading_a_struct_field_from_another_thread_is_not_recorded():
    doc = ThreadGuard(FakeCalcDoc(FakeCalcSheet(), selection_ref="A2:A5"))
    area = doc.CurrentController.getSelection().getRangeAddress()
    found = {}

    _in_worker(lambda: found.update(rows=(area.StartRow, area.EndRow)))

    assert found["rows"] == (1, 4)
    assert doc.violations == []


def test_objects_inside_a_returned_sequence_stay_guarded():
    dialog = MagicMock()
    dialog.getControls.return_value = (MagicMock(),)
    guarded = ThreadGuard(dialog, path="dialog")
    controls = guarded.getControls()

    _in_worker(lambda: controls[0].setText("x"))

    assert guarded.violations == ["dialog.getControls()[0].setText() depuis worker"]


def test_guarded_arguments_reach_the_fake_unwrapped():
    doc = ThreadGuard(FakeWriterDoc(current_paragraph="ligne"))
    cursor = doc.Text.createTextCursor()
    cursor.gotoEndOfParagraph(True)
    doc.CurrentController.select(cursor)
    assert doc.CurrentController.getSelection().getByIndex(0).getString() == "ligne"


def test_assigned_guarded_values_reach_the_fake_unwrapped():
    target = SimpleNamespace()
    cursor = ThreadGuard(FakeWriterDoc()).Text.createTextCursor()
    ThreadGuard(target).Cursor = cursor
    assert isinstance(target.Cursor, FakeCursor)
