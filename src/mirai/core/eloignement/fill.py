"""Balises d'un document Writer : les repérer, les remplir, les actualiser.

Éloignement reste la seule source des balises et de leurs valeurs : ce module
ne connaît aucune balise par son nom. Il lit celles que le document contient
(champs « espace réservé » des modèles Relatorio, ou texte `<prefixe.feuille>`
resté visible) et y écrit les valeurs reçues.

Une valeur reçue est une DONNÉE : elle est insérée comme texte brut, jamais
comme champ, macro ou lien. Un signet entoure chaque valeur insérée ; son nom
porte la balise et une empreinte courte de la valeur, ce qui permet, à
l'actualisation, de savoir si l'agent a modifié le passage à la main sans rien
stocker hors du document.

Toutes les fonctions qui reçoivent `model` s'exécutent sur le thread principal.
"""

import dataclasses
import functools
import hashlib
import re

UNDO_LABEL = "MIrAI — Éloignement"
MARK_PREFIX = "MirAI.balise|"
# Signets posés par Éloignement à la génération (demande en cours) : sans
# empreinte, l'état « modifié à la main » y est inconnu.
SERVER_MARK_PREFIX = "balise:"

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*$")
# Texte resté visible : seulement les balises à espace de noms (`a.b`), pour
# ne pas prendre `<br>` ou `<x>` pour une balise.
LITERAL_PATTERN = r"<[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+>"
_JUMP_EDIT = "com.sun.star.text.TextField.JumpEdit"


@dataclasses.dataclass
class Placeholder:
    name: str
    kind: str        # "champ" | "texte"
    target: object   # le champ, ou la plage de texte


@dataclasses.dataclass
class Mark:
    bookmark: str    # nom complet du signet
    balise: str
    digest: str      # "" si inconnu (signet posé par Éloignement)
    text: str


@dataclasses.dataclass
class Change:
    balise: str
    bookmark: str
    current: str
    new: str
    modified_by_hand: bool   # True aussi quand on ne peut pas le savoir


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]


def balise_name(raw):
    """Nom de balise à partir du texte d'un espace réservé, ou ""."""
    text = str(raw or "").strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    return text if _NAME.match(text) else ""


def as_text(value):
    """Valeur reçue → texte inséré. None ou vide : rien à insérer."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (list, tuple)):
        return ", ".join(as_text(v) for v in value if as_text(v))
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def mark_name(balise, value, index):
    return f"{MARK_PREFIX}{balise}|{digest(value)}|{index}"


def parse_mark(name):
    """(balise, empreinte) d'un nom de signet, ou None."""
    if name.startswith(MARK_PREFIX):
        parts = name[len(MARK_PREFIX):].split("|")
        if len(parts) >= 2 and _NAME.match(parts[0]):
            return parts[0], parts[1]
        return None
    if name.startswith(SERVER_MARK_PREFIX):
        balise = name[len(SERVER_MARK_PREFIX):].split("|")[0]
        return (balise, "") if _NAME.match(balise) else None
    return None


# -- lecture -------------------------------------------------------------------

def find_placeholders(model):
    """Balises encore à remplir, dans l'ordre du document autant que possible."""
    found = []
    try:
        enum = model.getTextFields().createEnumeration()
        while enum.hasMoreElements():
            field = enum.nextElement()
            try:
                if not field.supportsService(_JUMP_EDIT):
                    continue
                name = balise_name(field.PlaceHolder)
            except Exception:
                continue
            if name:
                found.append(Placeholder(name, "champ", field))
    except Exception:
        pass
    try:
        descriptor = model.createSearchDescriptor()
        descriptor.SearchString = LITERAL_PATTERN
        descriptor.SearchRegularExpression = True
        ranges = model.findAll(descriptor)
        for index in range(ranges.getCount() if ranges else 0):
            rng = ranges.getByIndex(index)
            name = balise_name(rng.getString())
            if name:
                found.append(Placeholder(name, "texte", rng))
    except Exception:
        pass
    return _in_document_order(model, found)


def _start(item):
    return item.target.getAnchor() if item.kind == "champ" else item.target


def _in_document_order(model, items):
    """Trie par position dans le corps du texte ; ce qui n'y est pas
    (tableau, cadre) garde son rang relatif, en fin de liste."""
    try:
        body = model.getText()
    except Exception:
        return items

    def compare(a, b):
        try:
            # compareRegionStarts : 1 si a est avant b, -1 s'il est après.
            return -int(body.compareRegionStarts(_start(a), _start(b)))
        except Exception:
            return 0

    return sorted(items, key=functools.cmp_to_key(compare))


LOGIC_SCHEME = "relatorio://"


def has_template_logic(model):
    """Le document porte-t-il de la logique de modèle Relatorio : blocs
    `if`/`for` (liens relatorio://) ou expressions `${…}` ? Seul Éloignement
    sait les évaluer."""
    try:
        descriptor = model.createSearchDescriptor()
        descriptor.SearchString = r"\$\{[^}]*\}"
        descriptor.SearchRegularExpression = True
        if model.findFirst(descriptor) is not None:
            return True
    except Exception:
        pass
    try:
        paragraphs = model.getText().createEnumeration()
        while paragraphs.hasMoreElements():
            paragraph = paragraphs.nextElement()
            if not paragraph.supportsService("com.sun.star.text.Paragraph"):
                continue
            portions = paragraph.createEnumeration()
            while portions.hasMoreElements():
                url = str(getattr(portions.nextElement(), "HyperLinkURL", "") or "")
                if url.startswith(LOGIC_SCHEME):
                    return True
    except Exception:
        return False
    return False


def placeholder_names(placeholders):
    """Noms distincts, dans l'ordre de première apparition."""
    names = []
    for item in placeholders:
        if item.name not in names:
            names.append(item.name)
    return names


def read_marks(model):
    marks = []
    try:
        bookmarks = model.getBookmarks()
        names = bookmarks.getElementNames()
    except Exception:
        return marks
    for name in names:
        parsed = parse_mark(name)
        if not parsed:
            continue
        try:
            text = bookmarks.getByName(name).getAnchor().getString()
        except Exception:
            continue
        marks.append(Mark(name, parsed[0], parsed[1], text))
    return marks


# -- plans (purs, testables) -----------------------------------------------------

def refresh_plan(marks, values):
    """Écarts entre le document et le dossier. Un passage modifié à la main
    (empreinte différente, ou inconnue) est signalé : il ne sera écrasé
    qu'avec l'accord de l'agent."""
    changes = []
    for mark in marks:
        if mark.balise not in values:
            continue
        new = as_text(values.get(mark.balise))
        if new == mark.text:
            continue
        by_hand = (not mark.digest) or digest(mark.text) != mark.digest
        changes.append(Change(mark.balise, mark.bookmark, mark.text, new, by_hand))
    return changes


# -- écriture ----------------------------------------------------------------------

def _undo(model):
    try:
        return model.getUndoManager()
    except Exception:
        return None


def _insert_marked(model, text, cursor, value, balise, index):
    """Écrit `value` à la place de ce que couvre `cursor` et l'entoure d'un signet."""
    cursor.setString(value)
    bookmark = model.createInstance("com.sun.star.text.Bookmark")
    bookmark.setName(mark_name(balise, value, index))
    text.insertTextContent(cursor, bookmark, True)


def fill(model, values):
    """Remplit les balises du document avec les valeurs reçues.

    Rend (remplies, restées vides) : deux listes de noms de balises."""
    placeholders = find_placeholders(model)
    filled, empty = [], []
    undo = _undo(model)
    if undo is not None:
        undo.enterUndoContext(UNDO_LABEL)
    try:
        existing = _existing_mark_count(model)
        for index, item in enumerate(placeholders, start=existing + 1):
            value = as_text(values.get(item.name))
            if not value:
                if item.name not in empty:
                    empty.append(item.name)
                continue
            if item.kind == "champ":
                anchor = item.target.getAnchor()
                text = anchor.getText()
                cursor = text.createTextCursorByRange(anchor.getStart())
                text.removeTextContent(item.target)
            else:
                text = item.target.getText()
                cursor = text.createTextCursorByRange(item.target)
            _insert_marked(model, text, cursor, value, item.name, index)
            if item.name not in filled:
                filled.append(item.name)
    finally:
        if undo is not None:
            undo.leaveUndoContext()
    return filled, [name for name in empty if name not in filled]


def apply_changes(model, changes):
    """Écrit les écarts acceptés. Chaque signet est reposé avec la nouvelle
    empreinte. Rend le nombre de passages écrits."""
    if not changes:
        return 0
    written = 0
    bookmarks = model.getBookmarks()
    undo = _undo(model)
    if undo is not None:
        undo.enterUndoContext(UNDO_LABEL)
    try:
        index = _existing_mark_count(model) + 1
        for change in changes:
            try:
                bookmark = bookmarks.getByName(change.bookmark)
            except Exception:
                continue
            anchor = bookmark.getAnchor()
            text = anchor.getText()
            cursor = text.createTextCursorByRange(anchor)
            text.removeTextContent(bookmark)
            _insert_marked(model, text, cursor, change.new, change.balise, index)
            index += 1
            written += 1
    finally:
        if undo is not None:
            undo.leaveUndoContext()
    return written


def go_to(model, balise):
    """Sélectionne la première occurrence encore vide de la balise."""
    for item in find_placeholders(model):
        if item.name != balise:
            continue
        try:
            target = item.target.getAnchor() if item.kind == "champ" else item.target
            model.getCurrentController().select(target)
            return True
        except Exception:
            return False
    return False


def _existing_mark_count(model):
    try:
        return sum(1 for name in model.getBookmarks().getElementNames()
                   if name.startswith(MARK_PREFIX))
    except Exception:
        return 0
