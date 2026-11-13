"""Écriture d'un texte récrit dans une plage Writer, par morceaux.

Remplacer le texte d'une plage d'un seul coup lui fait perdre sa mise en forme
interne (gras, italique, liens) : le nouveau texte prend celle du premier
caractère. Sur plusieurs paragraphes, Writer les fond même en un seul. Ici,
seuls les mots, ou les lignes, qui changent sont remplacés : le reste garde sa
mise en forme, et chaque paragraphe sa place.
"""

import difflib
import re

# Un mot, une suite d'espaces ou un signe : la comparaison se fait à ce grain,
# pour qu'une virgule ajoutée ne remplace pas le mot qui la précède.
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")

# XTextCursor.goRight et goLeft prennent un short.
_MAX_STEP = 2 ** 15 - 1


def plan(old, new, by_word=True):
    """(début, fin, texte) de chaque passage de `old` à remplacer pour obtenir
    `new`, du dernier au premier : écrire dans cet ordre ne décale pas les
    positions qui restent à traiter.

    Le texte est pris ligne à ligne (paragraphe ou saut de ligne manuel) quand
    il en garde le nombre ; sinon, d'un seul tenant. Avec `by_word`, chaque
    ligne est comparée mot à mot ; sans, une ligne qui change est remplacée
    entière.

    Calcul pur, à faire dans le worker : comparer un long texte prend du
    temps, qui ne doit pas figer LibreOffice."""
    old_lines, new_lines = _plain(old).split("\n"), _plain(new).split("\n")
    if len(old_lines) != len(new_lines):
        old_lines, new_lines = [old], [new]
    edits, offset = [], 0
    for old_line, new_line in zip(old_lines, new_lines, strict=True):
        if by_word:
            edits.extend(_segment(old_line, new_line, offset))
        elif old_line != new_line:
            edits.append((offset, offset + len(old_line), new_line))
        offset += len(old_line) + 1
    return edits[::-1]


def _plain(text):
    """Sous Windows, Writer sépare les paragraphes d'une plage lue par « \\r\\n »,
    qui n'occupe pourtant qu'une position, comme le « \\n » des autres systèmes."""
    return text.replace("\r\n", "\n")


def _segment(old, new, offset):
    before, after = _TOKEN.findall(old), _TOKEN.findall(new)
    starts = [offset]
    for token in before:
        starts.append(starts[-1] + len(token))
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    return [(starts[i1], starts[i2], "".join(after[j1:j2]))
            for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _go_right(cursor, count, expand):
    _go(cursor.goRight, count, expand)


def _go_left(cursor, count, expand):
    _go(cursor.goLeft, count, expand)


def _go(move, count, expand):
    while count > 0:
        step = min(count, _MAX_STEP)
        move(step, expand)
        count -= step


def apply(rng, old, new, edits):
    """Écrit `new` dans `rng`, qui contient `old`, par les remplacements
    `edits` de `plan`, et rend la plage écrite, à sélectionner : `rng` ne
    couvre plus un texte remplacé à l'un de ses bords.

    Avant chaque remplacement, le curseur doit couvrir le texte attendu. Sinon
    (un champ, une note ou un saut de paragraphe ne compte pas comme dans le
    texte lu), tout le texte est écrit d'un coup, comme les autres actions.
    Un seul curseur remonte la plage depuis sa fin, d'un remplacement au
    précédent : le thread principal ne la parcourt qu'une fois.
    Thread principal exclusivement."""
    text = rng.getText()
    old = _plain(old)
    cursor = text.createTextCursorByRange(rng.getStart())
    _go_right(cursor, len(old), False)
    position = len(old)
    for start, end, replacement in edits:
        _go_left(cursor, position - end, False)
        _go_left(cursor, end - start, True)
        if _plain(cursor.getString()) != old[start:end]:
            rng.setString(new)
            return rng
        cursor.setString(replacement)
        cursor.collapseToStart()
        position = start
    # Le texte avant le premier remplacement n'a pas bougé.
    _go_left(cursor, position, False)
    _go_right(cursor, len(_plain(new)), True)
    return cursor
