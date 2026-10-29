"""Lecture d'un flux SSE, dans le thread appelant.

INVARIANT : `run_stream()` s'exécute dans le **thread worker** du run, jamais
sur le thread principal. Il ne touche donc à aucun objet UNO et ne pompe aucun
événement — c'est `MainThreadDispatcher` qui reporte les mises à jour d'affichage
sur le thread principal, lequel reste libre pour LibreOffice pendant toute la
génération.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error


@dataclasses.dataclass
class RawChunk:
    chunk: dict          # chunk SSE complet parsé (porte delta.tool_calls)


@dataclasses.dataclass
class StreamHttpError:
    status: int
    body: str
    headers: object


@dataclasses.dataclass
class StreamNetworkError:
    reason: str


@dataclasses.dataclass
class StreamOutcome:
    ok: bool
    error: object = None      # StreamHttpError | StreamNetworkError | None
    cancelled: bool = False


def iter_sse_chunks(response):
    """Génère les objets JSON d'un flux SSE. Générateur pur, testable sans réseau.

    Les lignes vides, les lignes hors `data:` et les charges utiles illisibles
    sont ignorées ; `[DONE]` termine le flux, et le générateur rend alors True.
    """
    for line in response:
        if not line.strip() or not line.startswith(b"data: "):
            continue
        payload = line[len(b"data: "):].decode("utf-8").strip()
        if payload == "[DONE]":
            return True
        try:
            yield json.loads(payload)
        except Exception:
            continue
    return False


def _finishes(chunk):
    """Le chunk clôt la réponse du modèle (`finish_reason`)."""
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    return isinstance(choice, dict) and bool(choice.get("finish_reason"))


def _read_events(shell, response, on_event, cancelled):
    """Dispatche les chunks du flux. Rend None si l'arrêt est demandé, sinon
    True quand le flux est allé jusqu'à sa fin : `[DONE]` ou `finish_reason`."""
    chunks = iter_sse_chunks(response)
    finished = False
    while True:
        try:
            chunk = next(chunks)
        except StopIteration as end:
            return finished or bool(end.value)
        if cancelled():
            return None
        finished = finished or _finishes(chunk)
        try:
            on_event(RawChunk(chunk))
        except Exception as exc:
            shell.log(f"[sse] on_event error: {exc}")


def run_stream(shell, request, on_event, cancel_event=None):
    """Lit le flux et dispatche les événements — dans le thread appelant.

    `request` peut être une requête urllib déjà construite ou un CALLABLE qui la
    construit. Le callable est le mode à privilégier : bâtir la requête déclenche
    côté coquille des lectures de configuration, une résolution de modèle et,
    après un 401, une reprise d'authentification — autant d'appels réseau qui
    doivent rester dans ce thread.

    `on_event(event)` reçoit des RawChunk dans l'ordre. `cancel_event`
    (threading.Event) est consulté entre chaque chunk : dès qu'il est armé, la
    lecture s'arrête proprement.

    Les erreurs ne sont jamais levées : elles sont journalisées et rapportées
    dans l'outcome. Une connexion fermée en plein flux n'en est pas une pour
    urllib, qui arrête la lecture comme si la réponse était complète : un flux
    terminé sans `[DONE]` ni `finish_reason` est donc rapporté comme coupé.
    """
    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    if _cancelled():
        return StreamOutcome(ok=False, cancelled=True)

    try:
        # Construction paresseuse : tout ce qui peut bloquer reste dans ce thread.
        actual_request = request() if callable(request) else request
        with shell.urlopen(actual_request, timeout=shell.request_timeout()) as response:
            complete = _read_events(shell, response, on_event, _cancelled)
        if complete is None:
            return StreamOutcome(ok=False, cancelled=True)
        if not complete:
            raise ConnectionError("flux interrompu avant sa fin")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        error = StreamHttpError(exc.code, body, exc.headers)
        shell.report_llm_error(error.status, error.body, error.headers)
        shell.log(f"[sse] HTTP {error.status} body_len={len(error.body)}")
        # Un arrêt demandé pendant l'attente prime sur l'erreur arrivée ensuite.
        if _cancelled():
            return StreamOutcome(ok=False, cancelled=True)
        return StreamOutcome(ok=False, error=error)
    except Exception as exc:
        if _cancelled():
            return StreamOutcome(ok=False, cancelled=True)
        error = StreamNetworkError(str(exc))
        shell.report_llm_network_error(error.reason)
        shell.log(f"[sse] network error: {error.reason}")
        return StreamOutcome(ok=False, error=error)

    if _cancelled():
        return StreamOutcome(ok=False, cancelled=True)
    return StreamOutcome(ok=True)
