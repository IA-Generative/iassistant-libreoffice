"""Document Writer factice dont le corps est une liste de paragraphes à état
réel : texte et style de chacun, références stables comme sous UNO.

Il sert aux actions qui portent sur tout le document : ce qu'elles écrivent,
où, avec quel style, et si l'écriture a eu lieu dans une action d'annulation,
se lit directement sur `paragraphs`, `styles` et `undo`.
"""

from types import SimpleNamespace

from tests.stubs.thread_guard import UnoStruct


class FakeParagraph:
    def __init__(self, doc, text, style):
        self._doc = doc
        self.text = text
        self.style = style

    def supportsService(self, name):
        return name == "com.sun.star.text.Paragraph"

    def getString(self):
        return self.text

    def setString(self, text):
        self._doc.record_write()
        self.text = text

    def getPropertyValue(self, name):
        if name == "ParaStyleName":
            return self.style
        raise AttributeError(name)

    def setPropertyValue(self, name, value):
        if name != "ParaStyleName":
            raise AttributeError(name)
        self._doc.record_write()
        self.style = value

    def getEnd(self):
        # Une position rendue par valeur : sous ThreadGuard, le paragraphe
        # qu'elle désigne reste l'objet du document, pas son enveloppe.
        return UnoStruct(paragraph=self)


class FakeBodyCursor:
    """Curseur posé en fin de paragraphe : un saut de paragraphe crée le
    suivant, qui hérite du style courant, et le texte s'y écrit."""

    def __init__(self, doc, position):
        self._doc = doc
        self.anchor = position.paragraph

    def getEnd(self):
        return UnoStruct(paragraph=self.anchor)

    def setPropertyValue(self, name, value):
        if name != "ParaStyleName":
            raise AttributeError(name)
        self.anchor.style = value


class FakeBodyText:
    def __init__(self, doc):
        self._doc = doc

    def createEnumeration(self):
        items = list(self._doc.items)
        return SimpleNamespace(hasMoreElements=lambda: bool(items),
                               nextElement=lambda: items.pop(0))

    def createTextCursorByRange(self, position):
        return FakeBodyCursor(self._doc, position)

    def removeTextContent(self, paragraph):
        self._doc.record_write()
        self._doc.items.remove(paragraph)

    def insertControlCharacter(self, cursor, _char, _absorb):
        self._doc.record_write()
        created = FakeParagraph(self._doc, "", cursor.anchor.style)
        self._doc.items.insert(self._doc.items.index(cursor.anchor) + 1, created)
        cursor.anchor = created

    def insertString(self, cursor, text, _absorb):
        self._doc.record_write()
        cursor.anchor.text += text


class FakeUndo:
    def __init__(self):
        self.entered = []
        self.left = 0

    @property
    def open(self):
        return len(self.entered) > self.left

    def enterUndoContext(self, label):
        self.entered.append(label)

    def leaveUndoContext(self):
        self.left += 1


class FakeBodyDoc:
    """Document Writer : corps, contrôleur, sélection et gestionnaire
    d'annulation. Les écritures faites hors d'une action d'annulation sont
    comptées dans `writes_outside_undo`."""

    def __init__(self, paragraphs, styles=None, selection_text=""):
        styles = list(styles or ["Standard"] * len(paragraphs))
        self.items = [FakeParagraph(self, text, style)
                      for text, style in zip(paragraphs, styles, strict=True)]
        self.Text = FakeBodyText(self)
        self.undo = FakeUndo()
        self.writes = 0
        self.writes_outside_undo = 0
        selection = SimpleNamespace(getString=lambda: selection_text)
        self.controller = SimpleNamespace(
            getSelection=lambda: SimpleNamespace(getByIndex=lambda _i: selection),
            getViewCursor=lambda: SimpleNamespace(gotoRange=lambda *_a: None),
            select=lambda _target: None)
        self.CurrentController = self.controller

    @property
    def paragraphs(self):
        return [p.text for p in self.items]

    @property
    def styles(self):
        return [p.style for p in self.items]

    def record_write(self):
        self.writes += 1
        if not self.undo.open:
            self.writes_outside_undo += 1

    def getUndoManager(self):
        return self.undo
