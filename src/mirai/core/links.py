"""Liens distribués par le Device Management (`config.links`).

Le serveur sert une liste plate, déjà résolue, que le client remplace en bloc
à chaque /config : aucune fusion ici, aucun repli codé en dur pour un lien que
le DM a retiré. Chaque entrée nomme le RÔLE du lien dans l'interface (`id`),
pas sa destination. Une entrée hors contrat est ignorée seule, jamais la liste.
"""

import re

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
MAX_LABEL = 80
MAX_HINT = 200
MAX_URL = 2048


def _https(value):
    return (isinstance(value, str) and value.startswith("https://")
            and len(value) <= MAX_URL and "${" not in value
            and not any(c.isspace() for c in value))


def _normalize_one(raw):
    if not isinstance(raw, dict):
        return None
    link_id = raw.get("id")
    label = raw.get("label")
    if not isinstance(link_id, str) or not ID_RE.match(link_id):
        return None
    if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
        return None
    if not _https(raw.get("url")):
        return None
    link = {"id": link_id, "label": label.strip(), "url": raw["url"]}
    hint = raw.get("hint")
    if isinstance(hint, str) and hint.strip() and len(hint) <= MAX_HINT:
        link["hint"] = hint.strip()
    if _https(raw.get("hintUrl")):
        link["hintUrl"] = raw["hintUrl"]
    return link


def normalize_links(raw):
    """Liste des liens valides, dédoublonnés par id, dans l'ordre reçu."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        link = _normalize_one(item)
        if link is None or link["id"] in seen:
            continue
        seen.add(link["id"])
        out.append(link)
    return out


def find_link(raw, link_id):
    """Le lien de ce rôle, ou None : l'appelant masque l'élément."""
    for link in normalize_links(raw):
        if link["id"] == link_id:
            return link
    return None
