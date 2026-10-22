"""Faux objets partagés par les tests de la coquille."""

import json
import os
from unittest.mock import MagicMock


def http_response(body=b"", status=200):
    """Réponse urllib factice, utilisable en `with`. `read()` rend des octets :
    *body* tel quel, encodé en UTF-8 si c'est du texte, sinon en JSON."""
    if not isinstance(body, (bytes, str)):
        body = json.dumps(body)
    if isinstance(body, str):
        body = body.encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.headers.items.return_value = []
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def refuse_settings_writes(monkeypatch, refusals=None):
    """Écriture de settings.json refusée comme sous Windows quand un autre
    programme tient le fichier : les `refusals` premières fois, ou toujours."""
    from src.mirai import local_config
    real = local_config.write_json_atomic
    calls = []

    def write(path, data):
        if os.path.basename(path) == local_config.SETTINGS_FILE:
            calls.append(path)
            if refusals is None or len(calls) <= refusals:
                raise PermissionError("[WinError 5] Accès refusé")
        return real(path, data)

    monkeypatch.setattr(local_config, "write_json_atomic", write)
