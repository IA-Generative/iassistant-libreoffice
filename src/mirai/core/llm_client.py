"""Client LLM double-mode : tool calling OpenAI natif ou repli JSON parsé.

Une seule représentation interne (ToolCall) quel que soit le fil. Mode résolu
depuis la config `llm_tool_mode` ("auto" par défaut, distribuable par DM) ;
en auto, une erreur HTTP 400/404/422 sur une requête portant des tools bascule
définitivement en "json" (caché dans `llm_tool_mode_detected`).

Règle de streaming en mode JSON : les deltas sont retenus tant que la réponse
peut être un appel d'outil (commence par '{', '```' ou '<think>') ; si le
parse échoue en fin de stream, le texte est restitué intégralement — on ne
perd JAMAIS la sortie du modèle.
"""

import dataclasses
import json
import re

from . import sse_pump, telemetry_steps
from .progress import NullProgress
from .text_filters import strip_think_blocks
from .tool_calls import ToolCall

DEFAULT_STEP_MAX_TOKENS = 4000

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?|```")


@dataclasses.dataclass
class StepResult:
    text: str = ""
    tool_calls: list = dataclasses.field(default_factory=list)
    finish_reason: str = ""
    streamed: bool = False    # True si le texte a déjà été poussé au sink
    error: str = ""           # "http_429", "network_error"… — étape interrompue
    raw_json: str = ""        # réponse JSON brute (mode json, ré-encodage fidèle)
    reasoning_chars: int = 0  # raisonnement reçu — sert à diagnostiquer un
                              # budget épuisé avant la réponse (cf. step())

    @property
    def starved_by_reasoning(self) -> bool:
        """Le modèle a dépensé tout son budget à réfléchir, sans rien répondre.

        Signature exacte : le flux s'arrête sur `length` (plafond atteint), du
        raisonnement est arrivé, mais ni texte ni tool call. Les modèles à
        raisonnement puisent la réflexion ET la réponse dans le MÊME
        `max_tokens`, et la réponse vient en dernier : un raisonnement un peu
        plus bavard que d'habitude la fait disparaître entièrement.
        """
        return (self.finish_reason == "length"
                and self.reasoning_chars > 0
                and not (self.text or "").strip()
                and not self.tool_calls)


def repair_json(text):
    """Réparations minimales pour le JSON de petits modèles."""
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _first_balanced_object(text):
    """Extrait le premier objet JSON {...} équilibré, en respectant les chaînes."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return ""


def parse_json_tool_calls(text):
    """Parse tolérant de la sortie JSON du protocole de repli.

    Accepte {"tool_calls":[{name, arguments}…]} ou un appel nu
    {"name": …, "arguments": …}. Retourne [] si ce n'est pas un appel d'outil.
    """
    cleaned = strip_think_blocks(text or "")
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    candidate = _first_balanced_object(cleaned)
    if not candidate:
        return []
    try:
        data = json.loads(repair_json(candidate))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    entries = None
    if isinstance(data.get("tool_calls"), list):
        entries = data["tool_calls"]
    elif "name" in data and "arguments" in data:
        entries = [data]
    if not entries:
        return []

    calls = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        arguments = entry.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(repair_json(arguments))
            except Exception:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(id=f"call_{i}", name=name, arguments=arguments,
                              raw=candidate))
    return calls


class LLMClient:
    def __init__(self, shell, max_tokens=None):
        self.shell = shell
        try:
            self.max_tokens = int(max_tokens or shell.get_config(
                "assistant_max_tokens", DEFAULT_STEP_MAX_TOKENS))
        except Exception:
            self.max_tokens = DEFAULT_STEP_MAX_TOKENS
        self.configured_mode = str(
            shell.get_config("llm_tool_mode", "auto") or "auto").strip().lower()
        if self.configured_mode not in ("auto", "native", "json"):
            self.configured_mode = "auto"

    def effective_mode(self):
        if self.configured_mode != "auto":
            return self.configured_mode
        detected = str(self.shell.get_config("llm_tool_mode_detected", "") or "")
        return "json" if detected == "json" else "native"

    def step(self, messages, tools=None, on_text_delta=None, cancel_event=None,
             progress=None):
        """Un aller LLM. Avec `tools`, peut retourner des tool_calls ;
        sans tools, streaming texte intégral (aucune rétention).

        `cancel_event` interrompt la lecture du flux entre deux chunks : une
        annulation ne doit ni attendre la fin de la génération ni relancer une
        reprise d'authentification.
        """
        mode = self.effective_mode() if tools else "text"
        result = self._run_step(messages, tools, on_text_delta, mode,
                                cancel_event=cancel_event, progress=progress)

        # Auto-détection : le relais rejette la requête portant des tools →
        # bascule définitive en mode JSON et re-tentative immédiate.
        if (result.error in ("http_400", "http_404", "http_422")
                and mode == "native" and self.configured_mode == "auto"):
            self.shell.log("[llm] tools natifs rejetés — bascule en mode json")
            try:
                self.shell.set_config("llm_tool_mode_detected", "json")
            except Exception:
                pass
            # Bascule DÉFINITIVE pour ce poste : elle mérite une trace. Sans
            # elle, un parc dont la moitié parle JSON est indistinguable d'un
            # parc en tool calling natif. Auto-limitée par `set_config`.
            telemetry_steps.emit(self.shell, telemetry_steps.TOOLS_FALLBACK_JSON,
                                 {"llm.status": result.error})
            mode = "json"
            result = self._run_step(messages, tools, on_text_delta, mode,
                                    cancel_event=cancel_event, progress=progress)

        # 401 : jeton d'accès absent, expiré ou révoqué. Une seule reprise, avec
        # la récupération exécutée DANS le thread réseau (recover_auth passe par
        # _build_request) — elle fait du réseau bloquant, et sur le thread
        # principal LibreOffice paraîtrait gelé (cf. sse_pump).
        cancelled = cancel_event is not None and cancel_event.is_set()
        if result.error == "http_401" and not cancelled:
            self.shell.log("[llm] 401 — tentative de récupération du jeton d'accès")
            result = self._run_step(messages, tools, on_text_delta, mode,
                                    recover_auth=True, cancel_event=cancel_event,
                                    progress=progress)
            # Seule la reprise RÉUSSIE est signalée : un second 401 est déjà
            # porté par LlmRelayError côté coquille, et l'annoncer « récupéré »
            # serait un mensonge de tableau de bord.
            if result.error != "http_401":
                telemetry_steps.emit(self.shell, telemetry_steps.AUTH_RECOVERED)

        # Budget épuisé par le raisonnement : une seule reprise, plus large.
        # Réflexion et réponse partagent `max_tokens` et la réponse vient en dernier.
        # On n'envoie pas `reasoning_effort` : plusieurs modèles du relais le
        # refusent (HTTP 400) ; élargir le plafond est accepté partout.
        if result.starved_by_reasoning and not cancelled:
            widened = max(self.max_tokens * 3, 12000)
            self.shell.log(
                f"[llm] budget épuisé par le raisonnement "
                f"({result.reasoning_chars} car., 0 de réponse) — "
                f"reprise à max_tokens={widened}")
            retried = self._run_step(messages, tools, on_text_delta, mode,
                                     cancel_event=cancel_event,
                                     progress=progress, max_tokens=widened)
            # Ne jamais dégrader : on ne garde la reprise que si elle apporte
            # ce qui manquait — du texte ou un tool call.
            if (retried.text or "").strip() or retried.tool_calls:
                # L'ÉCHEC de ce mécanisme est déjà visible (la palette émet
                # `llm.reasoning_starved`). Sans son pendant réussi, impossible
                # de dire si la reprise sauve des runs ou coûte un aller-retour
                # pour rien.
                telemetry_steps.emit(
                    self.shell, telemetry_steps.REASONING_RETRY_OK,
                    {"reasoning.chars": int(result.reasoning_chars),
                     "retry.max_tokens": int(widened)})
                return retried
        return result

    def _run_step(self, messages, tools, on_text_delta, mode, recover_auth=False,
                  cancel_event=None, progress=None, max_tokens=None):
        progress = progress or NullProgress()
        extra_body = None
        if tools and mode == "native":
            extra_body = {"tools": tools, "tool_choice": "auto"}

        # Fabrique différée : build_chat_request lit la config et peut résoudre
        # le modèle via le réseau. Exécutée dans le thread du pump, jamais sur
        # le thread principal — sinon LibreOffice paraît gelé (cf. sse_pump).
        def _build_request():
            if recover_auth:
                recover = getattr(self.shell, "recover_llm_auth", None)
                if callable(recover):
                    recover()
            return self.shell.build_chat_request(
                messages, max_tokens=max_tokens or self.max_tokens,
                extra_body=extra_body)

        text_parts = []
        withhold = (mode == "json")   # rétention tant que ça ressemble à un tool call
        decided = [False]
        live = [False]
        fragments = {}                # index → {id, name, arguments}
        finish = [""]
        reasoning_chars = [0]
        chunks = [0]                  # chunks SSE effectivement traités

        def _handle_text(content):
            text_parts.append(content)
            if on_text_delta is None:
                return
            if not withhold:
                live[0] = True
                on_text_delta(content)
                return
            if live[0]:
                on_text_delta(content)
                return
            if not decided[0]:
                accumulated = "".join(text_parts).lstrip()
                if not accumulated:
                    return
                if accumulated[0] in "{`<":
                    decided[0] = True      # candidat tool call / think : on retient
                else:
                    decided[0] = True
                    live[0] = True
                    on_text_delta("".join(text_parts))

        def _on_event(event):
            chunks[0] += 1
            chunk = event.chunk
            # Le relais peut envoyer un bloc `usage` en fin de flux — on le LIT
            # s'il vient, sans jamais le réclamer : ajouter `stream_options` au
            # corps ferait rejeter la requête par certains relais.
            usage = chunk.get("usage")
            if isinstance(usage, dict) and usage.get("completion_tokens"):
                progress.exact_tokens(int(usage["completion_tokens"]))
            choices = chunk.get("choices") or []
            if not choices:
                return
            choice = choices[0]
            delta = choice.get("delta") or {}
            for fragment in delta.get("tool_calls") or []:
                index = int(fragment.get("index", 0))
                slot = fragments.setdefault(
                    index, {"id": "", "name": "", "arguments": ""})
                if fragment.get("id"):
                    slot["id"] = fragment["id"]
                function = fragment.get("function") or {}
                if function.get("name"):
                    slot["name"] += function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]
            content = delta.get("content")
            if content:
                progress.on_text(content)
                _handle_text(content)
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                # Le modèle « réfléchit » : rien à afficher dans le document,
                # mais l'utilisateur doit voir que ça travaille.
                reasoning_chars[0] += len(reasoning)
                progress.on_reasoning(reasoning)
            if choice.get("finish_reason"):
                finish[0] = choice["finish_reason"]

        outcome = sse_pump.run_stream(self.shell, _build_request, _on_event,
                                      cancel_event=cancel_event)

        # Bilan du flux : distingue un step muet (0 chunk) d'un step qui rend une réponse vide.
        try:
            self.shell.log(
                f"[llm] step mode={mode} chunks={chunks[0]} "
                f"finish={finish[0] or '-'} texte={len(''.join(text_parts))}c "
                f"tool_calls={len(fragments)} "
                f"args={sum(len(f['arguments']) for f in fragments.values())}c "
                f"raisonnement={reasoning_chars[0]}c ok={outcome.ok}")
        except Exception:
            pass

        if not outcome.ok:
            if isinstance(outcome.error, sse_pump.StreamHttpError):
                return StepResult(error=f"http_{outcome.error.status}",
                                  finish_reason=finish[0],
                                  reasoning_chars=reasoning_chars[0])
            return StepResult(error="network_error", finish_reason=finish[0],
                              reasoning_chars=reasoning_chars[0])

        full_text = "".join(text_parts)

        if fragments:  # mode natif : tool calls assemblés
            calls = []
            for index in sorted(fragments):
                slot = fragments[index]
                try:
                    arguments = json.loads(repair_json(slot["arguments"] or "{}"))
                except Exception:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                calls.append(ToolCall(
                    id=slot["id"] or f"call_{index}", name=slot["name"],
                    arguments=arguments, raw=slot["arguments"]))
            return StepResult(text=full_text, tool_calls=calls,
                              finish_reason=finish[0], streamed=live[0],
                              reasoning_chars=reasoning_chars[0])

        if mode == "json" and tools:
            calls = parse_json_tool_calls(full_text)
            if calls:
                return StepResult(tool_calls=calls, finish_reason=finish[0],
                                  raw_json=calls[0].raw,
                                  reasoning_chars=reasoning_chars[0])
            # Pas un tool call : texte final — jamais perdu même s'il était retenu
            clean = strip_think_blocks(full_text)
            return StepResult(text=clean, finish_reason=finish[0],
                              streamed=live[0],
                              reasoning_chars=reasoning_chars[0])

        return StepResult(text=strip_think_blocks(full_text),
                          finish_reason=finish[0], streamed=live[0],
                          reasoning_chars=reasoning_chars[0])

    def encode_tool_exchange(self, step, results):
        """Ré-encode l'échange (appel + résultats) dans le format du fil actif."""
        if step.raw_json:  # mode json
            payload = [
                {"name": (step.tool_calls[i].name if i < len(step.tool_calls) else ""),
                 "ok": r.ok,
                 "result": r.content if r.ok else r.error}
                for i, r in enumerate(results)
            ]
            return [
                {"role": "assistant", "content": step.raw_json},
                {"role": "user", "content":
                    "RÉSULTATS DES OUTILS :\n"
                    + json.dumps(payload, ensure_ascii=False)
                    + "\nPoursuis la tâche. Si elle est terminée, réponds "
                      "normalement (sans JSON)."},
            ]
        # mode natif
        assistant_message = {
            "role": "assistant",
            "content": step.text or None,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name,
                              "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in step.tool_calls
            ],
        }
        tool_messages = [
            {"role": "tool", "tool_call_id": r.call_id,
             "content": r.content if r.ok else f"ERREUR : {r.error}"}
            for r in results
        ]
        return [assistant_message] + tool_messages
