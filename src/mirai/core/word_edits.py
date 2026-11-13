"""Écriture d'un texte récrit dans une plage Writer, par morceaux.

Remplacer le texte d'une plage d'un seul coup lui fait perdre sa mise en forme
interne (gras, italique, liens) : le nouveau texte prend celle du premier
caractère. Sur plusieurs paragraphes, Writer les fond même en un seul. Ici,
seuls les mots, ou les lignes, qui changent sont remplacés : le reste garde sa
mise en forme, et chaque paragraphe sa place.
"""

import difflib
import os
import re

# Un mot, une suite d'espaces ou un signe : la comparaison se fait à ce grain,
# pour qu'une virgule ajoutée ne remplace pas le mot qui la précède.
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")

# Portions qui occupent une seule position du texte mais se lisent autrement
# qu'un caractère : une note (son numéro), un commentaire (rien), un champ (sa
# valeur), une image ancrée comme caractère (rien).
_OBJECTS = {"Footnote", "Annotation", "AnnotationEnd", "TextField", "Frame"}

# XTextCursor.goRight et goLeft prennent un short.
_MAX_STEP = 2 ** 15 - 1


def plan(old, new, by_word=True):
    """(début, fin, texte) de chaque passage de `old` à remplacer pour obtenir
    `new`, du dernier au premier : écrire dans cet ordre ne décale pas les
    positions qui restent à traiter.

    Le texte est pris ligne à ligne (paragraphe ou saut de ligne manuel), et
    `new` en a autant que `old` (voir `fit_lines`). Avec `by_word`, chaque
    ligne est comparée mot à mot ; sans, une ligne qui change est remplacée
    entière.

    Chaque remplacement se réduit aux caractères qui changent : une note
    collée à un mot dont une lettre change reste hors du remplacement.

    Calcul pur, à faire dans le worker : comparer un long texte prend du
    temps, qui ne doit pas figer LibreOffice."""
    old, new = _plain(old), _plain(new)
    edits, offset = [], 0
    for old_line, new_line in zip(old.split("\n"), new.split("\n"), strict=True):
        if by_word:
            edits.extend(_segment(old_line, new_line, offset))
        elif old_line != new_line:
            edits.append((offset, offset + len(old_line), new_line))
        offset += len(old_line) + 1
    return [_trim(old, edit) for edit in edits[::-1]]


def fit_lines(original, reply):
    """`reply` posée ligne à ligne sur `original` : chaque ligne non vide
    rendue prend la place d'une ligne non vide lue, entre ses blancs de bord,
    et les lignes vides lues restent où elles sont. None si `reply` n'a pas
    autant de lignes non vides qu'`original` : elles ne sauraient plus où
    aller."""
    lines = _plain(original).split("\n")
    answers = [line for line in _plain(reply).split("\n") if line.strip()]
    if len(answers) != sum(1 for line in lines if line.strip()):
        return None
    answers = iter(answers)
    return "\n".join(keep_edges(line, next(answers)) if line.strip() else line
                     for line in lines)


def keep_edges(sent, answer):
    """`answer` entre les blancs de bord de `sent` : un retrait, une tabulation
    ou une espace finale que le modèle a retirés n'avaient pas à changer."""
    head = sent[:len(sent) - len(sent.lstrip())]
    return head + answer.strip() + sent[len(sent.rstrip()):]


def _trim(old, edit):
    start, end, new = edit
    span = old[start:end]
    head = len(os.path.commonprefix([span, new]))
    tail = len(os.path.commonprefix([span[head:][::-1], new[head:][::-1]]))
    return start + head, end - tail, new[head:len(new) - tail]


def _plain(text):
    """Sous Windows, Writer sépare les paragraphes d'une plage lue par « \\r\\n »,
    qui n'occupe pourtant qu'une position, comme le « \\n » des autres systèmes."""
    return text.replace("\r\n", "\n")


def _segment(old, new, offset):
    before, after = _TOKEN.findall(old), _TOKEN.findall(new)
    starts = [offset]
    for token in before:
        starts.append(starts[-1] + len(token))
    matcher = difflib.SequenceMatcher(None, [_key(t) for t in before],
                                      [_key(t) for t in after], autojunk=False)
    return [(starts[i1], starts[i2], "".join(after[j1:j2]))
            for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _key(token):
    """Une espace simple rendue à la place d'une espace insécable ou d'une
    tabulation n'est pas une correction : les deux comparent égales."""
    return " " if len(token) == 1 and token.isspace() else token


def _fits(text, rng, cursor, old, edits):
    """`cursor`, posé sur `rng`, se lit position par position comme `old`, et
    aucun remplacement ne couvre un objet ancré, qu'il effacerait."""
    if _plain(cursor.getString()) != old or text.compareRegionEnds(cursor, rng):
        return False
    objects = _objects(cursor)
    if any(len(read) != 1 for _, read in objects):
        return False
    return not any(start <= offset < end for offset, _ in objects for start, end, _ in edits)


def _objects(cursor):
    """(position, lecture) de chaque objet ancré que couvre `cursor`, la
    position comptée comme si chaque objet se lisait en un caractère. Un
    tableau compte pour un objet qui ne se lit pas."""
    found, offset = [], 0
    paragraphs = cursor.createEnumeration()
    while paragraphs.hasMoreElements():
        paragraph = paragraphs.nextElement()
        if not paragraph.supportsService("com.sun.star.text.Paragraph"):
            found.append((offset, ""))
            continue
        portions = paragraph.createEnumeration()
        while portions.hasMoreElements():
            portion = portions.nextElement()
            read = _plain(portion.getString())
            if portion.TextPortionType in _OBJECTS:
                found.append((offset, read))
                offset += 1
            else:
                offset += len(read)
        offset += 1          # la fin du paragraphe
    return found


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

    Rend None sans rien écrire si la plage ne se lit pas position par position
    comme `old` (un champ, un commentaire ou une image y occupe une position
    mais se lit autrement, et les remplacements tomberaient à côté), ou si un
    remplacement couvre une note, un champ, un commentaire ou une image, qu'il
    effacerait.
    Un seul curseur remonte la plage depuis sa fin, d'un remplacement au
    précédent : le thread principal ne la parcourt qu'une fois.
    Thread principal exclusivement."""
    old = _plain(old)
    text = rng.getText()
    cursor = text.createTextCursorByRange(rng.getStart())
    _go_right(cursor, len(old), True)
    if not _fits(text, rng, cursor, old, edits):
        return None
    cursor.collapseToEnd()
    position = len(old)
    for start, end, replacement in edits:
        _go_left(cursor, position - end, False)
        _go_left(cursor, end - start, True)
        cursor.setString(replacement)
        cursor.collapseToStart()
        position = start
    # Le texte avant le premier remplacement n'a pas bougé.
    _go_left(cursor, position, False)
    _go_right(cursor, len(_plain(new)), True)
    return cursor
