"""Recommandations contextuelles par heuristiques (application, sélection,
nature du contenu). Sans LLM.

Ce module ne connaît ni UNO ni la palette : il reçoit une description de la
situation et rend une liste de suggestions. L'IHM décide quoi en faire.
"""

from __future__ import annotations

import dataclasses
import re

LONG_SELECTION_CHARS = 400      # au-delà, résumer devient l'action évidente
SHORT_SELECTION_CHARS = 80      # en deçà, on est sur une phrase, pas un passage


@dataclasses.dataclass(frozen=True)
class Suggestion:
    """Une proposition cliquable.

    `preset_id` renseigné → un clic lance directement l'action.
    Sinon le libellé pré-remplit le champ de prompt, sans rien exécuter :
    l'utilisateur garde la main.
    """
    label: str
    prompt: str = ""
    preset_id: str = ""

    @property
    def runs_immediately(self) -> bool:
        return bool(self.preset_id)


_NUMERIC = re.compile(r"^-?[\d\s.,%€$]+$")


def looks_numeric(values) -> bool:
    """Vrai si l'essentiel des valeurs sont des nombres.

    Tolère quelques cellules de texte : un en-tête de colonne ne doit pas
    disqualifier une plage de chiffres.
    """
    cleaned = [str(v).strip() for v in (values or []) if str(v).strip()]
    if len(cleaned) < 2:
        return False
    numeric = sum(1 for v in cleaned if _NUMERIC.match(v))
    return numeric >= max(2, int(len(cleaned) * 0.7))


def _writer_suggestions(selected_text: str, has_paragraph: bool):
    length = len(selected_text.strip())
    items = []

    if length >= LONG_SELECTION_CHARS:
        items.append(Suggestion("Résumer ce passage", preset_id="summarize"))
        items.append(Suggestion("Le rendre plus court", preset_id="shorten"))
    elif length >= SHORT_SELECTION_CHARS:
        items.append(Suggestion("Simplifier la formulation", preset_id="simplify"))
        items.append(Suggestion("Développer ce passage", preset_id="lengthen"))
    elif length > 0:
        items.append(Suggestion("Développer cette phrase", preset_id="lengthen"))
    elif has_paragraph:
        # Sans sélection les actions ciblent le paragraphe courant : proposer
        # autre chose que « sélectionnez du texte » serait décourageant.
        items.append(Suggestion("Simplifier ce paragraphe", preset_id="simplify"))

    items.extend((
        Suggestion("Corrige l'orthographe et la grammaire",
                   prompt="Corrige l'orthographe, la grammaire et la syntaxe, "
                          "sans reformuler."),
        Suggestion("Rends le ton plus formel",
                   prompt="Réécris ce passage sur un ton plus formel et "
                          "administratif."),
        Suggestion("Traduis en anglais",
                   prompt="Traduis ce passage en anglais."),
    ))
    return items


def _calc_suggestions(cell_count: int, values):
    items = []
    if cell_count == 0:
        items.append(Suggestion(
            "Écrire une formule",
            prompt="Écris une formule qui "))
    elif looks_numeric(values):
        items.append(Suggestion("Analyser ces chiffres", preset_id="analyze"))
        items.append(Suggestion(
            "Calculer la moyenne et le total",
            prompt="Donne la moyenne, le total et les valeurs extrêmes de "
                   "cette plage."))
    else:
        items.append(Suggestion(
            "Mettre en majuscules",
            prompt="Mets chaque valeur en majuscules."))
        items.append(Suggestion(
            "Classer par catégorie",
            prompt="Classe chaque valeur par catégorie."))

    items.append(Suggestion(
        "Repérer les anomalies",
        prompt="Repère les valeurs incohérentes ou aberrantes."))
    return items


def suggest(app: str, selected_text: str = "", has_paragraph: bool = False,
            cell_count: int = 0, values=None, limit: int = 5):
    """Rend les suggestions pertinentes pour la situation décrite.

    `app` vaut "writer" ou "calc". Les autres paramètres décrivent la cible ;
    aucun n'est obligatoire — une situation inconnue rend une liste courte
    plutôt qu'une liste vide.
    """
    if app == "calc":
        items = _calc_suggestions(cell_count, values)
    else:
        items = _writer_suggestions(selected_text or "", has_paragraph)
    return items[:limit]


def render(suggestions) -> str:
    """Rend les suggestions en texte, une par ligne, pour la zone basse.

    Les propositions qui s'exécutent directement portent « ▸ » : l'utilisateur
    voit d'un coup d'œil ce qui va agir et ce qui va seulement pré-remplir.
    """
    if not suggestions:
        return "Aucune suggestion pour cette sélection."
    lines = []
    for index, item in enumerate(suggestions, start=1):
        marker = "▸" if item.runs_immediately else "·"
        lines.append(f"{index}. {marker} {item.label}")
    return "\n".join(lines)
