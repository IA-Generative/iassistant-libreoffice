"""`writer_replace_paragraphs` — le pendant écriture de la carte du document.

Sans cet outil, le moteur savait LIRE le document numéroté ([P1], [P2]…) mais
n'avait aucun moyen d'agir dessus hors sélection. Une demande du type
« restructure ce document en deux paragraphes » recevait alors une réponse en
texte, le document restant inchangé — l'utilisateur voyait « il ne se passe
rien » alors que le run se terminait en succès.

Le fake ci-dessous manipule des RÉFÉRENCES de paragraphes, comme UNO : c'est ce
qui permet de vérifier que chaque paragraphe garde son propre style, et qu'une
suppression n'invalide pas les autres.
"""

from src.mirai.core.context import ToolContext
from src.mirai.core.tools.writer_tools import replace_paragraphs
from tests.stubs.fake_shell import FakeShell


class FakeParagraph:
    """Paragraphe à état réel, porteur de son texte ET de son style."""

    def __init__(self, text, style="Standard"):
        self.text = text
        self.style = style

    def supportsService(self, name):
        return name == "com.sun.star.text.Paragraph"

    def getString(self):
        return self.text

    def setString(self, text):
        self.text = text

    def getPropertyValue(self, name):
        if name == "ParaStyleName":
            return self.style
        raise AttributeError(name)

    def getStart(self):
        return ("start", self)

    def getEnd(self):
        return ("end", self)


class FakeCursor:
    def __init__(self, doc, position):
        self._doc = doc
        self._anchor = position[1]

    def append_paragraph(self):
        index = self._doc.items.index(self._anchor)
        # Un paragraphe ajouté hérite du style de celui qui le précède.
        created = FakeParagraph("", self._anchor.style)
        self._doc.items.insert(index + 1, created)
        self._anchor = created

    def write_last(self, text):
        self._anchor.text += text


class FakeText:
    def __init__(self, doc):
        self._doc = doc

    def createEnumeration(self):
        return FakeEnumeration(self._doc)

    def createTextCursorByRange(self, position):
        return FakeCursor(self._doc, position)

    def removeTextContent(self, para):
        self._doc.items.remove(para)

    def insertControlCharacter(self, cursor, _char, _absorb):
        cursor.append_paragraph()

    def insertString(self, cursor, text, _absorb):
        cursor.write_last(text)


class FakeEnumeration:
    def __init__(self, doc):
        self._items = list(doc.items)

    def hasMoreElements(self):
        return bool(self._items)

    def nextElement(self):
        return self._items.pop(0)


class FakeDoc:
    """Document Writer à état réel : les remplacements sont observables."""

    def __init__(self, paragraphs, styles=None):
        styles = list(styles or ["Standard"] * len(paragraphs))
        self.items = [FakeParagraph(t, s) for t, s in zip(paragraphs, styles, strict=True)]
        self.Text = FakeText(self)

    @property
    def paragraphs(self):
        return [p.text for p in self.items]

    @property
    def styles(self):
        return [p.style for p in self.items]

    def getUndoManager(self):
        return self

    def enterUndoContext(self, _label):
        pass

    def leaveUndoContext(self):
        pass


def _ctx(paragraphs, styles=None):
    doc = FakeDoc(paragraphs, styles)
    return ToolContext(doc, object(), "writer", FakeShell()), doc


def test_replaces_a_single_paragraph():
    ctx, doc = _ctx(["Premier.", "Deuxième.", "Troisième."])

    result = replace_paragraphs(ctx, {"start": 2, "text": "Remplacé."})

    assert result.ok
    assert doc.paragraphs == ["Premier.", "Remplacé.", "Troisième."]


def test_replaces_a_range():
    ctx, doc = _ctx(["A", "B", "C", "D"])

    result = replace_paragraphs(ctx, {"start": 2, "end": 3, "text": "Fusion."})

    assert result.ok
    assert doc.paragraphs == ["A", "Fusion.", "D"]
    assert result.data["replaced"] == 2


def test_newlines_create_paragraphs():
    """Le cas qui motive l'outil : « restructure en deux paragraphes »."""
    ctx, doc = _ctx(["Un.", "Deux.", "Trois.", "Quatre.", "Cinq."])

    result = replace_paragraphs(
        ctx, {"start": 1, "end": 5, "text": "Bloc un.\nBloc deux."})

    assert result.ok
    assert doc.paragraphs == ["Bloc un.", "Bloc deux."]


def test_end_is_clamped_to_the_document():
    """Un modèle qui vise trop loin ne doit pas faire échouer l'opération."""
    ctx, doc = _ctx(["A", "B"])

    result = replace_paragraphs(ctx, {"start": 1, "end": 99, "text": "Tout."})

    assert result.ok
    assert doc.paragraphs == ["Tout."]


def test_start_beyond_the_document_is_refused():
    ctx, doc = _ctx(["A", "B"])

    result = replace_paragraphs(ctx, {"start": 7, "text": "X"})

    assert not result.ok
    assert "2 paragraphe" in result.error
    assert doc.paragraphs == ["A", "B"], "le document ne doit pas être touché"


def test_invalid_range_is_refused():
    ctx, doc = _ctx(["A", "B", "C"])

    assert not replace_paragraphs(ctx, {"start": 0, "text": "X"}).ok
    assert not replace_paragraphs(ctx, {"start": 3, "end": 2, "text": "X"}).ok
    assert doc.paragraphs == ["A", "B", "C"]


def test_tool_is_registered_for_writer():
    """Sans enregistrement, l'outil serait invisible pour le modèle."""
    from src.mirai.core.registry import ToolRegistry
    from src.mirai.core.tools import register_all

    names = [t["function"]["name"]
             for t in register_all(ToolRegistry()).openai_tools("writer")]
    assert "writer_replace_paragraphs" in names


def test_tool_is_not_offered_in_calc():
    from src.mirai.core.registry import ToolRegistry
    from src.mirai.core.tools import register_all

    names = [t["function"]["name"]
             for t in register_all(ToolRegistry()).openai_tools("calc")]
    assert "writer_replace_paragraphs" not in names


def test_each_paragraph_keeps_its_own_style():
    """Le défaut observé : tout le document repassait en style « Titre ».

    Un `setString` sur une étendue couvrant plusieurs paragraphes applique le
    style du PREMIER à l'ensemble. Écrire dans chaque paragraphe séparément est
    la seule façon de préserver un document mixte titre + corps.
    """
    ctx, doc = _ctx(["Titre du document", "Corps un.", "Corps deux."],
                    styles=["Heading 1", "Standard", "Standard"])

    result = replace_paragraphs(
        ctx, {"start": 1, "end": 3, "text": "Nouveau titre\nNouveau corps.\nSuite."})

    assert result.ok
    assert doc.styles == ["Heading 1", "Standard", "Standard"], (
        "chaque paragraphe doit conserver SON style ; observé : "
        f"{doc.styles}")
    assert doc.paragraphs == ["Nouveau titre", "Nouveau corps.", "Suite."]


def test_added_paragraphs_inherit_the_last_style_not_the_first():
    """Un paragraphe ajouté suit le corps du texte, pas le titre."""
    ctx, doc = _ctx(["Titre", "Corps."], styles=["Heading 1", "Standard"])

    replace_paragraphs(ctx, {"start": 1, "end": 2, "text": "T\nA\nB\nC"})

    assert doc.styles == ["Heading 1", "Standard", "Standard", "Standard"]


def test_removing_paragraphs_keeps_remaining_styles():
    ctx, doc = _ctx(["Titre", "A", "B", "Note"],
                    styles=["Heading 1", "Standard", "Standard", "Quotations"])

    replace_paragraphs(ctx, {"start": 2, "end": 3, "text": "Fusion."})

    assert doc.paragraphs == ["Titre", "Fusion.", "Note"]
    assert doc.styles == ["Heading 1", "Standard", "Quotations"]


def test_document_map_exposes_styles_and_total():
    """Le modèle doit VOIR les styles et la fin du document.

    Sans le style, il fusionne un titre avec le corps sans le savoir ; sans le
    total, une demande portant sur « tout le document » s'arrête en chemin.
    """
    from src.mirai.core.tools.writer_tools import get_document_map

    ctx, _doc = _ctx(["Titre", "Corps."], styles=["Heading 1", "Standard"])
    result = get_document_map(ctx, {})

    assert "<Heading 1>" in result.content
    assert "Standard" not in result.content, "le style par défaut reste implicite"
    assert "FIN DU DOCUMENT — 2 paragraphes" in result.content
    assert result.data["paragraph_count"] == 2
