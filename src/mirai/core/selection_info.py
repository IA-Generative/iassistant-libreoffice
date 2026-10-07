"""Libellé de ce sur quoi l'action va porter.

Répond directement au « il ne se passe rien » : avant même de lancer un run,
l'utilisateur doit voir la cible. Les formateurs sont **purs** — ils prennent du
texte ou des coordonnées, jamais d'objet UNO — donc testables hors LibreOffice.

La règle d'acquisition, elle, est ailleurs (ui/palette.py) : `XSelectionChangeListener`,
livré par LibreOffice sur le thread principal. Surtout pas de thread qui
interroge la sélection en boucle — c'est le contre-exemple qu'on vient
d'éliminer du code historique.
"""

from __future__ import annotations

from ..i18n import t as _t

MAX_EXCERPT = 90


def compact_whitespace(text: str) -> str:
    """Réduit tout groupe d'espaces/retours à un espace unique."""
    return " ".join((text or "").split())


def middle_ellipsis(text: str, limit: int = MAX_EXCERPT) -> str:
    """Tronque par le MILIEU, sur une frontière de mot quand c'est possible.

    Garder le début ET la fin permet de reconnaître un passage d'un coup d'œil,
    là où une troncature simple montre toujours le même début de paragraphe.
    """
    text = compact_whitespace(text)
    if len(text) <= limit:
        return text
    keep = (limit - 1) // 2
    head, tail = text[:keep], text[-keep:]
    space = head.rfind(" ")
    if space > keep // 2:
        head = head[:space]
    space = tail.find(" ")
    if -1 < space < keep // 2:
        tail = tail[space + 1:]
    return f"{head}…{tail}"


def writer_label(selected_text: str, paragraph_text: str = "") -> str:
    """Libellé Writer, adapté au ciblage réel de l'action.

    Sans sélection, le libellé citait le paragraphe sous le curseur. Curseur en
    tête de document, il affichait donc le TITRE — « Paragraphe courant :
    « OpenClaw » » — ce qui laissait croire que l'action ne porterait que sur
    cette ligne. Or l'orchestrateur annonce au modèle « aucune sélection, donc
    DOCUMENT ENTIER » (cf. `orchestrator._scope_line`).

    Les deux cibles coexistent selon le chemin emprunté : une demande libre
    porte sur tout le document, un preset sur le paragraphe courant
    (`presets._target_selection_text`). Le libellé nomme donc la portée large,
    la plus lourde de conséquences, et mentionne l'autre — plutôt que de citer
    un extrait qui n'engage rien.
    """
    selected = compact_whitespace(selected_text)
    if selected:
        return _t("sel.writer_selection", excerpt=middle_ellipsis(selected))
    if compact_whitespace(paragraph_text):
        return _t("sel.no_selection")
    return _t("sel.no_target")


def column_letter(index: int) -> str:
    """Index de colonne (0 = A) → lettre(s) de colonne."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def calc_label(start_col: int, start_row: int, end_col: int, end_row: int) -> str:
    """Libellé Calc : « 5 cellules sélectionnées (A4:A8) ».

    Reprend la formulation historique — les utilisateurs la connaissent.
    """
    columns = abs(end_col - start_col) + 1
    rows = abs(end_row - start_row) + 1
    count = columns * rows
    first = f"{column_letter(min(start_col, end_col))}{min(start_row, end_row) + 1}"
    if count == 1:
        return _t("sel.calc_cell", ref=first)
    last = f"{column_letter(max(start_col, end_col))}{max(start_row, end_row) + 1}"
    return _t("sel.calc_range", count=count, first=first, last=last)
