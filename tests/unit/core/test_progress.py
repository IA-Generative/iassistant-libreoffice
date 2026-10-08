"""Jauge d'activité : montrer que ça travaille, et à quoi.

Sur une opération longue, une interface immobile est indiscernable d'une
interface plantée. Ces tests portent sur la matière affichée — phase, jetons,
durée — indépendamment de toute IHM.
"""

from src.mirai.core.progress import (
    SPINNER_FRAMES,
    NullProgress,
    RunProgress,
)


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_estimates_tokens_from_characters():
    progress = RunProgress()
    progress.on_text("x" * 40)
    assert progress.tokens == 10          # 40 / 4
    assert progress.is_exact is False


def test_exact_usage_overrides_the_estimate():
    """Si le relais envoie `usage`, il fait autorité."""
    progress = RunProgress()
    progress.on_text("x" * 4000)
    progress.exact_tokens(137)

    assert progress.tokens == 137
    assert progress.is_exact is True


def test_reasoning_counts_but_names_a_different_phase():
    """Distinguer « réfléchit » de « rédige » : l'utilisateur veut savoir."""
    progress = RunProgress()
    progress.on_reasoning("y" * 20)

    assert progress.phase == "Réflexion"
    assert progress.tokens == 5


def test_text_switches_the_phase_to_writing():
    progress = RunProgress()
    progress.on_reasoning("y" * 20)
    progress.on_text("bonjour")
    assert progress.phase == "Rédaction"


def test_tool_phase_can_be_set_explicitly():
    progress = RunProgress()
    progress.set_phase("Action sur le document")
    assert progress.phase == "Action sur le document"


def test_spinner_cycles_without_repeating_immediately():
    progress = RunProgress()
    frames = [progress.spin() for _ in range(len(SPINNER_FRAMES))]
    assert frames == list(SPINNER_FRAMES)
    assert progress.spin() == SPINNER_FRAMES[0], "le cycle doit boucler"


def test_elapsed_uses_the_injected_clock():
    clock = FakeClock()
    progress = RunProgress(now=clock)
    clock.advance(12.4)
    assert int(progress.elapsed) == 12


def test_render_shows_phase_tokens_and_duration():
    clock = FakeClock()
    progress = RunProgress(now=clock)
    progress.on_text("x" * 400)
    clock.advance(7)

    line = progress.render()

    assert "Rédaction" in line
    assert "100 tk" in line
    assert "7 s" in line
    assert line[0] in SPINNER_FRAMES


def test_render_marks_estimated_counts():
    """Ne jamais faire passer une approximation pour une mesure."""
    progress = RunProgress()
    progress.on_text("x" * 400)
    assert "~100 tk" in progress.render()

    progress.exact_tokens(88)
    rendered = progress.render()
    assert "88 tk" in rendered
    assert "~" not in rendered


def test_render_omits_tokens_before_anything_arrives():
    progress = RunProgress()
    assert "tk" not in progress.render()


def test_large_counts_are_readable():
    progress = RunProgress()
    progress.exact_tokens(12400)
    assert "12 400 tk" in progress.render()


def test_render_can_avoid_advancing_the_spinner():
    progress = RunProgress()
    first = progress.render(spin=False)
    second = progress.render(spin=False)
    assert first == second


def test_null_progress_accepts_everything():
    """Hors interface, la jauge doit être inerte sans jamais gêner."""
    progress = NullProgress()
    progress.on_text("x")
    progress.on_reasoning("y")
    progress.exact_tokens(5)
    progress.set_phase("peu importe")


# Raisonnement consultable au survol

def test_reasoning_is_kept_for_the_tooltip():
    progress = RunProgress()
    progress.on_reasoning("Je commence par lire le document. ")
    progress.on_reasoning("Puis je le découpe en deux.")

    assert "lire le document" in progress.tooltip
    assert "découpe en deux" in progress.tooltip


def test_reasoning_keeps_the_end_not_the_beginning():
    """Une infobulle doit montrer où EN EST la réflexion, pas son préambule."""
    from src.mirai.core.progress import REASONING_TOOLTIP_CHARS

    progress = RunProgress()
    progress.on_reasoning("DÉBUT" + "x" * (REASONING_TOOLTIP_CHARS * 2))
    progress.on_reasoning("FIN")

    reasoning = progress.tooltip.partition("\n\n")[2]
    assert len(reasoning) <= REASONING_TOOLTIP_CHARS
    assert reasoning.endswith("FIN")
    assert "DÉBUT" not in reasoning


def test_tooltip_falls_back_to_the_streamed_text():
    """Tous les modèles n'émettent pas de raisonnement — la plupart n'ont que
    du texte. L'infobulle doit rester utile dans ce cas."""
    progress = RunProgress()
    progress.on_text("Le préfet arrête que…")

    tooltip = progress.tooltip
    assert "Texte en cours" in tooltip
    assert "Le préfet arrête" in tooltip


def test_tooltip_prefers_reasoning_when_available():
    progress = RunProgress()
    progress.on_text("du texte")
    progress.on_reasoning("je réfléchis")

    tooltip = progress.tooltip
    assert "Réflexion du modèle" in tooltip
    assert "je réfléchis" in tooltip


def test_tooltip_is_empty_before_anything_arrives():
    assert RunProgress().tooltip == ""
    assert NullProgress().tooltip == ""


# Activité nommée (tâche de fond)
#
# Une tâche partie d'un simple clic d'onglet — l'analyse du document — doit
# rester identifiable. Sans cela `on_reasoning` écrase la phase et la ligne
# d'état devient indiscernable d'un run que l'utilisateur n'a pas demandé.

def test_named_activity_survives_reasoning():
    p = RunProgress(activity="Analyse du document")
    p.on_reasoning("x" * 400)
    assert p.phase == "Analyse du document"


def test_named_activity_survives_text():
    p = RunProgress(activity="Analyse du document")
    p.on_text("y" * 400)
    assert p.phase == "Analyse du document"


def test_named_activity_survives_set_phase():
    p = RunProgress(activity="Analyse du document")
    p.set_phase("Action sur le document")
    assert p.phase == "Analyse du document"


def test_named_activity_still_counts_tokens_and_time():
    """Même format que le run : seule l'étiquette d'activité est figée."""
    p = RunProgress(activity="Analyse du document")
    p.on_reasoning("x" * 400)
    rendu = p.render()
    assert "Analyse du document" in rendu
    assert "tk" in rendu and rendu.endswith("s")


def test_without_activity_phases_still_move():
    """Le run garde son déroulé : c'est lui qui a été demandé explicitement."""
    p = RunProgress()
    p.on_reasoning("x")
    assert p.phase == "Réflexion"
    p.on_text("y")
    assert p.phase == "Rédaction"
    p.set_phase("Action sur le document")
    assert p.phase == "Action sur le document"
