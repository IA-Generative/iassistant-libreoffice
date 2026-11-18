"""Faux objets partagés par les tests de la coquille et de l'add-in =PROMPT()."""

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


def enriched_v2(features=None, update=None):
    """Build a minimal EnrichedConfigResponse (schema_version=2)."""
    return {
        "meta": {"schema_version": 2, "generated_at": "2026-03-15T00:00:00Z"},
        "config": {},
        "features": features or {},
        "update": update,
    }


def make_update_directive(action="update", target="2.0.0", current="1.0.0",
                          checksum=None, artifact_url="/binaries/lo/2.0.0/mirai.oxt",
                          urgency="normal", campaign_id=1):
    d = {
        "action": action,
        "target_version": target,
        "current_version": current,
        "artifact_url": artifact_url,
        "checksum": checksum or "",
        "urgency": urgency,
        "campaign_id": campaign_id,
    }
    return d


def base_config() -> dict:
    return {
        "llm_base_urls": "http://localhost:11434",
        "llm_api_tokens": "test-token",
        "authHeaderName": "Authorization",
        "authHeaderPrefix": "Bearer ",
        "llm_default_models": "llama3",
        "llm_request_timeout_seconds": 30,
        "proxy_allow_insecure_ssl": False,
    }
