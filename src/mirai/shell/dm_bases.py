"""Règles de la coquille sur les bases DM, en fonctions pures.

La liste embarquée par l'OXT, le DM actif et l'allowlist des hôtes sans
vérification TLS se calculent ici, à partir de valeurs reçues explicitement,
pour être testés sans `MainJob`.
"""

import urllib.parse


def baked_list(urls, legacy):
    """La liste des bases DM de l'OXT : `bootstrap_urls` nettoyée, sinon `bootstrap_url`."""
    result = []
    if isinstance(urls, (list, tuple)):
        for item in urls:
            value = str(item or "").strip()
            if value and value not in result:
                result.append(value)
    if not result:
        value = str(legacy or "").strip()
        if value:
            result.append(value)
    return result


def active_base(resolved, persisted, urls):
    """Le DM de référence : gagnant de la session, sinon gagnant persisté encore listé,
    sinon la première base, sinon une chaîne vide."""
    resolved = str(resolved or "").strip()
    if resolved:
        return resolved
    persisted = str(persisted or "").strip()
    if persisted and persisted.rstrip("/") in {url.rstrip("/") for url in urls}:
        return persisted
    return urls[0] if urls else ""


def is_insecure_url(url, patterns):
    """Vrai quand l'hôte de `url` figure dans `patterns` (`bootstrap_insecure_urls`).

    L'appariement se fait par hôte : il vaut pour une base nue comme pour une URL
    complète (base + chemin de config), et tolère une entrée écrite sans schéma.
    """
    if not isinstance(patterns, (list, tuple)):
        return False
    try:
        target_host = (urllib.parse.urlsplit(str(url or "").strip()).hostname or "").lower()
    except Exception:
        target_host = ""
    if not target_host:
        return False
    for item in patterns:
        value = str(item or "").strip()
        if not value:
            continue
        try:
            host = urllib.parse.urlsplit(value).hostname or ""
        except Exception:
            host = ""
        if not host:
            host = value.split("/")[0]
        if host and host.lower() == target_host:
            return True
    return False
