"""Options de génération : ce qu'Éloignement exige pour un modèle, et le corps qui les porte.

Éloignement décrit chaque option dans `options_requises` du modèle : son code,
l'endroit où la poser dans `options` (`champ`, chemin pointé) et, s'il les
donne, un libellé, un type et les valeurs permises. Rien de cela n'est connu du
plugin à l'avance : sans libellé, on montre le code ; sans type, un champ libre
dont une saisie purement numérique part en nombre (les délais et durées
d'Éloignement sont des entiers).
"""

import re
from dataclasses import dataclass, field

_CHAMP = re.compile(r"^[a-z][a-z0-9_]{0,63}(\.[a-z][a-z0-9_]{0,63}){0,2}$")
TYPES = ("texte", "entier", "booleen", "choix")
AUTO = "libre"     # type non dit par Éloignement
YES_NO = [{"valeur": True, "libelle": "Oui"}, {"valeur": False, "libelle": "Non"}]


@dataclass
class Option:
    option: str
    champ: str
    libelle: str
    type: str = AUTO
    valeurs: list = field(default_factory=list)     # [{valeur, libelle}]
    defaut: object = None

    def choices(self):
        """Valeurs proposées dans une liste ; vide pour une saisie libre."""
        if self.type == "booleen":
            return YES_NO
        return self.valeurs


def _scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _valeurs(raw):
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and _scalar(item.get("valeur")):
            out.append({"valeur": item.get("valeur"),
                        "libelle": str(item.get("libelle") or item.get("valeur"))})
        elif _scalar(item) and item is not None:
            out.append({"valeur": item, "libelle": str(item)})
    return out


def required(modele):
    """Options exigées par le modèle, telles qu'Éloignement les décrit.

    Une entrée sans code ou au chemin malformé est écartée : elle ne pourrait
    pas être posée dans la demande."""
    found = []
    for raw in (modele or {}).get("options_requises") or []:
        if not isinstance(raw, dict):
            continue
        code, champ = str(raw.get("option") or ""), str(raw.get("champ") or "")
        if not code or not _CHAMP.match(champ):
            continue
        valeurs = _valeurs(raw.get("valeurs"))
        kind = str(raw.get("type") or "")
        if kind not in TYPES:
            kind = "choix" if valeurs else AUTO
        defaut = raw.get("defaut") if _scalar(raw.get("defaut")) else None
        found.append(Option(code, champ, str(raw.get("libelle") or code), kind, valeurs, defaut))
    return found


def _convert(option, raw):
    if option.choices():
        return raw                     # valeur prise telle quelle dans la liste d'Éloignement
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise ValueError(f"« {option.libelle} » est à renseigner.")
    if option.type == "entier":
        if not re.fullmatch(r"-?\d{1,6}", text):
            raise ValueError(f"« {option.libelle} » attend un nombre entier.")
        return int(text)
    if option.type == AUTO and re.fullmatch(r"-?\d{1,6}", text):
        return int(text)
    return text


def build(options, answers):
    """Corps `options` de la génération : chaque réponse posée à son `champ`.

    `answers` : {code: valeur} ; une valeur choisie dans une liste est passée
    telle qu'Éloignement l'a donnée, une saisie libre est convertie selon le
    type. Lève ValueError, en français, sur une réponse manquante ou invalide."""
    body = {}
    for option in options:
        if option.option not in answers:
            raise ValueError(f"« {option.libelle} » est à renseigner.")
        value = _convert(option, answers[option.option])
        *parents, leaf = option.champ.split(".")
        node = body
        for part in parents:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Options d'Éloignement incohérentes autour de « {part} ».")
            node = child
        node[leaf] = value
    return body


def valid_body(options):
    """Vrai si `options` est un corps acceptable : objets imbriqués, feuilles scalaires."""
    if not isinstance(options, dict):
        return False
    for key, value in options.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            return False
        if isinstance(value, dict):
            if not valid_body(value):
                return False
        elif not _scalar(value):
            return False
    return True
