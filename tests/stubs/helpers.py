"""Faux objets partagés par les tests de la coquille."""

import json
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
