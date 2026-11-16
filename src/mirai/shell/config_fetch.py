"""Récupération de /config auprès des bases DM.

Hors de MainJob pour tenir le budget de la coquille : les fonctions reçoivent de
quoi interroger une base (en-têtes, ouverture de la requête), sans le job.
"""

import json
import logging
import urllib.error
import urllib.request

from . import dm_bases


def attempt(base_url, config_path, modes, headers, open_url):
    """Essaie `base_url` dans chaque mode de `modes` ([(nom, via_proxy)]) avec
    `headers`, en-têtes relais de cette base compris ; `open_url(request, via_proxy)`
    ouvre la requête. Rend ((config_data, relay_present), None) à la première
    réponse JSON exploitable, sinon (None, dernière erreur)."""
    url = base_url.rstrip("/") + "/" + config_path.lstrip("/")
    relay_present = "X-Relay-Client" in headers
    error = "unknown"
    for mode, use_proxy in modes:
        try:
            logging.info(f"DM config fetch attempt: mode={mode} url={url}")
            logging.info(f"DM config fetch headers: relay={'yes' if relay_present else 'no'} "
                         f"keys={list(headers.keys())}")
            request = urllib.request.Request(url, headers=headers)
            with open_url(request, use_proxy) as response:
                payload = response.read().decode("utf-8")
            logging.info(f"DM bootstrap response ({mode}): {len(payload)} octets")
            config_data = json.loads(payload)
            if isinstance(config_data, dict):
                return (config_data, relay_present), None
            error = f"Invalid JSON root type: {type(config_data).__name__}"
            logging.info(f"Failed to fetch device management config ({mode}): {error}")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            error = f"HTTP {e.code} {e.reason}"
            logging.info(f"Failed to fetch device management config ({mode}): "
                         f"HTTP {e.code} {e.reason} body_len={len(body)}")
        except urllib.error.URLError as e:
            error = f"URL error {e.reason}"
            logging.info(f"Failed to fetch device management config ({mode}): URL error {e.reason}")
        except Exception as e:
            error = str(e)
            logging.info(f"Failed to fetch device management config ({mode}): {str(e)}")
    return None, error


def first_answer(candidates, fetch_from, unreachable):
    """Interroge les candidats dans l'ordre avec `fetch_from(base)` (le rendu
    d'`attempt`) ; rend ((base, réponse) ou None, dernière erreur) et tient à jour
    `unreachable`, les bases que le processus a vues échouer.

    Les bases que la gagnante nomme avant elle sont essayées dans la même
    récupération, en un seul saut : la liste rapportée par le saut attend la
    récupération suivante.
    """
    winner, queue, hopped, last_error = None, list(candidates), False, "unknown"
    while queue:
        base_url = queue.pop(0)
        got, error = fetch_from(base_url)
        if error:
            last_error = error
        if dm_bases.mark_reachability(unreachable, base_url, got is not None):
            logging.info(f"DM base {base_url}: " + ("reachable again" if got
                         else "unreachable, tried last for the rest of the session"))
        if got is None:
            continue
        winner = (base_url, got)
        if hopped:
            break
        hopped = True
        queue = dm_bases.ahead_of(dm_bases.announced_list(got[0]), base_url, unreachable)
        if queue:
            logging.info(f"DM {base_url} names {queue} ahead of itself: trying them now")
    return winner, last_error
