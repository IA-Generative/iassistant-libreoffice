"""Faux documents Writer/Calc à état réel (pas de simples MagicMock) pour
asserter l'état final du document dans les tests golden des presets/tools."""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.stubs.thread_guard import UnoStruct


class FakeUndoManager:
    def __init__(self):
        self.entered = []
        self.left = 0

    def enterUndoContext(self, label):
        self.entered.append(label)

    def leaveUndoContext(self):
        self.left += 1


class FakeCursor:
    def __init__(self, doc=None):
        self._doc = doc
        self.selected_text = ""

    def collapseToEnd(self):
        pass

    def getEnd(self):
        return self

    # Ciblage du paragraphe courant (XParagraphCursor)
    def gotoStartOfParagraph(self, expand):
        pass

    def gotoEndOfParagraph(self, expand):
        self.selected_text = getattr(self._doc, "current_paragraph", "") or ""

    def getString(self):
        return self.selected_text

    def setString(self, value):
        self.selected_text = value
        self._doc.replaced = value

    def getText(self):
        return self._doc.text_obj


class FakeWriterText:
    def __init__(self, doc):
        self._doc = doc

    def createTextCursorByRange(self, rng):
        return FakeCursor(self._doc)

    def createTextCursor(self):
        return FakeCursor(self._doc)

    def insertString(self, cursor, text, absorb):
        self._doc.inserted += text

    def createEnumeration(self):
        return _FakeParagraphEnum(self._doc.paragraphs)


class _FakeParagraph:
    def __init__(self, content):
        self._content = content

    def supportsService(self, name):
        return name == "com.sun.star.text.Paragraph"

    def getString(self):
        return self._content


class _FakeParagraphEnum:
    def __init__(self, paragraphs):
        self._items = [_FakeParagraph(p) for p in paragraphs]
        self._index = 0

    def hasMoreElements(self):
        return self._index < len(self._items)

    def nextElement(self):
        item = self._items[self._index]
        self._index += 1
        return item


class FakeTextRange:
    def __init__(self, doc, text):
        self._doc = doc
        self._text = text

    def getString(self):
        return self._text

    def setString(self, value):
        self._doc.replaced = value
        self._text = value

    def getText(self):
        return self._doc.text_obj


class FakeWriterDoc:
    """Document Writer factice : model + controller + sélection."""

    def __init__(self, selection_text="", paragraphs=None, current_paragraph=""):
        self.inserted = ""            # tout ce qui a été inséré au fil de l'eau
        self.replaced = None          # dernière valeur de setString sur la sélection
        self.paragraphs = paragraphs or []
        self.current_paragraph = current_paragraph  # paragraphe sous le curseur
        self.text_obj = FakeWriterText(self)
        self.undo = FakeUndoManager()
        self.selection_range = FakeTextRange(self, selection_text)
        self.selected = []            # appels controller.select

        def _select(target):
            self.selected.append(target)
            # Sélectionner un curseur-paragraphe fait du paragraphe la sélection
            if isinstance(target, FakeCursor) and target.selected_text:
                self.selection_range = FakeTextRange(self, target.selected_text)

        controller = SimpleNamespace()
        controller.getSelection = lambda: SimpleNamespace(
            getCount=lambda: 1, getByIndex=lambda i: self.selection_range)
        controller.getViewCursor = lambda: MagicMock()
        controller.select = _select
        self.controller = controller

        # Interface "model" UNO
        self.Text = self.text_obj
        self.CurrentController = controller

    def getUndoManager(self):
        return self.undo

    # Recherche (writer_find_replace)
    def createSearchDescriptor(self):
        return SimpleNamespace(SearchString="", SearchCaseSensitive=True)

    def findFirst(self, descriptor):
        needle = descriptor.SearchString
        haystack = "\n".join(self.paragraphs)
        if needle and needle in haystack:
            return _FakeFound(self, needle)
        return None

    def findNext(self, position, descriptor):
        return None


class _FakeFound:
    def __init__(self, doc, needle):
        self._doc = doc
        self._needle = needle

    def setString(self, replacement):
        self._doc.paragraphs = [
            p.replace(self._needle, replacement, 1) if self._needle in p else p
            for p in self._doc.paragraphs
        ]

    def getEnd(self):
        return self


class FakeCell:
    def __init__(self, sheet, col, row):
        self._sheet = sheet
        self._col = col
        self._row = row
        self.properties = {}

    def getString(self):
        return self._sheet.grid.get((self._col, self._row), "")

    def setString(self, value):
        self._sheet.grid[(self._col, self._row)] = value

    def setFormula(self, formula):
        self._sheet.formulas[(self._col, self._row)] = formula
        # Simule l'évaluation : la valeur affichée est fournie par le test.
        display = self._sheet.formula_results.get(formula, formula)
        self._sheet.grid[(self._col, self._row)] = display

    def getFormula(self):
        return self._sheet.formulas.get((self._col, self._row), "")

    def getError(self):
        return self._sheet.formula_errors.get(self.getFormula(), 0)

    def getCellAddress(self):
        return UnoStruct(Sheet=0, Column=self._col, Row=self._row)

    def setPropertyValue(self, name, value):
        self.properties[name] = value

    def getPropertyValue(self, name):
        if name in self.properties:
            return self.properties[name]
        raise RuntimeError(f"property {name}")

    def getText(self):
        raise RuntimeError("no XText on fake cell")  # force le repli setString


class _FakeAxisEntry:
    def __init__(self):
        self.OptimalHeight = False
        self.Height = 1000
        self.OptimalWidth = False
        self.Width = 9000


class _FakeAxis:
    def __init__(self, count):
        self.Count = count
        self._entries = {}

    def getByIndex(self, index):
        return self._entries.setdefault(index, _FakeAxisEntry())


def _parse_ref(ref):
    """'C4' → (2, 3) ; 'A1:B5' → adresse de plage."""
    match = re.match(r"^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$", ref.strip())
    if not match:
        raise ValueError(f"référence invalide : {ref}")

    def col_index(letters):
        value = 0
        for ch in letters:
            value = value * 26 + (ord(ch) - ord("A") + 1)
        return value - 1

    start_col, start_row = col_index(match.group(1)), int(match.group(2)) - 1
    if match.group(3):
        end_col, end_row = col_index(match.group(3)), int(match.group(4)) - 1
    else:
        end_col, end_row = start_col, start_row
    return UnoStruct(StartColumn=start_col, StartRow=start_row,
                     EndColumn=end_col, EndRow=end_row)


class _FakeRange:
    def __init__(self, sheet, area):
        self._sheet = sheet
        self._area = area

    def getRangeAddress(self):
        return self._area

    def getCellByPosition(self, col, row):
        return self._sheet.getCellByPosition(
            self._area.StartColumn + col, self._area.StartRow + row)

    def merge(self, value):
        self._sheet.merged.append((self._area, value))


class _FakeSheetCursor:
    """Curseur de feuille. Seule la fin de la zone utilisée est simulée, telle
    que Calc la donne pour une feuille sans mise en forme : la dernière colonne
    et la dernière ligne qui portent un contenu, A1 sur une feuille vide."""

    def __init__(self, sheet):
        self._sheet = sheet
        self._address = UnoStruct(StartColumn=0, StartRow=0, EndColumn=0, EndRow=0)

    def gotoEndOfUsedArea(self, expand):
        filled = [key for key, value in self._sheet.grid.items() if value]
        filled += list(self._sheet.formulas)
        col = max((c for c, _ in filled), default=0)
        row = max((r for _, r in filled), default=0)
        self._address = UnoStruct(StartColumn=col, StartRow=row, EndColumn=col, EndRow=row)

    def getRangeAddress(self):
        return self._address


class FakeCalcSheet:
    def __init__(self, grid=None, num_cols=50, num_rows=200):
        self.grid = dict(grid or {})          # (col, row) → str
        self.formulas = {}
        self.formula_results = {}             # formule → valeur affichée simulée
        self.formula_errors = {}              # formule → code d'erreur Calc simulé
        self.merged = []
        self.protected = False
        self.cells_asked = 0                  # cellules demandées, lues ou écrites
        self._columns = _FakeAxis(num_cols)
        self._rows = _FakeAxis(num_rows)

    def getCellByPosition(self, col, row):
        self.cells_asked += 1
        return FakeCell(self, col, row)

    def isProtected(self):
        return self.protected

    def getCellRangeByName(self, ref):
        return _FakeRange(self, _parse_ref(ref))

    def getCellRangeByPosition(self, start_col, start_row, end_col, end_row):
        area = UnoStruct(StartColumn=start_col, StartRow=start_row,
                         EndColumn=end_col, EndRow=end_row)
        return _FakeRange(self, area)

    def getColumns(self):
        return self._columns

    def createCursor(self):
        return _FakeSheetCursor(self)

    def getRows(self):
        return self._rows


_REL_REF = re.compile(r"(?<![A-Za-z_.$])(\$?[A-Z]{1,3})(\$?)(\d+)(?![\w(])")
_STRING = re.compile(r'("(?:[^"]|"")*")')


class _FakeFormulaParser:
    """Comme Calc à la copie : décale les lignes relatives, laisse les lignes
    ancrées par $, les littéraux "…" et les noms de fonction (LOG10) intacts.
    Comme Calc, printFormula rend la formule sans le « = » initial."""

    def parseFormula(self, formula, address):
        return formula.lstrip("="), address.Row

    def printFormula(self, tokens, address):
        text, src_row = tokens
        delta = address.Row - src_row

        def shift(match):
            if match.group(2) == "$":
                return match.group(0)
            return f"{match.group(1)}{int(match.group(3)) + delta}"

        return "".join(part if part.startswith('"') else _REL_REF.sub(shift, part)
                       for part in _STRING.split(text))


class FakeCalcDoc:
    """Document Calc factice : ActiveSheet + sélection rectangulaire."""

    def __init__(self, sheet, selection_ref="A1:A1"):
        self.sheet = sheet
        self.undo = FakeUndoManager()
        area = _parse_ref(selection_ref)
        controller = SimpleNamespace()
        controller.ActiveSheet = sheet
        controller.getSelection = lambda: SimpleNamespace(
            getRangeAddress=lambda: area)
        self.controller = controller
        self.CurrentController = controller
        self.Sheets = MagicMock()

    def getUndoManager(self):
        return self.undo

    def createInstance(self, name):
        assert name == "com.sun.star.sheet.FormulaParser", name
        return _FakeFormulaParser()
