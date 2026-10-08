"""Le raisonnement et la réponse se partagent `max_tokens` — et la réponse vient en dernier.

Panne mesurée sur le relais Scaleway, modèle `gemma-4-26b-a4b-it` :
l'utilisateur voyait « ⚠ Réponse inexploitable — le document n'a pas été modifié »
de façon INTERMITTENTE, sur un prompt identique qui marchait l'instant d'avant.

Le mécanisme, établi en rejouant l'appel à plafond décroissant :

    max_tokens | finish | raisonnement | réponse | résultat
          4000 |   stop |    9 074 car |   1 093 | 2 paragraphes ✓
          3500 |   stop |    8 775 car |   1 248 | 2 paragraphes ✓
          3000 |   stop |   10 930 car |   1 246 | 2 paragraphes ✓
          2000 | length |    8 060 car |       0 | ⚠ inexploitable

Le modèle émet 8 000 à 14 300 caractères de raisonnement AVANT la moindre ligne
de réponse. À 4 000 tokens (~16 000 caractères) le compte passe la plupart du
temps ; quand la réflexion est un peu plus bavarde, elle mange tout et la
réponse n'est jamais émise. `llama-3.3-70b-instruct`, qui n'émet aucun
raisonnement, ne peut pas rencontrer ce défaut — d'où « ça marche avec l'un,
pas avec l'autre ».

Pourquoi on n'a PAS coupé le raisonnement à la source : `reasoning_effort` est
refusé en HTTP 400 par trois modèles du relais sur cinq (llama-3.3,
mistral-small, gpt-oss). Élargir le plafond est accepté partout.
"""

from src.mirai.core.llm_client import LLMClient, StepResult
from tests.stubs.fake_shell import (
    FakeShell,
    FakeSSEResponse,
    native_tool_call_chunks,
    reasoning_chunks,
    text_chunks,
)

# La signature de la panne

def test_budget_starved_by_reasoning_is_recognised():
    step = StepResult(finish_reason="length", reasoning_chars=8060, text="")
    assert step.starved_by_reasoning


def test_a_normal_truncation_is_not_the_same_defect():
    """Coupé au plafond APRÈS avoir répondu : la réponse existe, on la garde."""
    step = StepResult(finish_reason="length", reasoning_chars=8060,
                      text="Un premier paragraphe déjà écrit")
    assert not step.starved_by_reasoning


def test_a_model_without_reasoning_never_matches():
    step = StepResult(finish_reason="length", reasoning_chars=0, text="")
    assert not step.starved_by_reasoning


def test_a_tool_call_is_a_real_answer():
    """Le budget a servi : ne pas relancer et payer deux fois l'appel."""
    step = StepResult(finish_reason="length", reasoning_chars=9000,
                      text="", tool_calls=[object()])
    assert not step.starved_by_reasoning


# La reprise

def test_reasoning_is_counted_from_both_field_names():
    """Les relais nomment le champ `reasoning` ou `reasoning_content`."""
    for key in ("reasoning", "reasoning_content"):
        shell = FakeShell(responses=[FakeSSEResponse(
            reasoning_chunks("Je réfléch", "is encore", content="Voilà.",
                             finish="stop", key=key))])
        step = LLMClient(shell).step([{"role": "user", "content": "x"}])
        assert step.reasoning_chars == len("Je réfléchis encore"), key


def test_starved_run_is_retried_with_a_wider_budget():
    shell = FakeShell(responses=[
        FakeSSEResponse(reasoning_chunks("pensée " * 50)),          # rien
        FakeSSEResponse(text_chunks("Le document réécrit.")),       # reprise
    ])
    step = LLMClient(shell).step([{"role": "user", "content": "réécris"}])

    assert step.text == "Le document réécrit."
    assert len(shell.requests) == 2, "une reprise, pas plus"
    premier, reprise = (r["max_tokens"] for r in shell.requests)
    assert reprise > premier, "la reprise doit élargir le plafond"
    assert reprise >= 12000


def test_the_retry_happens_once_and_never_degrades():
    """Si la reprise échoue aussi, on rend le premier résultat, pas le pire."""
    shell = FakeShell(responses=[
        FakeSSEResponse(reasoning_chunks("pensée " * 50)),
        FakeSSEResponse(reasoning_chunks("encore " * 50)),
    ])
    step = LLMClient(shell).step([{"role": "user", "content": "réécris"}])

    assert len(shell.requests) == 2, "jamais de troisième tentative"
    assert step.starved_by_reasoning, "l'appelant doit pouvoir le dire à l'utilisateur"


def test_a_successful_run_is_never_retried():
    shell = FakeShell(responses=[FakeSSEResponse(
        reasoning_chunks("je réfléchis", content="Réponse.", finish="stop"))])
    step = LLMClient(shell).step([{"role": "user", "content": "x"}])

    assert step.text == "Réponse."
    assert len(shell.requests) == 1


def test_a_tool_call_run_is_never_retried():
    shell = FakeShell(
        config={"llm_tool_mode": "native"},
        responses=[FakeSSEResponse(
            native_tool_call_chunks("writer_get_selection", "{}"))])
    tools = [{"type": "function", "function": {
        "name": "writer_get_selection", "description": "d",
        "parameters": {"type": "object", "properties": {}}}}]
    LLMClient(shell).step([{"role": "user", "content": "x"}], tools=tools)

    assert len(shell.requests) == 1


def test_cancelling_prevents_the_retry():
    """Annuler doit arrêter le travail, pas déclencher un appel plus coûteux."""
    class Cancelled:
        def is_set(self):
            return True

    shell = FakeShell(responses=[FakeSSEResponse(reasoning_chunks("pensée " * 50))])
    LLMClient(shell).step([{"role": "user", "content": "x"}],
                          cancel_event=Cancelled())

    # 0 et non 1 : une annulation déjà posée court-circuite jusqu'à l'émission
    # de la requête. Ce qui compte ici est qu'il n'y ait pas de SECONDE.
    assert len(shell.requests) < 2


# La reprise réussie doit se VOIR
# L'échec est télémétré par la palette (`llm.reasoning_starved`). Sans le
# pendant « réussi », impossible de savoir si la reprise élargie sert à
# quelque chose — ou si elle coûte un aller-retour pour rien.

def _retry_steps(shell):
    from src.mirai.core import telemetry_steps
    return [attrs for span, attrs in shell.telemetry_events
            if span == telemetry_steps.SPAN
            and attrs.get("step.name") == telemetry_steps.REASONING_RETRY_OK]


def test_a_successful_retry_is_telemetered_with_its_cost():
    shell = FakeShell(responses=[
        FakeSSEResponse(reasoning_chunks("pensée " * 50)),
        FakeSSEResponse(text_chunks("Le document réécrit.")),
    ])
    LLMClient(shell).step([{"role": "user", "content": "réécris"}])

    steps = _retry_steps(shell)
    assert len(steps) == 1
    attrs = steps[0]
    assert attrs["reasoning.chars"] > 0
    assert attrs["retry.max_tokens"] >= 12000


def test_a_failed_retry_emits_no_success_step():
    shell = FakeShell(responses=[
        FakeSSEResponse(reasoning_chunks("pensée " * 50)),
        FakeSSEResponse(reasoning_chunks("encore " * 50)),
    ])
    LLMClient(shell).step([{"role": "user", "content": "réécris"}])
    assert _retry_steps(shell) == []


def test_an_untried_run_emits_no_retry_step():
    shell = FakeShell(responses=[FakeSSEResponse(
        reasoning_chunks("je réfléchis", content="Réponse.", finish="stop"))])
    LLMClient(shell).step([{"role": "user", "content": "x"}])
    assert _retry_steps(shell) == []
