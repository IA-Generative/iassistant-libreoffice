"""Règles de la coquille sur les bases DM, en fonctions pures.

La liste embarquée par l'OXT, la liste annoncée par le DM, l'ordre des
candidats, les bases à essayer dans la même récupération, le DM actif et
l'allowlist des hôtes sans vérification TLS se calculent ici, à partir de
valeurs reçues explicitement, pour être testés sans `MainJob`.
"""

import urllib.parse

from .. import local_config


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


def same_base(a, b):
    """Vrai quand les deux URL désignent le même DM (schéma et hôte en minuscules,
    port conservé, barre finale ignorée)."""
    return local_config._dm_base_url(a) == local_config._dm_base_url(b)


def issued_by_another(issuer, target):
    """Vrai quand la paire relais a un émetteur enregistré et que ce n'est pas
    `target` ; une paire antérieure à l'émetteur vaut pour tout DM."""
    issuer, target = str(issuer or "").strip(), str(target or "").strip()
    return bool(issuer and target) and not same_base(issuer, target)


def _dedupe(urls):
    result = []
    for url in urls:
        value = str(url or "").strip()
        if value and not any(same_base(value, kept) for kept in result):
            result.append(value)
    return result


def announced_list(config_data):
    """Les bases que le DM annonce (`bootstrapUrls` des réglages), nettoyées et
    dédoublonnées ; None quand la réponse n'en porte aucune exploitable."""
    settings = local_config.select_settings(config_data)
    urls = settings.get("bootstrapUrls") if isinstance(settings, dict) else None
    if not isinstance(urls, (list, tuple)):
        return None
    result = _dedupe(item.strip().rstrip("/") for item in urls if isinstance(item, str))
    return result or None


def candidate_order(announced, baked, last_winner, unreachable=()):
    """Les bases à essayer, dans l'ordre : annoncées, puis celles de l'OXT, puis le
    dernier gagnant en repli ; celles que le processus a vues échouer en queue."""
    candidates = _dedupe([*(announced or []), *baked, last_winner])
    demoted = [url for url in candidates if local_config._dm_base_url(url) in unreachable]
    return [url for url in candidates if url not in demoted] + demoted


def ahead_of(announced, winner, unreachable=()):
    """Les bases que la liste annoncée nomme avant `winner` (toutes si elle ne le
    nomme pas), sans celles que le processus a vues échouer."""
    result = []
    for url in announced or []:
        if same_base(url, winner):
            break
        if local_config._dm_base_url(url) not in unreachable:
            result.append(url)
    return result


def mark_reachability(unreachable, base, answered):
    """Met à jour l'ensemble des bases injoignables du processus ; vrai quand il change."""
    key = local_config._dm_base_url(base)
    before = key in unreachable
    if answered:
        unreachable.discard(key)
    else:
        unreachable.add(key)
    return before == answered


def is_switch(previous, answering):
    """Vrai quand une base répond alors que le gagnant persisté en désigne une autre."""
    previous = str(previous or "").strip()
    return bool(previous) and not same_base(previous, answering)


def active_base(resolved, persisted, candidates):
    """Le DM de référence : gagnant de la session, sinon gagnant persisté, sinon le
    premier candidat, sinon une chaîne vide."""
    return (str(resolved or "").strip() or str(persisted or "").strip()
            or (candidates[0] if candidates else ""))


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
