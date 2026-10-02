"""Lecture d'un flux SSE, dans le thread appelant.

INVARIANT : `run_stream()` s'exécute dans le **thread worker** du run, jamais
sur le thread principal. Il ne touche donc à aucun objet UNO et ne pompe aucun
événement — c'est `MainThreadDispatcher` qui reporte les mises à jour d'affichage
sur le thread principal, lequel reste libre pour LibreOffice pendant toute la
génération.

C'est le point de bascule par rapport à l'implémentation historique : le drain
`processEventsToIdle` a disparu, donc la classe de gel (et l'abort
`std::terminate` dans `DispatchUserEvents`) n'est plus possible ici — non pas
évitée par vigilance, mais impossible par construction.
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
    sont ignorées ; `[DONE]` termine le flux.
    """
    for line in response:
        if not line.strip() or not line.startswith(b"data: "):
            continue
        payload = line[len(b"data: "):].decode("utf-8").strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except Exception:
            continue


def run_stream(shell, request, on_event, tick=None, cancel_event=None):
    """Lit le flux et dispatche les événements — dans le thread appelant.

    `request` peut être une requête urllib déjà construite ou un CALLABLE qui la
    construit. Le callable est le mode à privilégier : bâtir la requête déclenche
    côté coquille des lectures de configuration, une résolution de modèle et,
    après un 401, une reprise d'authentification — autant d'appels réseau qui
    doivent rester dans ce thread.

    `on_event(event)` reçoit des RawChunk dans l'ordre. `tick()` est appelé entre
    les chunks (animation). `cancel_event` (threading.Event) est consulté entre
    chaque chunk : dès qu'il est armé, la lecture s'arrête proprement.

    Les erreurs ne sont jamais levées : elles sont journalisées et rapportées
    dans l'outcome.
    """
    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    if _cancelled():
        return StreamOutcome(ok=False, cancelled=True)

    try:
        # Construction paresseuse : tout ce qui peut bloquer reste dans ce thread.
        actual_request = request() if callable(request) else request
        with shell.urlopen(actual_request, timeout=shell.request_timeout()) as response:
            for chunk in iter_sse_chunks(response):
                if _cancelled():
                    return StreamOutcome(ok=False, cancelled=True)
                try:
                    on_event(RawChunk(chunk))
                except Exception as exc:
                    shell.log(f"[sse] on_event error: {exc}")
                if tick is not None:
                    try:
                        tick()
                    except Exception:
                        pass
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        error = StreamHttpError(exc.code, body, exc.headers)
        shell.report_llm_error(error.status, error.body, error.headers)
        shell.log(f"[sse] HTTP {error.status} body_len={len(error.body)}")
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
