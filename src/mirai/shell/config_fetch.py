"""Une tentative de récupération de /config auprès d'une base DM.

Hors de MainJob pour tenir le budget de la coquille : la fonction reçoit le job,
qui porte les en-têtes relais de chaque base, le contexte TLS et le transport HTTP.
"""

import json
import logging
import urllib.error
import urllib.request


def attempt(job, base_url, config_path, modes, timeout, headers):
    """Essaie `base_url` dans chaque mode de `modes` ([(nom, via_proxy)]) avec les
    en-têtes relais de cette base. Rend ((config_data, relay_present), None) à la
    première réponse JSON exploitable, sinon (None, dernière erreur)."""
    url = base_url.rstrip("/") + "/" + config_path.lstrip("/")
    sent = dict(headers)
    sent.update(job._relay_headers(base_url))
    relay_present = "X-Relay-Client" in sent
    error = "unknown"
    for mode, use_proxy in modes:
        try:
            logging.info(f"DM config fetch attempt: mode={mode} url={url}")
            logging.info(f"DM config fetch headers: relay={'yes' if relay_present else 'no'} "
                         f"keys={list(sent.keys())}")
            request = urllib.request.Request(url, headers=sent)
            with job._urlopen(request, context=job.get_ssl_context(base_url), timeout=timeout,
                              use_proxy=use_proxy) as response:
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
