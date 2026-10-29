"""Boucle agentique : prompt → LLM → tool calls → résultats → … → réponse finale.

Un run = un contexte undo (via ToolContext, ouvert par le premier tool mutant)
= un seul Ctrl+Z. Le RunObserver alimente le journal d'actions de la palette
(séquence proposée puis exécutée, façon agent). Aucun contenu de document ni
de prompt ne part en télémétrie — uniquement compteurs et statuts.
"""

import dataclasses
import time

from ..i18n import t as _t
from . import prompts
from .progress import NullProgress


class RunObserver:
    """Interface du journal d'actions — implémentée par la palette."""

    def on_run_start(self, mode):
        pass

    def on_tool_calls(self, calls):
        pass

    def on_tool_result(self, call, result, duration_ms):
        pass

    def on_final(self, text):
        pass

    def on_error(self, code, message):
        pass


@dataclasses.dataclass
class RunResult:
    ok: bool
    iterations: int = 0
    text: str = ""
    reason: str = ""
    changed: bool = False    # un outil mutant a abouti sur le document


ERROR_KEYS = {
    # Le renouvellement automatique du jeton (refresh /config, puis
    # ré-enrôlement) a déjà été tenté avant d'en arriver là : ce message ne
    # s'affiche que si le poste n'a pas pu se ré-authentifier tout seul.
    "http_401": "run.err_401",
    "http_403": "run.err_403",
    "http_429": "run.err_429",
    "network_error": "run.err_network",
    # Flux coupé par « Arrêter » : un arrêt voulu, pas une panne.
    "cancelled": "run.stopped",
    # Flux terminé sans texte ni appel d'outil (garde dans run_agentic).
    "empty_response": "run.err_empty",
    "truncated": "run.err_truncated",
    "max_iterations": "run.not_converged",
    # Plafond atteint après un outil mutant : le document a changé, et
    # relancer la demande le réécrirait.
    "max_iterations_after_changes": "run.applied_not_concluded",
}


def error_message(code):
    key = ERROR_KEYS.get(code)
    return _t(key) if key else _t("run.err_generic", code=code)


def is_outage(code):
    """Le réseau ou le service IA est en panne, par opposition à une réponse
    que l'assistant n'a pas pu exploiter."""
    return code == "network_error" or code.startswith("http_")


DEFAULT_MAX_ITERATIONS = 6


class Orchestrator:
    """Pilote un run complet. S'exécute dans le thread worker.

    `dispatcher` (facultatif) marshalle vers le thread principal tout ce qui
    touche UNO : exécution des tools et fermeture du contexte undo. Sans lui —
    tests, ou contexte hors LibreOffice — les appels se font sur place.
    `cancel_event` est consulté entre chaque étape et chaque tool.
    """

    def __init__(self, llm, registry, ctx, observer=None, conversation=None,
                 max_iterations=None, cancel_event=None, dispatcher=None,
                 progress=None):
        self.llm = llm
        self.registry = registry
        self.ctx = ctx
        self.observer = observer or RunObserver()
        self.conversation = conversation
        self.cancel_event = cancel_event
        self.dispatcher = dispatcher
        self.progress = progress or NullProgress()
        if max_iterations is None:
            try:
                max_iterations = int(ctx.shell.get_config(
                    "orchestrator_max_iterations", DEFAULT_MAX_ITERATIONS))
            except Exception:
                max_iterations = DEFAULT_MAX_ITERATIONS
        self.max_iterations = max_iterations

    def run_agentic(self, user_prompt, sink, preset_extra=""):
        # En mode configuré "auto", le prompt système garde le protocole JSON
        # (un flip natif→json en cours de run reste couvert).
        prompt_mode = (self.llm.configured_mode
                       if self.llm.configured_mode == "auto"
                       else self.llm.effective_mode())
        system_prompt = prompts.build_system(
            self.ctx.app, self.registry, prompt_mode, preset_extra)

        messages = [{"role": "system", "content": system_prompt}]
        if self.conversation is not None:
            messages.extend(self.conversation.context_messages(self.ctx.app))
        messages.append({"role": "user",
                         "content": self._with_scope(user_prompt)})
        tools = self.registry.openai_tools(self.ctx.app)
        mutating = {spec.name for spec in self.registry.list_tools(self.ctx.app)
                    if spec.mutates}

        self.observer.on_run_start(self.llm.effective_mode())
        acted = False    # un outil mutant a-t-il déjà agi sur le document ?
        changed = False  # l'un d'eux a-t-il abouti ?
        try:
            for iteration in range(self.max_iterations):
                if self.cancelled:
                    return RunResult(ok=False, iterations=iteration,
                                     reason="cancelled", text=_t("run.stopped"),
                                     changed=changed)
                step = self.llm.step(messages, tools=tools,
                                     on_text_delta=sink.stream_delta,
                                     cancel_event=self.cancel_event,
                                     progress=self.progress)
                if self.cancelled:
                    return RunResult(ok=False, iterations=iteration + 1,
                                     reason="cancelled", text=_t("run.stopped"),
                                     changed=changed)
                if step.error:
                    message = error_message(step.error)
                    self.observer.on_error(step.error, message)
                    result = RunResult(ok=False, iterations=iteration + 1,
                                       reason=step.error, text=message,
                                       changed=changed)
                    return result
                if step.tool_calls:
                    self.progress.set_phase(_t("progress.action"))
                    self.observer.on_tool_calls(step.tool_calls)
                    results = self._execute_tool_calls(step.tool_calls)
                    messages.extend(self.llm.encode_tool_exchange(step, results))
                    acted = acted or any(call.name in mutating
                                         for call in step.tool_calls)
                    # « Arrêter » peut couper la série : moins de résultats que d'appels.
                    changed = changed or any(
                        call.name in mutating and result.ok
                        for call, result in zip(step.tool_calls, results, strict=False))
                    continue

                final_text = step.text.strip()
                # Rien produit ET rien fait : c'est un échec, pas une réussite
                # muette. On ne déclenche PAS la garde si un outil a déjà agi —
                # le document a alors bien été modifié, et un texte de clôture
                # vide y est légitime.
                if not final_text and not acted:
                    message = error_message("empty_response")
                    self.observer.on_error("empty_response", message)
                    return RunResult(ok=False, iterations=iteration + 1,
                                     reason="empty_response", text=message)

                sink.finish(final_text, step.streamed)
                self.observer.on_final(final_text)
                if self.conversation is not None:
                    self.conversation.append("user", user_prompt, self.ctx.app)
                    self.conversation.append("assistant", final_text, self.ctx.app)
                result = RunResult(ok=True, iterations=iteration + 1,
                                   text=final_text, changed=changed)
                return result

            if self.cancelled:
                return RunResult(ok=False, iterations=self.max_iterations,
                                 reason="cancelled", text=_t("run.stopped"),
                                 changed=changed)
            reason = "max_iterations_after_changes" if acted else "max_iterations"
            message = error_message(reason)
            self.observer.on_error(reason, message)
            return RunResult(ok=False, iterations=self.max_iterations,
                             reason=reason, text=message, changed=changed)
        finally:
            # Le span AssistantRun est émis par le worker de la palette — point
            # unique couvrant les QUATRE chemins d'exécution. Le RunResult
            # retourné porte déjà tout ce que le span disait (ok, iterations,
            # reason) : un second émetteur ici ferait diverger deux formats.
            self._on_main(self.ctx.undo_end)

    def _with_scope(self, user_prompt):
        """Préfixe la demande par sa PORTÉE, lue sur le document.

        Une consigne générale du prompt système se dilue : le modèle lisait le
        document puis répondait du texte, sans jamais écrire. Annoncer la portée
        juste avant la demande — « aucune sélection, donc document entier » —
        supprime l'ambiguïté au moment où elle compte.
        """
        try:
            scope = self._scope_line()
        except Exception:
            return user_prompt
        return f"{scope}\n\n{user_prompt}" if scope else user_prompt

    def _scope_line(self):
        selected = ""
        try:
            selected = self.ctx.on_main(
                lambda: self.ctx.controller.getSelection()
                .getByIndex(0).getString()) or ""
        except Exception:
            selected = ""

        if selected.strip():
            return (f"PORTÉE : la sélection courante ({len(selected)} caractères). "
                    "N'agis que sur elle.")
        if self.ctx.app == "writer":
            return ("PORTÉE : aucune sélection — la demande porte sur le "
                    "DOCUMENT ENTIER. Commence par lire sa carte "
                    "(writer_get_document_map), puis APPLIQUE la modification "
                    "avec writer_replace_paragraphs de [P1] au dernier "
                    "paragraphe. Répondre le texte réécrit sans appeler l'outil "
                    "ne modifierait rien.")
        return ("PORTÉE : aucune plage sélectionnée — appuie-toi sur la vue "
                "d'ensemble de la feuille avant d'agir.")

    @property
    def cancelled(self):
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _on_main(self, fn, timeout=30):
        """Exécute fn sur le thread principal si un dispatcher est fourni.

        Les tools touchent le document : hors dispatcher, ils s'exécuteraient
        dans le worker, sans SolarMutex. C'est le seul point de passage.
        """
        if self.dispatcher is None:
            return fn()
        return self.dispatcher.call(fn, timeout=timeout)

    def _execute_tool_calls(self, calls):
        """Joue les outils demandés, en séquence, et rend leurs résultats."""
        results = []
        for call in calls:
            if self.cancelled:
                break
            call_started = time.monotonic()
            tool_result = self._on_main(
                lambda c=call: self.registry.call_tool(
                    c.name, c.arguments, self.ctx, call_id=c.id))
            duration_ms = int((time.monotonic() - call_started) * 1000)
            self.observer.on_tool_result(call, tool_result, duration_ms)
            results.append(tool_result)
        return results
