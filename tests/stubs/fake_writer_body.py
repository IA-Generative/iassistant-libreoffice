"""Document Writer factice dont le corps est une liste de paragraphes à état
réel : texte et style de chacun, références stables comme sous UNO.

Il sert aux actions qui portent sur tout le document : ce qu'elles écrivent,
où, avec quel style, et si l'écriture a eu lieu dans une action d'annulation,
se lit directement sur `paragraphs`, `styles` et `undo`. `TABLE` à la place
d'un texte met un tableau dans le corps ; `tables` dit combien il en reste.

Un paragraphe, comme la sélection, peut aussi être donné en passages
`[(texte, mise en forme), …]` : chaque caractère garde la mise en forme de son
passage, et `runs()` dit ce qu'il en reste après l'action.
"""

from types import SimpleNamespace

from tests.stubs.thread_guard import UnoStruct

TABLE = object()


class FakeTable:
    """Tableau du corps : l'énumération le rend comme un seul élément, sans
    le texte de ses cellules."""

    def supportsService(self, name):
        return name in ("com.sun.star.text.TextTable", "com.sun.star.text.TextContent")


class FakeParagraph:
    def __init__(self, doc, text, style):
        self._doc = doc
        if isinstance(text, list):
            self.text = "".join(part for part, _ in text)
            self.formats = [fmt for part, fmt in text for _ in part]
        else:
            self.text = text
            self.formats = [None] * len(text)
        self.style = style
        self.index = None        # index (table des matières…) qui contient le paragraphe

    def supportsService(self, name):
        return name == "com.sun.star.text.Paragraph"

    def getString(self):
        return self.text

    def setString(self, text):
        # Comme Writer : le nouveau texte prend la mise en forme du premier
        # caractère remplacé.
        self._doc.record_write()
        first = self.formats[0] if self.formats else None
        self.text = text
        self.formats = [first] * len(text)

    def runs(self):
        """[(texte, mise en forme)] : les caractères consécutifs de même mise
        en forme, regroupés."""
        result = []
        for char, fmt in zip(self.text, self.formats, strict=True):
            if result and result[-1][1] == fmt:
                result[-1] = (result[-1][0] + char, fmt)
            else:
                result.append((char, fmt))
        return result

    def getText(self):
        return self._doc.Text

    def getStart(self):
        return UnoStruct(paragraph=self, offset=0)

    def getPropertyValue(self, name):
        if name == "ParaStyleName":
            return self.style
        if name == "DocumentIndex":
            return self.index
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
        self._until = None

    def getEnd(self):
        return UnoStruct(paragraph=self.anchor)

    def gotoRange(self, position, _expand):
        self._until = position.paragraph

    def setString(self, _text):
        # Le curseur va de la fin d'un paragraphe à la fin d'un suivant :
        # effacer la plage supprime les paragraphes qu'elle couvre, et comme
        # Writer, les tableaux placés entre eux.
        self._doc.record_write()
        items = self._doc.items
        del items[items.index(self.anchor) + 1:items.index(self._until) + 1]

    def setPropertyValue(self, name, value):
        if name != "ParaStyleName":
            raise AttributeError(name)
        self.anchor.style = value


class FakeInlineCursor:
    """Curseur posé dans le texte d'un paragraphe : il avance caractère par
    caractère, sans jamais dépasser la fin du paragraphe, et remplace ce qu'il
    couvre."""

    def __init__(self, doc, paragraph, offset):
        self._doc = doc
        self.paragraph = paragraph
        self.start = self.end = offset

    def goRight(self, count, expand):
        if not -2 ** 15 <= count < 2 ** 15:
            raise OverflowError("goRight prend un short")
        whole = self.end + count <= len(self.paragraph.text)
        self.end = min(self.end + count, len(self.paragraph.text))
        if not expand:
            self.start = self.end
        return whole

    def getString(self):
        return self.paragraph.text[self.start:self.end]

    def setString(self, text):
        # Comme Writer : le texte remplaçant prend la mise en forme du premier
        # caractère remplacé ; inséré, celle du caractère qui le précède.
        self._doc.record_write()
        p = self.paragraph
        if self.end > self.start:
            fmt = p.formats[self.start]
        elif self.start:
            fmt = p.formats[self.start - 1]
        else:
            fmt = p.formats[0] if p.formats else None
        p.text = p.text[:self.start] + text + p.text[self.end:]
        p.formats = p.formats[:self.start] + [fmt] * len(text) + p.formats[self.end:]
        self.end = self.start + len(text)


class FakeBodyText:
    def __init__(self, doc):
        self._doc = doc

    def createEnumeration(self):
        items = list(self._doc.items)
        return SimpleNamespace(hasMoreElements=lambda: bool(items),
                               nextElement=lambda: items.pop(0))

    def createTextCursorByRange(self, position):
        if hasattr(position, "offset"):
            return FakeInlineCursor(self._doc, position.paragraph, position.offset)
        return FakeBodyCursor(self._doc, position)

    def removeTextContent(self, paragraph):
        # Comme Writer : quand les modifications sont enregistrées, rien
        # n'est supprimé et rien ne le signale.
        if self._doc.record_changes:
            return
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
        cursor.anchor.formats += [None] * len(text)


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

    def __init__(self, paragraphs, styles=None, selection=""):
        styles = list(styles or ["Standard"] * len(paragraphs))
        self.items = [FakeTable() if text is TABLE else FakeParagraph(self, text, style)
                      for text, style in zip(paragraphs, styles, strict=True)]
        self.Text = FakeBodyText(self)
        self.record_changes = False      # Édition > Suivi des modifications > Enregistrer
        self.undo = FakeUndo()
        self.writes = 0
        self.writes_outside_undo = 0
        # La sélection, hors du corps : un passage du document qu'une action
        # peut lire et réécrire.
        self.selection = FakeParagraph(self, selection, "Standard")
        self.controller = SimpleNamespace(
            getSelection=lambda: SimpleNamespace(getCount=lambda: 1,
                                                 getByIndex=lambda _i: self.selection),
            getViewCursor=lambda: SimpleNamespace(gotoRange=lambda *_a: None),
            select=lambda _target: None)
        self.CurrentController = self.controller

    @property
    def paragraphs(self):
        return [p.text for p in self.items if isinstance(p, FakeParagraph)]

    @property
    def styles(self):
        return [p.style for p in self.items if isinstance(p, FakeParagraph)]

    @property
    def tables(self):
        return sum(isinstance(item, FakeTable) for item in self.items)

    def record_write(self):
        self.writes += 1
        if not self.undo.open:
            self.writes_outside_undo += 1

    def getUndoManager(self):
        return self.undo
