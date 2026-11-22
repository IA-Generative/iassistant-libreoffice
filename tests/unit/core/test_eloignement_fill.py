"""Balises : valeurs insérées comme données, actualisation sans écraser la main de l'agent."""

from unittest.mock import MagicMock

import pytest

from src.mirai.core.eloignement import fill


@pytest.mark.parametrize("raw,name", [
    ("<etranger.nom>", "etranger.nom"),
    ("etranger.date_naissance", "etranger.date_naissance"),
    ("<x>", "x"),
    ("<a b>", ""),
    ("<a.b>; DROP", ""),
    ("", ""),
])
def test_balise_name(raw, name):
    assert fill.balise_name(raw) == name


def test_values_become_plain_text():
    assert fill.as_text(None) == ""
    assert fill.as_text(True) == "oui"
    assert fill.as_text(["A", None, "B"]) == "A, B"
    assert fill.as_text("ligne 1\r\nligne 2") == "ligne 1\nligne 2"


def test_mark_name_round_trip():
    name = fill.mark_name("etranger.nom", "KOFFI", 3)
    assert fill.parse_mark(name) == ("etranger.nom", fill.digest("KOFFI"))
    assert fill.parse_mark("balise:etranger.nom") == ("etranger.nom", "")
    assert fill.parse_mark("Signet1") is None


def test_refresh_plan_proposes_only_differences_and_flags_hand_edits():
    marks = [
        fill.Mark(fill.mark_name("a.nom", "KOFFI", 1), "a.nom", fill.digest("KOFFI"), "KOFFI"),
        fill.Mark(fill.mark_name("a.ville", "Lyon", 2), "a.ville", fill.digest("Lyon"), "Lyon 3e"),
        fill.Mark(fill.mark_name("a.date", "01/01/1990", 3), "a.date", fill.digest("01/01/1990"),
                  "01/01/1990"),
        fill.Mark("balise:a.mesure", "a.mesure", "", "OQTF"),
    ]
    plan = fill.refresh_plan(marks, {"a.nom": "KOFFI", "a.ville": "Marseille",
                                     "a.date": "02/01/1990", "a.mesure": "IRTF"})
    by_balise = {c.balise: c for c in plan}
    assert set(by_balise) == {"a.ville", "a.date", "a.mesure"}
    assert by_balise["a.ville"].modified_by_hand is True       # « Lyon 3e » tapé à la main
    assert by_balise["a.date"].modified_by_hand is False
    assert by_balise["a.mesure"].modified_by_hand is True      # inconnu : prudence


def test_a_balise_absent_from_the_dossier_is_left_alone():
    marks = [fill.Mark("m", "a.nom", fill.digest("X"), "X")]
    assert fill.refresh_plan(marks, {}) == []


class _Field:
    def __init__(self, placeholder, text):
        self.PlaceHolder = placeholder
        self._text = text
        self.anchor = MagicMock()
        self.anchor.getText.return_value = text

    def supportsService(self, name):
        return name == "com.sun.star.text.TextField.JumpEdit"

    def getAnchor(self):
        return self.anchor


def _doc_with_fields(*placeholders):
    text = MagicMock()
    cursors = []

    def make_cursor(_range):
        cursor = MagicMock()
        cursors.append(cursor)
        return cursor

    text.createTextCursorByRange.side_effect = make_cursor
    fields = [_Field(p, text) for p in placeholders]
    enum = MagicMock()
    enum.hasMoreElements.side_effect = [True] * len(fields) + [False]
    enum.nextElement.side_effect = fields
    doc = MagicMock()
    doc.getText.return_value.compareRegionStarts.return_value = 0   # ordre déjà bon
    doc.getTextFields.return_value.createEnumeration.return_value = enum
    doc.findAll.return_value.getCount.return_value = 0
    doc.getBookmarks.return_value.getElementNames.return_value = ()
    created = []

    def create(service):
        created.append(service)
        return MagicMock()

    doc.createInstance.side_effect = create
    return doc, text, cursors, created


def test_fill_inserts_values_as_text_and_creates_only_bookmarks():
    doc, text, cursors, created = _doc_with_fields("<etranger.nom>", "<etranger.url>",
                                                   "<etranger.alias>")
    hostile = '=WEBSERVICE("https://x") vnd.sun.star.script:Standard.M.x?language=Basic'
    filled, empty = fill.fill(doc, {"etranger.nom": "KOFFI", "etranger.url": hostile,
                                    "etranger.alias": None})
    assert filled == ["etranger.nom", "etranger.url"]
    assert empty == ["etranger.alias"]
    assert [c.setString.call_args.args[0] for c in cursors] == ["KOFFI", hostile]
    assert created == ["com.sun.star.text.Bookmark", "com.sun.star.text.Bookmark"]
    assert text.removeTextContent.call_count == 2           # les deux champs remplis
    doc.getUndoManager.return_value.enterUndoContext.assert_called_once_with(fill.UNDO_LABEL)
    doc.getUndoManager.return_value.leaveUndoContext.assert_called_once()
