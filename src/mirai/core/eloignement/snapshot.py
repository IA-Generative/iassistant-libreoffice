"""Actualiser un acte généré par Éloignement, qui ne porte aucune marque de balise.

À l'ouverture de l'acte, les valeurs du dossier telles qu'Éloignement les a
insérées sont gardées dans une propriété du document (`PROPERTY`). Pour
actualiser, l'ancienne valeur est cherchée dans le texte et ne compte que là
où rien n'y est collé (lettre, chiffre, trait d'union, apostrophe, souligné) :
« Karim » est remplacé dans « Prénom : Karim », pas dans « Karimou ». Une
valeur qu'on ne retrouve plus a été retouchée à la main : elle n'est pas
remplacée. C'est la règle du complément Word.

Les fonctions qui touchent au document sont à appeler sur le thread principal.
"""

import json
import re

from . import fill

PROPERTY = "MirAI.Eloignement.Valeurs"
_REMOVEABLE = 128          # com.sun.star.beans.PropertyAttribute.REMOVEABLE
_EDGE = r"\p{L}\p{N}_'’\-"
_ICU_SPECIAL = re.compile(r"([\\.^$|?*+()\[\]{}/-])")


def bounded_pattern(value):
    """Expression régulière (ICU, celle de la recherche LibreOffice) qui trouve
    `value` telle quelle, sans rien de collé avant ni après."""
    literal = _ICU_SPECIAL.sub(r"\\\1", value)
    return f"(?<![{_EDGE}]){literal}(?![{_EDGE}])"


def as_snapshot(values):
    """{balise: texte inséré} pour les balises qui ont une valeur."""
    out = {}
    for name, value in (values or {}).items():
        text = fill.as_text(value).strip()
        if fill.balise_name(str(name)) and text:
            out[str(name)] = text
    return out


def text_plan(snapshot, values, occurrences):
    """Écarts entre l'instantané et le dossier.

    `occurrences` : {balise: nombre d'endroits où l'ancienne valeur est
    retrouvée}. Zéro : retouchée à la main, signalée mais non remplacée."""
    changes = []
    for balise, old in snapshot.items():
        if balise not in values:
            continue
        new = fill.as_text(values[balise])
        if new == old:
            continue
        changes.append(fill.Change(balise, "", old, new, occurrences.get(balise, 0) == 0))
    return changes


# -- document ---------------------------------------------------------------------

def _props(model):
    return model.getDocumentProperties().getUserDefinedProperties()


def read_snapshot(model):
    try:
        props = _props(model)
        if not props.getPropertySetInfo().hasPropertyByName(PROPERTY):
            return {}
        data = json.loads(str(props.getPropertyValue(PROPERTY) or "{}"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}


def write_snapshot(model, snapshot):
    """Le document devient modifié."""
    props = _props(model)
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    if props.getPropertySetInfo().hasPropertyByName(PROPERTY):
        props.setPropertyValue(PROPERTY, raw)
    else:
        props.addProperty(PROPERTY, _REMOVEABLE, raw)


def _find_all(model, value):
    descriptor = model.createSearchDescriptor()
    descriptor.SearchString = bounded_pattern(value)
    descriptor.SearchRegularExpression = True
    descriptor.SearchCaseSensitive = True
    found = model.findAll(descriptor)
    return [found.getByIndex(i) for i in range(found.getCount())] if found else []


def count_occurrences(model, snapshot):
    return {balise: len(_find_all(model, old)) for balise, old in snapshot.items()}


def apply_text_changes(model, changes):
    """Remplace chaque occurrence bornée de l'ancienne valeur, en un seul
    Ctrl+Z, et met l'instantané à jour. Rend le nombre de passages écrits."""
    if not changes:
        return 0
    snapshot = read_snapshot(model)
    undo = model.getUndoManager()
    undo.enterUndoContext(fill.UNDO_LABEL)
    written = 0
    try:
        for change in changes:
            ranges = _find_all(model, change.current)
            for found in ranges:
                found.setString(change.new)
            if ranges:
                written += len(ranges)
                if change.new:
                    snapshot[change.balise] = change.new
                else:
                    snapshot.pop(change.balise, None)
        write_snapshot(model, snapshot)
    finally:
        undo.leaveUndoContext()
    return written
