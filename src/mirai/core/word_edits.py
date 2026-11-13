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

# XTextCursor.goRight prend un short.
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
    old_lines, new_lines = old.split("\n"), new.split("\n")
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


def _segment(old, new, offset):
    before, after = _TOKEN.findall(old), _TOKEN.findall(new)
    starts = [offset]
    for token in before:
        starts.append(starts[-1] + len(token))
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    return [(starts[i1], starts[i2], "".join(after[j1:j2]))
            for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _go_right(cursor, count, expand):
    while count > 0:
        step = min(count, _MAX_STEP)
        cursor.goRight(step, expand)
        count -= step


def apply(rng, old, new, edits):
    """Écrit `new` dans `rng`, qui contient `old`, par les remplacements
    `edits` de `plan`.

    Avant chaque remplacement, le curseur doit couvrir le texte attendu. Sinon
    (un champ, une note ou un saut de paragraphe ne compte pas comme dans le
    texte lu), tout le texte est écrit d'un coup, comme les autres actions.
    Thread principal exclusivement."""
    text = rng.getText()
    for start, end, replacement in edits:
        cursor = text.createTextCursorByRange(rng.getStart())
        _go_right(cursor, start, False)
        _go_right(cursor, end - start, True)
        if cursor.getString() != old[start:end]:
            rng.setString(new)
            return
        cursor.setString(replacement)
