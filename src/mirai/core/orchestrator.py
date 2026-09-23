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


ERROR_KEYS = {
    # Le renouvellement automatique du jeton (refresh /config, puis
    # ré-enrôlement) a déjà été tenté avant d'en arriver là : ce message ne
    # s'affiche que si le poste n'a pas pu se ré-authentifier tout seul.
    "http_401": "run.err_401",
    "http_403": "run.err_403",
    "http_429": "run.err_429",
    "network_error": "run.err_network",
    # Le flux s'est terminé sans erreur mais n'a rien livré : ni texte, ni appel
    # d'outil, et aucun outil n'avait agi plus tôt dans le run. Constaté en
    # recette le 2026-08-04 — le relais renvoyait pourtant un tool call complet
    # (229 chunks), le plugin n'en a rien récupéré. Sans cette garde le run se
    # déclarait RÉUSSI avec un texte vide : écran muet côté utilisateur, et
    # `assistant.ok=true` côté télémétrie, donc invisible dans les tableaux.
    "empty_response": "run.err_empty",
}


def error_message(code):
    key = ERROR_KEYS.get(code)
    return _t(key) if key else _t("run.err_generic", code=code)


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
            messages.extend(self.conversation.context_messages())
        messages.append({"role": "user",
                         "content": self._with_scope(user_prompt)})
        tools = self.registry.openai_tools(self.ctx.app)

        self.observer.on_run_start(self.llm.effective_mode())
        result = RunResult(ok=False, reason="max_iterations")
        acted = False    # un outil a-t-il déjà agi sur le document ?
        try:
            for iteration in range(self.max_iterations):
                if self.cancelled:
                    return RunResult(ok=False, iterations=iteration,
                                     reason="cancelled", text=_t("run.stopped"))
                step = self.llm.step(messages, tools=tools,
                                     on_text_delta=sink.stream_delta,
                                     cancel_event=self.cancel_event,
                                     progress=self.progress)
                if step.error:
                    message = error_message(step.error)
                    self.observer.on_error(step.error, message)
                    result = RunResult(ok=False, iterations=iteration + 1,
                                       reason=step.error, text=message)
                    return result
                if self.cancelled:
                    return RunResult(ok=False, iterations=iteration + 1,
                                     reason="cancelled", text=_t("run.stopped"))
                if step.tool_calls:
                    self.progress.set_phase(_t("progress.action"))
                    self.observer.on_tool_calls(step.tool_calls)
                    results = self._execute_tool_calls(step.tool_calls)
                    messages.extend(self.llm.encode_tool_exchange(step, results))
                    acted = True
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
                                   text=final_text)
                return result

            self.observer.on_error("max_iterations", _t("run.not_converged"))
            return result
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

    # ── Exécution des outils ────────────────────────────────────────────

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
