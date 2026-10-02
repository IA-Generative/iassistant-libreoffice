"""Fichiers locaux de l'extension : écritures atomiques, aucune valeur secrète.

Module sans dépendance UNO, partagé par la coquille (entrypoint.py) et l'add-in
=PROMPT().
"""

import json
import os
import tempfile

# Clés de la réponse /config qui portent un secret. La valeur est vidée avant
# toute écriture sur disque ; la clé reste, car sa présence est un signal
# (« llmToken » présent = proxy LLM du DM actif, cf. MainJob._llm_proxy_mode).
SECRET_DM_KEYS = frozenset({
    "llmToken", "llm_api_tokens", "embdToken", "telemetryKey", "tokenOWUI",
    "keycloak_client_secret", "keycloakClientSecret", "client_secret", "clientSecret",
    "proxy_password",
})


def redact_dm_config(config_data):
    """Copie de `config_data` où toute valeur de SECRET_DM_KEYS, à toute
    profondeur, est remplacée par une chaîne vide."""
    if isinstance(config_data, dict):
        return {key: ("" if key in SECRET_DM_KEYS else redact_dm_config(value))
                for key, value in config_data.items()}
    if isinstance(config_data, list):
        return [redact_dm_config(item) for item in config_data]
    return config_data


def write_json_atomic(path, data):
    """Écrit `data` en JSON sans jamais exposer de fichier tronqué : fichier
    temporaire du même dossier (0600 via mkstemp), fsync, puis os.replace,
    atomique sous POSIX comme sous Windows."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def read_json(path):
    """Objet JSON de `path`, ou {} (absent, illisible, autre type)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
