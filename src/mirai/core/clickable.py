"""Rendre cliquables les lignes des zones de texte de la palette.

Les onglets Conversation et Suggestions sont des zones `Edit` en lecture seule.
On ne les remplace pas par des listes : une `ListBox` ne renvoie pas à la ligne,
et une réponse de l'assistant y deviendrait illisible. On garde donc le confort
de lecture, et on rend la ligne cliquable en traduisant la position du curseur.

Au clic, LibreOffice place le curseur dans la zone : `getSelection().Min` donne
un décalage en caractères. Ce module le traduit en numéro de ligne, puis en
contenu à reporter dans la zone de saisie. Il est **pur** — aucun objet UNO —
donc testable sans LibreOffice.
"""

from ..i18n import t as _t

# Préfixes posés par le rendu des suggestions et de l'analyse. Ils servent à
# l'œil, pas à la demande : les reporter tels quels dans la saisie donnerait
# « 1. ▸ Résumer la sélection » au lieu de « Résumer la sélection ».
_PREFIXES = ("▸ ", "· ", "- ", "• ")

# Le fil de conversation préfixe chaque tour. On ne renvoie que le PROPOS, pas
# l'étiquette de son auteur.
def _speakers():
    return (_t("palette.user_prefix"), _t("palette.assistant_prefix"))


def line_at(text, offset):
    """Numéro de ligne (0 en tête) contenant ce décalage en caractères."""
    if not text or offset is None or offset < 0:
        return 0
    return text.count("\n", 0, min(int(offset), len(text)))


def clean(line):
    """Retire numérotation, puce et étiquette d'auteur — rend la demande nue.

    Rend "" pour ce qui n'a pas à être rejoué : ligne vide, titre de section,
    ligne de statut. L'appelant traite "" comme « rien à faire ici ».
    """
    line = (line or "").strip()
    if not line:
        return ""
    # Numérotation « 1. » / « 2) » posée par le rendu des suggestions.
    tete = line.split(" ", 1)
    if len(tete) == 2 and tete[0].rstrip(".)").isdigit() and tete[0][:1].isdigit():
        line = tete[1].strip()
    for prefixe in _PREFIXES:
        if line.startswith(prefixe):
            line = line[len(prefixe):].strip()
            break
    for locuteur in _speakers():
        if line.startswith(locuteur):
            line = line[len(locuteur):].strip()
            break
    # Un intitulé de section se termine par « : » (« ： » en chinois) et n'est
    # pas une demande.
    if line.endswith((":", "：")):
        return ""
    return line


def payload_at(text, offset, min_chars=3):
    """Contenu à reporter dans la saisie pour un clic à ce décalage.

    Rend "" quand la ligne n'a rien de rejouable — l'appelant laisse alors la
    zone de saisie intacte plutôt que d'y coller un titre ou une ligne vide.
    """
    if not text:
        return ""
    lignes = text.split("\n")
    index = line_at(text, offset)
    if index >= len(lignes):
        return ""
    propos = clean(lignes[index])
    return propos if len(propos) >= min_chars else ""
