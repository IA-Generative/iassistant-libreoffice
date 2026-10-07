"""Construction de la palette — le test qui manquait.

Une régression a échappé à 465 tests verts : `_layout()` lisait `self._width`
avant qu'il n'existe, et la palette ne s'ouvrait plus du tout. Aucun test ne
CONSTRUISAIT la palette — ils vérifiaient tous le moteur, jamais l'assemblage
de l'IHM.

Ces tests montent la palette complète sur des contrôles factices. Ils
n'inspectent pas le rendu (impossible hors LibreOffice) mais attrapent la
famille de pannes qui empêche l'ouverture : attribut manquant, appel de
méthode inexistante, mauvaise signature.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.mirai import i18n
from src.mirai.i18n import t as _t
from tests.stubs.uno_stubs import install

install()


class FakeSize:
    def __init__(self, width=60, height=16):
        self.Width = width
        self.Height = height


class FakePosSize:
    X = 0
    Y = 0
    Width = 900
    Height = 700


class FakeControlModel:
    """Modèle de contrôle à état réel : `Text`/`Label` sont de vraies chaînes.

    Un MagicMock rendrait `.Text` incomparable, et masquerait les assertions
    portant sur le contenu affiché.
    """

    def __init__(self):
        self.Text = ""
        self.Label = ""
        self.TextColor = 0
        self.FontWeight = 100.0


class FakeControl:
    """Contrôle UNO minimal : accepte tout, mesure une taille plausible."""

    def __init__(self, name):
        self.name = name
        self.visible = True
        self.model = FakeControlModel()
        self.listeners = []

    def setPosSize(self, *_args):
        pass

    def getPosSize(self):
        return FakePosSize()

    def getPreferredSize(self):
        return FakeSize()

    def setVisible(self, value):
        self.visible = value

    def setFocus(self):
        pass

    def addMouseListener(self, listener):
        self.listeners.append(listener)

    def addKeyListener(self, listener):
        self.listeners.append(listener)

    def addItemListener(self, listener):
        self.listeners.append(listener)

    def setText(self, text):
        self.model.Text = text

    def isVisible(self):
        return self.visible


class FakePeer:
    """Peer minimal : `toFront()` est ce qui remonte une fenêtre non modale."""

    def __init__(self):
        self.front_calls = 0

    def toFront(self):
        self.front_calls += 1


class FakeDialog:
    def __init__(self):
        self.controls = {}
        self.model = FakeDialogModel(self)
        self.top_listeners = []
        self.window_listeners = []
        self.visible = False
        self.title = ""
        self.peer = FakePeer()

    def setModel(self, _model):
        pass

    def getControl(self, name):
        return self.controls.setdefault(name, FakeControl(name))

    def setVisible(self, value):
        self.visible = value

    def setTitle(self, title):
        self.title = title

    def getPeer(self):
        return self.peer

    def setPosSize(self, *_args):
        pass

    def getPosSize(self):
        return FakePosSize()

    def createPeer(self, *_args):
        pass

    def addTopWindowListener(self, listener):
        self.top_listeners.append(listener)

    def removeTopWindowListener(self, listener):
        self.top_listeners.remove(listener)

    def addWindowListener(self, listener):
        self.window_listeners.append(listener)

    def dispose(self):
        pass

    def setFocus(self):
        pass


class FakeDialogModel:
    def __init__(self, dialog):
        self._dialog = dialog
        self.names = []

    def createInstance(self, _service):
        return FakeControlModel()

    def insertByName(self, name, model):
        self.names.append(name)
        # Le contrôle et son modèle doivent être le MÊME objet des deux côtés,
        # sinon une écriture via _models[...] ne se voit pas via getControl().
        control = self._dialog.getControl(name)
        control.model = model


@pytest.fixture
def palette_module(monkeypatch):
    from src.mirai.core.ui_thread import DirectDispatcher
    from src.mirai.ui import dsfr
    from src.mirai.ui import palette as palette_module

    dialog = FakeDialog()
    monkeypatch.setattr(dsfr, "make_dialog",
                        lambda *_a, **_k: (dialog, dialog.model))
    monkeypatch.setattr(dsfr, "probe_font", lambda _toolkit: "Arial")
    monkeypatch.setattr(palette_module, "_open_palette", [None])
    # Dispatcher SYNCHRONE : avec le vrai, le service AsyncCallback est un
    # MagicMock qui accepte les tâches sans jamais les exécuter — les
    # assertions porteraient alors sur un affichage jamais mis à jour.
    monkeypatch.setattr(palette_module, "MainThreadDispatcher",
                        lambda _ctx, log=None: DirectDispatcher())
    monkeypatch.setattr(palette_module, "_fake_dialog", dialog, raising=False)
    return palette_module


def _build(palette_module, app="writer"):
    shell = MagicMock()
    shell.toolkit.return_value = MagicMock()
    shell.data_dir.return_value = "/tmp/mirai-test-palette"
    shell.get_config.side_effect = lambda key, default=None: default
    shell.log = lambda _m: None

    uno_ctx = MagicMock()
    return palette_module.AssistantPalette(uno_ctx, shell, app)


def test_palette_builds_for_writer(palette_module):
    """Le test qui aurait attrapé la régression : la palette se construit."""
    palette = _build(palette_module, "writer")
    assert palette.dialog is not None


def test_palette_builds_for_calc(palette_module):
    palette = _build(palette_module, "calc")
    assert palette.dialog is not None


def test_geometry_attributes_exist_before_layout(palette_module):
    """`_layout()` lit `_width` : il doit exister dès la construction."""
    palette = _build(palette_module)
    assert isinstance(palette._width, int)
    assert isinstance(palette._height, int)


def test_layout_can_be_rerun_with_an_imposed_width(palette_module):
    """Le redimensionnement passe par là — un TypeError ici gèle la fenêtre."""
    palette = _build(palette_module)
    palette._layout(width=1200)
    assert palette._width == 1200


def test_layout_is_idempotent(palette_module):
    """Invariant : relancer _layout() est toujours sûr (bascule d'onglet…)."""
    palette = _build(palette_module)
    first = palette._width
    palette._layout()
    palette._layout()
    assert palette._width == first


def test_all_three_tabs_are_created(palette_module):
    palette = _build(palette_module)
    assert palette.dialog is not None
    for tab_id, _label in palette_module.TABS:
        assert tab_id in palette._models, f"contenu manquant : {tab_id}"
        assert f"tab_{tab_id}" in palette._models, f"onglet manquant : {tab_id}"


def test_selecting_a_tab_does_not_raise(palette_module):
    palette = _build(palette_module)
    for tab_id, _label in palette_module.TABS:
        palette.select_tab(tab_id)
    assert palette.active_tab == palette_module.TABS[-1][0]


def test_footer_has_no_settings_links(palette_module):
    """Réglages / À propos / Documentation vivent UNIQUEMENT dans le menu."""
    _build(palette_module)
    names = set(palette_module._fake_dialog.model.names)
    for forbidden in ("link_settings", "link_about", "link_doc"):
        assert forbidden not in names, f"{forbidden} ne doit plus être dans la palette"
    assert "link_clear" in names


def test_resize_listener_is_attached(palette_module):
    palette = _build(palette_module)
    palette.show()
    dialog = palette_module._fake_dialog
    assert dialog.top_listeners, "la croix de fenêtre doit être écoutée"
    assert dialog.window_listeners, "le redimensionnement doit être écouté"


def test_close_removes_listeners_before_dispose(palette_module):
    """Retirer APRÈS dispose fait notifier un contrôle détruit."""
    palette = _build(palette_module)
    palette.show()
    palette.close()
    assert palette_module._fake_dialog.top_listeners == []


def test_conversation_shows_most_recent_first(palette_module):
    """Main courante : le dernier échange doit apparaître EN HAUT."""
    palette = _build(palette_module)
    palette.conversation.load = lambda: [
        {"role": "user", "text": "première question", "app": "writer"},
        {"role": "assistant", "text": "première réponse", "app": "writer"},
        {"role": "user", "text": "seconde question", "app": "writer"},
        {"role": "assistant", "text": "seconde réponse", "app": "writer"},
    ]
    palette._history_cache = None
    palette._render_conversation()

    text = palette._models["response"].Text
    assert text.index("seconde question") < text.index("première question"), (
        "l'échange le plus récent doit être en tête du fil")
    assert text.index("seconde question") < text.index("seconde réponse"), (
        "à l'intérieur d'un échange, la question précède la réponse")


def test_current_exchange_stays_on_top(palette_module):
    palette = _build(palette_module)
    palette.conversation.load = lambda: [
        {"role": "user", "text": "ancienne", "app": "writer"},
        {"role": "assistant", "text": "ancienne réponse", "app": "writer"},
    ]
    palette._history_cache = None
    palette._render_conversation()
    palette._append_response("Vous : ", "en cours")

    text = palette._models["response"].Text
    assert text.index("en cours") < text.index("ancienne")


# Mode « ajouter à la suite »

def test_append_mode_checkbox_exists(palette_module):
    """Les deux écoles coexistent : remplacer, ou ajouter entre marqueurs."""
    _build(palette_module)
    assert "append_mode" in palette_module._fake_dialog.model.names


def test_append_mode_defaults_to_replacing(palette_module):
    palette = _build(palette_module)
    assert palette.append_mode is False


def test_append_mode_is_remembered(palette_module):
    palette = _build(palette_module)
    palette.set_append_mode(True)

    assert palette.append_mode is True
    palette.shell.set_config.assert_called_with("assistant_append_mode", "1")


def test_sink_follows_the_choice(palette_module):
    from src.mirai.core.sinks import WriterInsertSink, WriterReplaceSink

    palette = _build(palette_module)
    ctx = MagicMock()

    palette.append_mode = False
    assert isinstance(palette._document_sink(ctx), WriterReplaceSink)

    palette.append_mode = True
    sink = palette._document_sink(ctx)
    assert isinstance(sink, WriterInsertSink)
    assert "début-du-texte-modifié" in sink.header_marker


# Journal d'actions

def test_journal_receives_lines_outside_agentic_mode(palette_module):
    """L'onglet Actions restait vide sur les presets et la réécriture."""
    palette = _build(palette_module)
    palette.journal_line("⚙ Lecture du document")
    palette.journal_line("✓ Écriture appliquée")

    text = palette._models["journal"].Text
    assert "Lecture du document" in text
    assert "Écriture appliquée" in text


def test_journal_lines_also_reach_the_log_file(palette_module):
    """Sans cela, un défaut rapporté ne laisse aucune trace de ce qu'a fait le run.

    Constaté le 2026-07-26 : `~/log.txt` ne portait que « run: début » et
    « run: terminé ». Impossible de dire quel chemin avait été emprunté, ni si
    le document avait été modifié — la seule information était à l'écran.
    """
    palette = _build(palette_module)
    written = []
    palette.shell.log = written.append

    palette.journal_line("✓ Lecture du document — 45 paragraphe(s)")

    assert any("Lecture du document — 45" in line for line in written)


def test_a_step_produces_telemetry_without_the_french_text(palette_module):
    from src.mirai.core import telemetry_steps

    palette = _build(palette_module)
    palette.shell.log = lambda _m: None
    palette.shell.telemetry.reset_mock()

    palette.journal_line("↳ Titre conservé : « Rapport annuel 2026 »",
                         step=telemetry_steps.DOCUMENT_READ,
                         **{"document.paragraphs": 45})

    name, attributes = palette.shell.telemetry.call_args[0]
    assert name == telemetry_steps.SPAN
    assert attributes["document.paragraphs"] == 45
    assert "Rapport annuel" not in str(attributes), "le document ne sort pas du poste"


def test_a_line_without_a_step_sends_no_telemetry(palette_module):
    """Toutes les lignes ne sont pas des étapes : pas de bruit dans les traces."""
    palette = _build(palette_module)
    palette.shell.log = lambda _m: None
    palette.shell.telemetry.reset_mock()

    palette.journal_line("↳ Titre conservé : « Rapport annuel 2026 »")

    palette.shell.telemetry.assert_not_called()


def test_text_is_written_through_the_control(palette_module):
    """Écrire le modèle ne repeint pas toujours : le contrôle doit suivre.

    Un UnoControlEdit déjà doté d'un peer conserve la donnée sans l'afficher —
    la zone paraît vide alors qu'une relecture du modèle rend bien le texte.
    """
    palette = _build(palette_module)
    palette._set_text("response", "bonjour")

    dialog = palette_module._fake_dialog
    assert palette._models["response"].Text == "bonjour"
    assert dialog.getControl("response").model.Text == "bonjour"


def test_set_text_tolerates_a_missing_control(palette_module):
    palette = _build(palette_module)
    palette._set_text("inexistant", "x")   # ne doit pas lever


# Panneau de réflexion (le « ⓘ »)

def test_reasoning_has_its_own_tab(palette_module):
    """Le raisonnement porte son propre onglet, en plus du raccourci ⓘ.

    Auparavant il partageait le rectangle sans onglet : cliquer sur « ⓘ »
    remplaçait le contenu de l'onglet courant — la Conversation le plus
    souvent — sans rien indiquer du déplacement ni du chemin de retour.
    """
    _build(palette_module)
    names = palette_module._fake_dialog.model.names

    assert palette_module.REASONING_PANE in names
    assert "reasoning_toggle" in names          # le raccourci reste
    assert f"tab_{palette_module.REASONING_PANE}" in names
    assert ("reasoning", "tab.reasoning") in palette_module.TABS


def test_reasoning_tab_is_never_blank(palette_module):
    """Cliquer sur l'onglet avant tout run ne doit pas donner un rectangle vide,
    qui se lit comme une panne."""
    palette = _build(palette_module)
    palette.set_reasoning("")
    assert palette._models[palette_module.REASONING_PANE].Text.strip()


def test_toggle_opens_then_closes_and_restores_the_tab(palette_module):
    """Un clic ouvre et MAINTIENT ; un second revient d'où l'on vient."""
    palette = _build(palette_module)
    palette.select_tab("journal")

    palette.toggle_reasoning()
    assert palette.active_tab == palette_module.REASONING_PANE

    palette.toggle_reasoning()
    assert palette.active_tab == "journal"


def test_reasoning_pane_is_not_remembered_across_sessions(palette_module):
    """Rouvrir la palette sur un raisonnement périmé n'aurait aucun sens."""
    palette = _build(palette_module)
    palette.select_tab("response")
    palette.shell.set_config.reset_mock()

    palette.toggle_reasoning()

    saved = [c for c in palette.shell.set_config.call_args_list
             if c.args and c.args[0] == "assistant_active_tab"]
    assert saved == []


def test_marker_appears_only_when_there_is_something_to_read(palette_module):
    palette = _build(palette_module)

    palette.set_reasoning("le modèle réfléchit…")
    assert palette._models["reasoning_toggle"].Label == "ⓘ"
    assert "réfléchit" in palette._models[palette_module.REASONING_PANE].Text

    palette.set_reasoning("")
    assert palette._models["reasoning_toggle"].Label == ""


# Span de run unifié (AssistantRun sur les 4 chemins)
# L'orchestrateur n'émettait le span QUE pour le mode agentique : pipeline,
# réécriture de sélection et réécriture de document étaient invisibles.
# Le point d'émission unique est le finally de `_run_in_worker`.

def _run_spans(palette):
    return [c.args[1] for c in palette.shell.telemetry.call_args_list
            if c.args and c.args[0] == "AssistantRun"]


def _step_spans(shell, step_name):
    from src.mirai.core import telemetry_steps
    return [c.args[1] for c in shell.telemetry.call_args_list
            if c.args and c.args[0] == telemetry_steps.SPAN
            and c.args[1].get("step.name") == step_name]


def _fake_preset(**overrides):
    base = dict(id="summarize", label="📝 Résumer", mode="pipeline",
                needs_input=False, input_hint="", apps=("writer",))
    base.update(overrides)
    return SimpleNamespace(**base)


def test_worker_emits_exactly_one_run_span(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_pipeline",
                        lambda preset, prompt, ctx, shown: {"ok": True})
    palette._run_in_worker(_fake_preset(), "", MagicMock(), "shown")

    spans = _run_spans(palette)
    assert len(spans) == 1
    attrs = spans[0]
    assert attrs["run.kind"] == "pipeline"
    assert attrs["assistant.preset"] == "summarize"
    assert attrs["assistant.ok"] is True
    assert attrs["assistant.cancelled"] is False
    assert attrs["append.mode"] is False
    assert isinstance(attrs["assistant.duration_ms"], int)
    assert "assistant.reason" not in attrs, "pas de reason sur un succès"


def test_selection_rewrite_branch_has_its_kind(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_selection_rewrite",
                        lambda ctx, prompt, selection: {"ok": True})
    palette._run_in_worker(None, "réécris ce passage", MagicMock(), "shown",
                           snapshot={"rewrite_selection": True,
                                     "selection": "du texte"})
    assert _run_spans(palette)[0]["run.kind"] == "selection_rewrite"
    assert _run_spans(palette)[0]["assistant.preset"] == "free"


def test_document_rewrite_branch_has_its_kind(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_document_rewrite",
                        lambda ctx, prompt, paragraphs, styles: {"ok": True})
    palette._run_in_worker(None, "réécris tout", MagicMock(), "shown",
                           snapshot={"rewrite": True, "paragraphs": ["a"],
                                     "styles": [""]})
    assert _run_spans(palette)[0]["run.kind"] == "document_rewrite"


def test_agentic_summary_reaches_the_span(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_agentic",
                        lambda preset, prompt, ctx: {
                            "ok": False, "reason": "max_iterations",
                            "mode": "json", "iterations": 6})
    palette._run_in_worker(None, "demande libre", MagicMock(), "shown")

    attrs = _run_spans(palette)[0]
    assert attrs["run.kind"] == "agentic"
    assert attrs["assistant.ok"] is False
    assert attrs["assistant.reason"] == "max_iterations"
    assert attrs["assistant.mode"] == "json"
    assert attrs["assistant.iterations"] == 6


def test_a_crashed_run_still_emits_its_span(palette_module, monkeypatch):
    def _boom(preset, prompt, ctx):
        raise RuntimeError("panne interne")
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_agentic", _boom)
    palette._run_in_worker(None, "demande", MagicMock(), "shown")

    attrs = _run_spans(palette)[0]
    assert attrs["assistant.ok"] is False
    assert attrs["assistant.reason"] == "exception"
    assert "panne interne" not in str(attrs), "jamais le texte de l'erreur"


def test_a_closed_palette_still_emits_its_span(palette_module, monkeypatch):
    from src.mirai.core.ui_thread import DispatcherClosed

    def _closed(preset, prompt, ctx):
        raise DispatcherClosed("fermée")
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_agentic", _closed)
    palette._run_in_worker(None, "demande", MagicMock(), "shown")

    assert _run_spans(palette)[0]["assistant.reason"] == "palette_closed"


def test_a_cancelled_run_is_flagged(palette_module, monkeypatch):
    palette = _build(palette_module)
    palette._cancel = threading.Event()
    palette._cancel.set()
    monkeypatch.setattr(palette, "_run_agentic",
                        lambda preset, prompt, ctx: {"ok": False,
                                                     "reason": "cancelled"})
    palette._run_in_worker(None, "demande", MagicMock(), "shown")

    attrs = _run_spans(palette)[0]
    assert attrs["assistant.cancelled"] is True
    assert attrs["assistant.reason"] == "cancelled"


def test_document_rewrite_reports_why_it_did_nothing(palette_module):
    """Les sorties anticipées doivent nommer leur cause dans le résumé."""
    palette = _build(palette_module)
    assert palette._run_document_rewrite(
        MagicMock(), "x", []) == {"ok": False, "reason": "empty_document"}
    assert palette._run_document_rewrite(
        MagicMock(), "x", ["Titre"], ["Heading 1"]) == {
            "ok": False, "reason": "headings_only"}


def test_rewrites_keep_the_document_language(palette_module, monkeypatch):
    """Sélection ou document entier : le texte réécrit garde sa langue, quelle
    que soit celle de l'interface."""
    sent = []

    class _Client:
        def __init__(self, _shell, max_tokens=None):
            pass

        def step(self, messages, **_kwargs):
            sent.append(messages)
            return SimpleNamespace(error="network_error")

    monkeypatch.setattr(palette_module, "LLMClient", _Client)
    i18n.set_locale("en")
    palette = _build(palette_module)
    palette._progress = palette_module.RunProgress()
    palette._run_selection_rewrite(MagicMock(), "shorten", "Some English text.")
    palette._run_document_rewrite(
        MagicMock(), "shorten", ["First paragraph.", "Second paragraph."])

    assert len(sent) == 2
    for messages in sent:
        system = messages[0]["content"]
        assert "MÊME LANGUE que le texte fourni" in system
        assert i18n.t("llm.answer_language") not in system


# Refus de lancement

def test_an_empty_prompt_refusal_is_counted(palette_module):
    palette = _build(palette_module)
    palette._start_run(preset=None)

    spans = _step_spans(palette.shell, "run.refused")
    assert len(spans) == 1
    assert spans[0]["refuse.reason"] == "empty_prompt"
    assert palette.busy is False


def test_the_same_refusal_twice_is_emitted_once(palette_module):
    """Entrée martelée sur un prompt vide : un span, pas une rafale."""
    palette = _build(palette_module)
    palette._start_run(preset=None)
    palette._start_run(preset=None)
    assert len(_step_spans(palette.shell, "run.refused")) == 1


def test_a_different_refusal_is_emitted_again(palette_module):
    palette = _build(palette_module)
    palette._start_run(preset=None)                      # empty_prompt
    palette._start_run(preset=_fake_preset(
        id="transform", needs_input=True,
        input_hint="Décrivez la transformation."))       # preset_needs_input

    reasons = [s["refuse.reason"] for s in _step_spans(palette.shell, "run.refused")]
    assert reasons == ["empty_prompt", "preset_needs_input"]
    assert _step_spans(palette.shell, "run.refused")[1]["preset.name"] == "transform"


def test_wrong_app_refusal_is_counted(palette_module):
    palette = _build(palette_module, "writer")
    palette._models["prompt"].Text = "transforme la colonne"
    palette._start_run(preset=_fake_preset(id="transform", apps=("calc",)))
    assert _step_spans(palette.shell, "run.refused")[0]["refuse.reason"] == "wrong_app"


def test_no_document_refusal_is_counted(palette_module):
    palette = _build(palette_module)
    palette._models["prompt"].Text = "résume"
    palette.uno_ctx.getServiceManager.return_value.createInstanceWithContext \
        .return_value.getCurrentComponent.return_value = None
    palette._start_run(preset=None)
    assert _step_spans(palette.shell, "run.refused")[0]["refuse.reason"] == "no_document"


# Session : compteurs agrégés, un seul span à la fermeture
# Un span par clic d'onglet saturerait l'envoi (1 span = 1 requête HTTP) :
# les interactions IHM sont comptées en mémoire et partent en UNE fois.

def test_close_emits_one_session_summary(palette_module):
    palette = _build(palette_module)
    palette._on_tab_click("journal")
    palette._on_tab_click("suggestions")
    palette.toggle_reasoning()      # ouverture : comptée
    palette.toggle_reasoning()      # fermeture : non comptée
    palette._on_clear()
    palette.set_append_mode(True)

    palette.close()
    palette.close()                 # croix + dispose : jamais deux spans

    spans = _step_spans(palette.shell, "palette.closed")
    assert len(spans) == 1
    attrs = spans[0]
    assert attrs["tabs.switches"] == 2
    assert attrs["suggestions.views"] == 1
    assert attrs["reasoning.opens"] == 1
    assert attrs["conversation.clears"] == 1
    assert attrs["append.toggles"] == 1
    assert attrs["runs.count"] == 0
    assert isinstance(attrs["session.duration_ms"], int)


def test_programmatic_tab_switches_are_not_counted(palette_module):
    """`select_tab` est appelé par show(), les runs, la restauration : seul le
    CLIC de l'utilisateur mesure un usage."""
    palette = _build(palette_module)
    palette.select_tab("journal")
    palette.select_tab("response")
    palette.close()
    assert _step_spans(palette.shell, "palette.closed")[0]["tabs.switches"] == 0


def test_runs_are_counted_in_the_session(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_run_agentic",
                        lambda preset, prompt, ctx: {"ok": True})
    palette._run_in_worker(None, "demande", MagicMock(), "shown")
    palette.close()
    assert _step_spans(palette.shell, "palette.closed")[0]["runs.count"] == 1


def test_refocusing_an_open_palette_is_telemetered(palette_module):
    palette = _build(palette_module)
    palette_module._open_palette[0] = palette
    shell = MagicMock()

    result = palette_module.open_or_focus(MagicMock(), shell, "writer")

    assert result is palette
    assert len(_step_spans(shell, "palette.refocused")) == 1


# Filet anti-blocage

def test_heal_stuck_is_telemetered(palette_module):
    """Chaque déclenchement du filet = un lot de messages async perdus.
    Sa fréquence sur le parc est un signal de santé, pas un détail."""
    palette = _build(palette_module)
    palette.busy = True
    palette._worker = None

    palette.heal_if_stuck()

    assert palette.busy is False
    assert len(_step_spans(palette.shell, "ui.heal_stuck")) == 1


def test_heal_does_nothing_on_a_healthy_palette(palette_module):
    palette = _build(palette_module)
    palette.heal_if_stuck()
    assert _step_spans(palette.shell, "ui.heal_stuck") == []


# Analyse asynchrone du document (onglet Suggestions)
#
# La pompe du dispatcher NE TOURNE QUE PENDANT UN RUN (ui_thread.start_pump).
# La première version lisait le document par `dispatcher.call` depuis le thread
# d'analyse : hors run, l'appel expirait au bout de 10 s et l'onglet restait
# muet — « le thread principal n'a pas répondu », constaté en recette le
# 2026-08-04. Le texte est donc lu sur le THREAD PRINCIPAL avant de partir, et
# la pompe est armée là aussi.

class _Reponse:
    def __init__(self, text="", error="", finish_reason="stop"):
        self.text, self.error, self.finish_reason = text, error, finish_reason


def _client(reponse=None, budgets=None, boum=False):
    class _C:
        def __init__(self, _shell, max_tokens=None):
            if budgets is not None:
                budgets.append(max_tokens)

        def step(self, _messages, progress=None):
            if boum:
                raise RuntimeError("relais injoignable")
            return reponse
    return _C


def _analyse(palette, palette_module, monkeypatch, client, doc="Un texte. " * 60):
    """Déroule l'analyse de bout en bout, sans thread (déterministe)."""
    monkeypatch.setattr(palette, "_document_text", lambda: doc)
    monkeypatch.setattr(palette_module, "LLMClient", client)
    lances = []
    monkeypatch.setattr(
        palette_module.threading, "Thread",
        lambda target=None, args=(), **kw: type(
            "T", (), {"start": lambda _s: lances.append((target, args))})())
    demarre = palette.start_document_analysis()
    for target, args in lances:
        if target == palette._analyse_in_worker:      # on n'anime pas en test
            target(*args)
    return demarre


def test_document_is_read_on_the_main_thread(palette_module, monkeypatch):
    """Aucun `dispatcher.call` : c'est lui qui expirait hors run."""
    palette = _build(palette_module)
    appels = []
    monkeypatch.setattr(palette.dispatcher, "call",
                        lambda *a, **k: appels.append(1))
    _analyse(palette, palette_module, monkeypatch,
             _client(_Reponse(text="- Une proposition")))
    assert appels == []


def test_pump_is_armed_before_the_worker_starts(palette_module, monkeypatch):
    """Sans pompe armée, le résultat ne serait jamais affiché."""
    palette = _build(palette_module)
    armee = []
    monkeypatch.setattr(palette.dispatcher, "start_pump",
                        lambda: armee.append(1))
    _analyse(palette, palette_module, monkeypatch,
             _client(_Reponse(text="- Une proposition")))
    assert armee == [1]


def test_successful_analysis_lands_in_the_tab(palette_module, monkeypatch):
    palette = _build(palette_module)
    _analyse(palette, palette_module, monkeypatch, _client(
        _Reponse(text="- Ajouter des intertitres\n- Scinder le paragraphe 4")))
    assert "Ajouter des intertitres" in palette._analysis_text
    assert "Ajouter des intertitres" in palette._models["suggestions"].Text


def test_truncated_analysis_drops_the_cut_item(palette_module, monkeypatch):
    palette = _build(palette_module)
    _analyse(palette, palette_module, monkeypatch, _client(_Reponse(
        text="- Ajouter des intertitres\n- Fusionner les sections en une seule chron",
        finish_reason="length")))
    assert "Ajouter des intertitres" in palette._analysis_text
    assert "chron" not in palette._analysis_text


def test_analysis_asks_for_a_large_budget(palette_module, monkeypatch):
    """Réflexion et réponse partagent le plafond : trop serré, gemma-4 ne rend rien."""
    palette = _build(palette_module)
    budgets = []
    _analyse(palette, palette_module, monkeypatch,
             _client(_Reponse(text="- Une proposition"), budgets=budgets))
    assert budgets == [palette_module.doc_analysis.MAX_TOKENS]


def test_failed_analysis_falls_back_to_static_suggestions(palette_module, monkeypatch):
    palette = _build(palette_module)
    _analyse(palette, palette_module, monkeypatch, _client(boum=True))
    assert palette._analysis_text == ""
    assert palette._analysis_running is False
    assert palette._models["suggestions"].Text == palette._analysis_base


def test_empty_answer_falls_back_to_static_suggestions(palette_module, monkeypatch):
    palette = _build(palette_module)
    _analyse(palette, palette_module, monkeypatch, _client(_Reponse(text="")))
    assert palette._analysis_text == ""
    assert palette._models["suggestions"].Text == palette._analysis_base


def test_short_document_is_reported_without_calling_the_model(palette_module, monkeypatch):
    palette = _build(palette_module)
    budgets = []
    demarre = _analyse(palette, palette_module, monkeypatch,
                       _client(_Reponse(), budgets=budgets), doc="Trois mots.")
    assert demarre is False
    assert budgets == []                  # aucun aller-retour réseau inutile
    assert palette._analysis_text == _t("analysis.too_short")


def test_no_analysis_outside_writer(palette_module, monkeypatch):
    palette = _build(palette_module, app="calc")
    assert palette.start_document_analysis() is False


def test_analysis_is_not_started_twice(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_document_text", lambda: "Un texte. " * 60)
    monkeypatch.setattr(palette_module.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda _s: None})())
    assert palette.start_document_analysis() is True
    assert palette.start_document_analysis() is False     # la première tourne


def test_a_run_invalidates_a_previous_analysis(palette_module):
    """Le document a changé : le constat précédent ne le décrit plus."""
    palette = _build(palette_module)
    palette._analysis_text = "constat périmé"
    palette._finish_run()
    assert palette._analysis_text == ""


def test_wait_is_shown_in_the_status_line_like_a_run(palette_module, monkeypatch):
    """Même endroit, même format que le run — une seule habitude à prendre.

    L'onglet, lui, garde les suggestions statiques : une animation dans la zone
    ET une autre dans la ligne d'état apprendraient deux endroits où regarder.
    """
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_document_text", lambda: "Un texte. " * 60)
    monkeypatch.setattr(palette_module.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda _s: None})())
    assert palette.start_document_analysis() is True
    rendu = palette._progress.render()
    assert _t("analysis.phase") in rendu
    assert _t("analysis.phase") not in palette._models["suggestions"].Text


def test_analysis_gauge_is_not_stopped_by_a_run_that_took_over(palette_module, monkeypatch):
    """Un run démarré pendant l'analyse pose SA jauge : la couper afficherait
    un run figé alors qu'il travaille."""
    palette = _build(palette_module)
    arrets = []
    monkeypatch.setattr(palette, "_stop_pulse", lambda: arrets.append(1))
    ancienne = palette_module.RunProgress()
    palette._progress = palette_module.RunProgress()      # le run a pris la main
    palette._publish_analysis(ancienne)
    assert arrets == []


# Lignes cliquables (Conversation et Suggestions)

def _clic(palette, name, offset):
    palette._pick_from_pane(name, offset)
    return palette._models["prompt"].Text


def test_clicking_a_suggestion_fills_the_prompt(palette_module):
    palette = _build(palette_module)
    palette._models["suggestions"].Text = ("1. ▸ Résumer la sélection\n"
                                           "2. · Reformuler en langage clair")
    assert _clic(palette, "suggestions", 26) == "Reformuler en langage clair"


def test_clicking_a_conversation_turn_fills_the_prompt(palette_module):
    palette = _build(palette_module)
    palette._models["response"].Text = "Vous : résume ce document\nMIrAI : c'est fait."
    assert _clic(palette, "response", 0) == "résume ce document"


def test_clicking_a_section_title_leaves_the_prompt_alone(palette_module):
    """Un clic pour LIRE ne doit pas écraser ce que l'utilisateur a tapé."""
    palette = _build(palette_module)
    palette._models["prompt"].Text = "ma demande en cours"
    palette._models["suggestions"].Text = "Propositions d'amélioration :\n· Ajouter des titres"
    assert _clic(palette, "suggestions", 0) == "ma demande en cours"


def test_click_is_ignored_during_a_run(palette_module):
    """Pendant un run la saisie est grisée : la modifier sèmerait la confusion."""
    palette = _build(palette_module)
    palette._models["prompt"].Text = "en cours"
    palette._models["suggestions"].Text = "· Ajouter des intertitres"
    palette.busy = True
    assert _clic(palette, "suggestions", 0) == "en cours"


def test_click_never_starts_a_run(palette_module, monkeypatch):
    """Le clic REMPLIT la saisie, il ne lance rien : dans une zone où l'on
    clique aussi pour lire, une action serait irrattrapable."""
    palette = _build(palette_module)
    lances = []
    monkeypatch.setattr(palette, "_on_send", lambda *_a: lances.append(1))
    palette._models["suggestions"].Text = "· Ajouter des intertitres"
    _clic(palette, "suggestions", 0)
    assert lances == []


def test_clickable_panes_have_a_mouse_listener(palette_module):
    """Sans écouteur posé, toute la logique de clic serait morte."""
    _build(palette_module)
    dialog = palette_module._fake_dialog
    for name in ("response", "suggestions"):
        listeners = dialog.getControl(name).listeners
        assert any(isinstance(handler, palette_module._PaneClickHandler)
                   for handler in listeners), f"aucun écouteur sur « {name} »"


def test_journal_and_reasoning_are_not_clickable(palette_module):
    """Un journal d'actions ou un raisonnement ne sont pas des demandes."""
    _build(palette_module)
    dialog = palette_module._fake_dialog
    for name in ("journal", palette_module.REASONING_PANE):
        listeners = dialog.getControl(name).listeners
        assert not any(isinstance(handler, palette_module._PaneClickHandler)
                       for handler in listeners), f"« {name} » ne doit pas être cliquable"


# Fenêtre non modale, titre, et application courante
#
# La palette était une fenêtre POSSÉDÉE par la fenêtre de document active à son
# ouverture. Mesuré le 2026-08-04 sur un Mac à trois écrans : impossible de la
# sortir de l'écran de son propriétaire, et elle le suivait au pixel près. Avec
# plusieurs documents, elle restait collée au premier tout en agissant sur le
# document courant — donc potentiellement un autre.

def test_window_keeps_an_explicit_parent(palette_module, monkeypatch):
    """Le parent EXPLICITE est conservé, faute de mieux.

    Il est bien la cause du confinement à un écran — mais passer `None` ne le
    corrige pas : VCL rattache alors le dialogue à la fenêtre active de
    l'application, et le suivi mesuré est identique (+1500/+261 dans les deux
    cas, 2026-08-04, trois écrans). Autant garder un parent prévisible, qui
    tient au moins la palette au-dessus du document.
    """
    parents = []
    monkeypatch.setattr(palette_module._fake_dialog, "createPeer",
                        lambda _toolkit, parent: parents.append(parent))
    _build(palette_module)
    assert parents and parents[0] is not None


def test_bring_to_front_raises_the_window(palette_module):
    """`setFocus` seul donne le clavier sans changer l'ordre d'empilement."""
    palette = _build(palette_module)
    dialog = palette_module._fake_dialog
    dialog.visible = False
    palette.bring_to_front()
    assert dialog.visible is True
    assert dialog.peer.front_calls == 1


def test_title_names_the_document(palette_module, monkeypatch):
    """Avec plusieurs documents, rien ne disait sur lequel la demande portait."""
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_current_document_name", lambda: "rapport.odt")
    palette.refresh_title()
    assert palette_module._fake_dialog.title == "MIrAI — Assistant · rapport.odt"


def test_title_falls_back_without_a_document(palette_module, monkeypatch):
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_current_document_name", lambda: "")
    palette.refresh_title()
    assert palette_module._fake_dialog.title == "MIrAI — Assistant"


def test_title_is_not_rewritten_when_unchanged(palette_module, monkeypatch):
    """Reposer le même titre fait clignoter la barre de fenêtre."""
    palette = _build(palette_module)
    monkeypatch.setattr(palette, "_current_document_name", lambda: "rapport.odt")
    palette.refresh_title()
    poses = []
    monkeypatch.setattr(palette_module._fake_dialog, "setTitle", poses.append)
    palette.refresh_title()
    assert poses == []


def test_current_app_follows_the_active_document(palette_module, monkeypatch):
    """`self.app` est figé à l'ouverture : suivre le document réel."""
    palette = _build(palette_module, app="writer")

    class _Calc:
        Sheets = object()

    desktop = MagicMock()
    desktop.getCurrentComponent.return_value = _Calc()
    palette.uno_ctx.getServiceManager.return_value.createInstanceWithContext.return_value = desktop
    assert palette.current_app() == "calc"


def test_current_app_falls_back_when_no_document(palette_module):
    palette = _build(palette_module, app="writer")
    desktop = MagicMock()
    desktop.getCurrentComponent.return_value = None
    palette.uno_ctx.getServiceManager.return_value.createInstanceWithContext.return_value = desktop
    assert palette.current_app() == "writer"
