"""Palette universelle MIrAI — fenêtre de prompt non modale, style DSFR.

Point d'entrée unique de l'assistant : prompt libre + chips des fonctions
fréquentes + fil de conversation persisté + journal d'actions optionnel
(séquence des outils proposés puis exécutés, façon agent).

Layout MESURÉ : les positions/tailles ne sont jamais estimées en pixels fixes
— après création du peer, chaque contrôle texte est dimensionné via
getPreferredSize() (tailles réelles de rendu, HiDPI/Retina compris) et le
reste est dérivé d'un facteur d'échelle. Invariant : relancer _layout() est
toujours sûr (repli du journal, etc.).

Threading : le run entier vit dans un thread worker ; le thread principal
retourne immédiatement à la boucle d'événements de LibreOffice, qui reste donc
utilisable pendant toute la génération (frappe, défilement, autre document).
Tout ce qui touche UNO — document, contrôles, undo — repasse par
`MainThreadDispatcher` (core/ui_thread.py). Le drapeau `busy` interdit deux
runs simultanés ; `_cancel` permet d'arrêter celui en cours.
"""

import threading
import time

import unohelper
from com.sun.star.awt import XKeyListener

try:
    from com.sun.star.awt import XCallback
except Exception:
    class XCallback:  # stub hors LO (tests)
        pass

try:
    from com.sun.star.view import XSelectionChangeListener as _XSelectionChangeListener
except Exception:
    _XSelectionChangeListener = None

try:
    from com.sun.star.awt import XTopWindowListener as _XTopWindowListener
except Exception:
    _XTopWindowListener = None

try:
    from com.sun.star.awt import XItemListener as _XItemListener
except Exception:
    _XItemListener = None

from ..core import (
    capabilities,
    clickable,
    doc_analysis,
    doc_rewrite,
    prompts,
    selection_info,
    suggestions,
    telemetry_steps,
)
from ..core import presets as presets_module
from ..core.context import ToolContext
from ..core.conversation import ConversationStore
from ..core.llm_client import LLMClient
from ..core.orchestrator import Orchestrator, RunObserver, error_message
from ..core.progress import RunProgress
from ..core.registry import ToolRegistry
from ..core.sinks import PaletteSink, WriterInsertSink, WriterReplaceSink
from ..core.tools import register_all
from ..core.ui_thread import DispatcherClosed, MainThreadDispatcher
from . import dsfr

try:
    from com.sun.star.awt.Key import ESCAPE as KEY_ESCAPE
    from com.sun.star.awt.Key import RETURN as KEY_RETURN
except Exception:
    KEY_RETURN, KEY_ESCAPE = 1280, 1281

TOOL_LABELS = {
    "writer_get_selection": "Lecture de la sélection",
    "writer_get_document_map": "Lecture du document",
    "writer_replace_paragraphs": "Réécriture des paragraphes",
    "writer_replace_selection": "Remplacement de la sélection",
    "writer_insert_text": "Insertion de texte",
    "writer_find_replace": "Remplacements dans le document",
    "calc_get_selection": "Lecture de la sélection",
    "calc_read_range": "Lecture d'une plage",
    "calc_get_sheet_overview": "Analyse de la structure",
    "calc_write_cells": "Écriture de cellules",
    "calc_write_result_column": "Écriture de la colonne résultat",
    "calc_set_formula": "Écriture de la formule",
    "calc_fill_formula_down": "Recopie de la formule",
}

_open_palette = [None]   # singleton de session

FLUSH_INTERVAL_S = 0.12   # cadence maximale des mises à jour d'affichage
FLUSH_CHARS = 80          # ou dès qu'on a accumulé ce nombre de caractères
PULSE_INTERVAL_S = 0.2    # cadence d'animation de la jauge d'activité

# Zone basse : trois contenus, un seul rectangle. L'identifiant sert aussi de
# nom de contrôle (« response » porte l'historique, déjà créé plus haut).
# « Conversation » et non « Historique » : cet onglet porte le FIL en cours,
# restauré d'une session à l'autre. « Historique » laissait attendre une liste
# de conversations passées, qui n'existe pas (une seule conversation en v1).
# « Raisonnement » a désormais SON onglet. Il partageait le rectangle des
# autres sans en porter un : le clic sur « ⓘ » remplaçait donc le contenu de
# l'onglet courant — le plus souvent la Conversation — sans que rien n'indique
# où l'on venait d'atterrir ni comment revenir. Un onglet nommé rend le
# déplacement visible, réversible d'un clic, et lisible sans mode d'emploi.
TABS = (("response", "Conversation"),
        ("suggestions", "Suggestions"),
        ("reasoning", "Raisonnement"),
        ("journal", "Actions"))

REASONING_PANE = "reasoning"
BOTTOM_PANES = tuple(tab_id for tab_id, _ in TABS)

REASONING_EMPTY = ("Le raisonnement du modèle s'affichera ici pendant "
                   "la prochaine demande.")

# Le retour d'un run doit se VOIR : une ligne de statut colorée selon l'issue,
# pas un texte discret dans une zone grise. C'est la leçon du « il ne se passe
# rien » — l'action partait bien, mais rien ne le signalait à l'écran.
STATUS_COLORS = {
    "neutral": dsfr.TOKENS["text_mention"],
    "error":   dsfr.TOKENS["error"],
    "success": dsfr.TOKENS["success"],
}


class _WindowCloser(unohelper.Base,
                    *([_XTopWindowListener] if _XTopWindowListener else [])):
    """Ferme la palette quand l'utilisateur clique la croix de la fenêtre.

    Un `UnoControlDialog` non modal ne se ferme PAS tout seul : la croix émet
    `windowClosing` et attend que quelqu'un agisse. Sans ce listener, le bouton
    de fermeture est inopérant — la fenêtre reste à l'écran quoi qu'on fasse.

    `windowActivated` sert de filet à l'indicateur de sélection : en Writer le
    listener de sélection ne se déclenche pas toujours sur un simple
    déplacement du curseur ; revenir sur la palette relit la cible.
    """

    def __init__(self, palette):
        self._palette = palette

    def windowClosing(self, _event):
        self._palette.close()

    def windowActivated(self, _event):
        self._palette.heal_if_stuck()
        self._palette.refresh_selection_label()

    # Le reste de l'interface : rien à faire, mais UNO exige les méthodes.
    def windowOpened(self, _event):
        pass

    def windowClosed(self, _event):
        pass

    def windowMinimized(self, _event):
        pass

    def windowNormalized(self, _event):
        pass

    def windowDeactivated(self, _event):
        pass

    def disposing(self, _event):
        pass


try:
    from com.sun.star.awt import XWindowListener as _XWindowListener
except Exception:
    _XWindowListener = None


class _ResizeWatcher(unohelper.Base,
                     *([_XWindowListener] if _XWindowListener else [])):
    """Relance le layout sur la taille RÉELLE du peer après redimensionnement.

    Sans cela, `Sizeable` agrandit le cadre mais les contrôles restent à leur
    place : la fenêtre grandit, son contenu non.
    """

    def __init__(self, palette):
        self._palette = palette

    def windowResized(self, event):
        self._palette.on_resized(event.Width, event.Height)

    def windowMoved(self, _event):
        pass

    def windowShown(self, _event):
        pass

    def windowHidden(self, _event):
        pass

    def disposing(self, _event):
        pass


class _AppendModeListener(unohelper.Base,
                          *([_XItemListener] if _XItemListener else [])):
    """Retient le choix « ajouter à la suite » dès que la case change."""

    def __init__(self, palette):
        self._palette = palette

    def itemStateChanged(self, event):
        self._palette.set_append_mode(bool(getattr(event, "Selected", 0)))

    def disposing(self, _event):
        pass


class _SelectionWatcher(unohelper.Base,
                        *([_XSelectionChangeListener]
                          if _XSelectionChangeListener else [])):
    """Suit la sélection du document — en PUSH, jamais en polling.

    LibreOffice livre `selectionChanged` sur le thread principal : on peut donc
    écrire dans les contrôles depuis le callback sans marshalling. C'est ce qui
    distingue ce patron du thread de rafraîchissement du code historique, qui
    écrivait dans des contrôles VCL toutes les 3 s depuis un thread de fond,
    sans SolarMutex.
    """

    def __init__(self, palette):
        self._palette = palette

    def selectionChanged(self, _event):
        self._palette.refresh_selection_label()

    def disposing(self, _event):
        self._palette.detach_selection_watcher()


def _selection_string(ctx):
    """Texte sélectionné, ou chaîne vide. À n'appeler que sur le thread principal."""
    try:
        return ctx.controller.getSelection().getByIndex(0).getString()
    except Exception:
        return ""


def _writer_targets(model):
    """(texte sélectionné, texte du paragraphe courant) — jamais d'exception.

    Sans sélection les actions ciblent le paragraphe sous le curseur : il faut
    donc pouvoir l'afficher, sinon l'indicateur laisse croire qu'aucune cible
    n'est déterminée.
    """
    selected = paragraph = ""
    try:
        selected = model.CurrentController.getSelection().getByIndex(0).getString()
    except Exception:
        selected = ""
    if not selected.strip():
        try:
            view_cursor = model.CurrentController.getViewCursor()
            text = view_cursor.getText()
            cursor = text.createTextCursorByRange(view_cursor)
            cursor.gotoStartOfParagraph(False)
            cursor.gotoEndOfParagraph(True)
            paragraph = cursor.getString()
        except Exception:
            paragraph = ""
    return selected, paragraph


def _calc_selection_sample(model, max_values=40):
    """(nombre de cellules, échantillon de valeurs) — jamais d'exception."""
    try:
        selection = model.CurrentController.Selection
        area = selection.getRangeAddress()
        rows = abs(area.EndRow - area.StartRow) + 1
        cols = abs(area.EndColumn - area.StartColumn) + 1
        sheet = model.CurrentController.ActiveSheet
        values = []
        for row in range(area.StartRow, area.EndRow + 1):
            for col in range(area.StartColumn, area.EndColumn + 1):
                if len(values) >= max_values:
                    return rows * cols, values
                text = sheet.getCellByPosition(col, row).getString()
                if text:
                    values.append(text)
        return rows * cols, values
    except Exception:
        return 0, []


def _friendly_error(exc):
    """Traduit une panne technique en phrase actionnable pour l'utilisateur."""
    text = str(exc)
    if "401" in text or "Unauthorized" in text or "Missing credentials" in text:
        return ("Jeton expiré — Menu MIrAI ▸ Paramètres pour vous reconnecter.")
    if "timeout" in text.lower() or "timed out" in text.lower():
        return "Le service n'a pas répondu à temps. Réessayez dans un instant."
    if "thread principal n'a pas répondu" in text:
        return "LibreOffice était occupé (fenêtre ouverte ?). Réessayez."
    return f"Erreur : {text}"


class _DeltaCoalescer:
    """Regroupe les fragments du flux avant de les envoyer au thread principal.

    Sans ce tampon, un flux rapide poste un événement UNO par token et sature
    la file du thread principal — l'application redevient molle alors même
    qu'on vient de la libérer. On ne publie donc qu'au plus toutes les
    ~120 ms, ou dès ~80 caractères accumulés.
    """

    def __init__(self, flush):
        self._flush = flush
        self._pending = []
        self._chars = 0
        self._last_flush = 0.0

    def add(self, text):
        if not text:
            return
        self._pending.append(text)
        self._chars += len(text)
        now = time.monotonic()
        if self._chars >= FLUSH_CHARS or (now - self._last_flush) >= FLUSH_INTERVAL_S:
            self.flush(now)

    def flush(self, now=None):
        """Publie ce qui est en attente. Sûr même si rien n'a été accumulé."""
        if not self._pending:
            return
        text = "".join(self._pending)
        self._pending = []
        self._chars = 0
        self._last_flush = now if now is not None else time.monotonic()
        self._flush(text)

    def reset(self):
        self._pending = []
        self._chars = 0
        self._last_flush = 0.0


class _DeferredCall(unohelper.Base, XCallback):
    """Exécute fn dans un événement utilisateur PROPRE (AsyncCallback).

    INVARIANT : ne jamais lancer un run LLM directement depuis un listener
    souris/clavier — le pompage processEventsToIdle depuis un dispatch
    imbriqué gèle l'UI et peut aborter LibreOffice (std::terminate dans
    DispatchUserEvents). On sort du dispatch courant avant de travailler.
    """

    def __init__(self, fn):
        self._fn = fn

    def notify(self, data):
        try:
            self._fn()
        except Exception:
            pass


class _KeyHandler(unohelper.Base, XKeyListener):
    def __init__(self, on_return, on_escape):
        self._on_return = on_return
        self._on_escape = on_escape

    def keyPressed(self, event):
        try:
            if event.KeyCode == KEY_RETURN and not (event.Modifiers & 1):
                self._on_return()
            elif event.KeyCode == KEY_ESCAPE:
                self._on_escape()
        except Exception:
            pass

    def keyReleased(self, event):
        pass

    def disposing(self, event):
        pass


class _PaneClickHandler(unohelper.Base, dsfr.XMouseListener):
    """Clic dans une zone de texte : reporte la ligne cliquée dans la saisie.

    On lit la position au RELÂCHEMENT et non à l'appui : c'est le clic qui
    déplace le curseur, donc à `mousePressed` la position est encore l'ancienne
    et on rapporterait la ligne précédemment cliquée.
    """

    def __init__(self, control, on_pick):
        self._control = control
        self._on_pick = on_pick

    def mouseReleased(self, _event):
        try:
            self._on_pick(self._control.getSelection().Min)
        except Exception:
            pass

    def mousePressed(self, _event):
        pass

    def mouseEntered(self, _event):
        pass

    def mouseExited(self, _event):
        pass


class _JournalObserver(RunObserver):
    """Alimente le journal d'actions de la palette (optionnel, repliable)."""

    def __init__(self, palette):
        self._palette = palette
        self.lines = []
        self.acted = False    # un outil a-t-il agi pendant CE run ?

    def _tool_label(self, call):
        return TOOL_LABELS.get(call.name, call.name)

    def _capabilities(self):
        """Libellés des outils réellement disponibles ici, sans doublon."""
        try:
            specs = self._palette.registry.list_tools(self._palette.app)
        except Exception:
            return []
        labels = []
        for spec in specs:
            label = TOOL_LABELS.get(spec.name, spec.name)
            if label not in labels:
                labels.append(label)
        return labels

    def _render(self):
        self._palette.set_journal_text("\n".join(self.lines))

    def on_run_start(self, mode):
        self.lines = [f"Mode outils : {mode}"]
        self.acted = False
        self._render()

    def on_tool_calls(self, calls):
        for call in calls:
            self.lines.append(f"⏳ {self._tool_label(call)}…")
        self._render()

    def on_tool_result(self, call, result, duration_ms):
        icon = "✓" if result.ok else "✗"
        label = self._tool_label(call)
        self.acted = True
        for index in range(len(self.lines) - 1, -1, -1):
            if self.lines[index] == f"⏳ {label}…":
                self.lines[index] = f"{icon} {label} ({duration_ms} ms)"
                break
        else:
            self.lines.append(f"{icon} {label} ({duration_ms} ms)")
        if not result.ok and result.error:
            self.lines.append(f"   ↳ {result.error[:120]}")
        self._render()

    def on_final(self, text):
        """Run terminé SANS qu'aucun outil n'ait agi : le dire explicitement.

        Sans cette trace, une demande que l'assistant ne sait pas satisfaire
        — « sauvegarde le document », alors qu'aucun outil de sauvegarde
        n'existe — se solde par une réponse en texte et un onglet Actions vide.
        L'utilisateur ne peut alors pas distinguer trois situations très
        différentes : l'action a eu lieu, elle a échoué, ou la capacité
        n'existe pas. On lève le doute, et on annonce ce qui est faisable ici.
        """
        if self.acted:
            return
        self.lines.append("ℹ Aucune action sur le document — réponse en texte seul.")
        labels = self._capabilities()
        if labels:
            self.lines.append(f"   Ici, l'assistant sait : {', '.join(labels)}.")
        self._render()

    def on_error(self, code, message):
        self.lines.append(f"⚠ {message}")
        self._render()


class AssistantPalette:
    def __init__(self, uno_ctx, shell, app, callbacks):
        self.uno_ctx = uno_ctx
        self.shell = shell
        self.app = app                      # "writer" | "calc" à l'ouverture
        self.callbacks = callbacks          # settings / about / documentation
        self.busy = False
        self.active_tab = self._restore_tab(shell)
        self._bottom_height = 0        # ajusté par le redimensionnement
        self._current_exchange = []    # tour en cours, affiché en tête du fil
        self._history_cache = None     # historique relu seulement quand il change
        self._journal_lines = []       # onglet « Actions » du run courant
        self._tab_before_reasoning = None   # onglet à restaurer en refermant
        self._analysis_text = ""       # analyse du document, quand elle arrive
        self._analysis_running = False  # une seule analyse à la fois
        self._analysis_base = ""       # socle statique, repli si l'analyse échoue
        self._analysis_progress = None  # jauge de l'analyse (partagée avec le run)
        self._title_shown = ""         # dernier titre posé (évite le clignotement)
        self.append_mode = self._restore_append_mode(shell)
        self._progress = None          # jauge du run en cours
        self._pulse = None             # thread d'animation de la jauge
        self._width = 0                # largeur courante (0 = pas encore mesurée)
        self._height = 0
        self._base_bottom_h = 0        # hauteur de zone basse au premier layout
        self._natural_height = 0       # hauteur totale au premier layout
        self._min_width = 0            # largeur qui garde les chips sur UNE ligne
        self._scale = 1.0
        self._laying_out = False       # garde anti-réentrance (_layout → setPosSize)
        self._resize_watcher = None
        self.registry = register_all(ToolRegistry())
        self.conversation = ConversationStore(shell.data_dir())
        self.dialog = None
        self._models = {}
        self._handlers = []                 # garde les listeners vivants (GC)
        self._chip_names = []
        # Exécution non bloquante : le run vit dans un worker, tout ce qui
        # touche UNO repasse par le dispatcher (voir core/ui_thread.py).
        self.dispatcher = MainThreadDispatcher(uno_ctx, log=shell.log)
        self._worker = None
        self._cancel = None
        self._delta_buffer = _DeltaCoalescer(self._flush_deltas)
        self._selection_watcher = None      # (listener, contrôleur) — garde vivante
        # Usage de la session : compté en mémoire, envoyé en UNE fois à la
        # fermeture. Un span par clic d'onglet saturerait l'envoi — côté
        # coquille, une trace coûte une requête HTTP et un thread.
        self._opened_at = time.monotonic()
        self._closed = False
        self._session = {"runs": 0, "tabs": 0, "suggestions": 0,
                         "reasoning": 0, "clears": 0, "append_toggles": 0}
        self._last_refusal = ""             # anti-rafale sur Entrée martelée
        self._build()

    # ── Construction (création des contrôles, positions posées par _layout) ──
    def _build(self):
        toolkit = self.shell.toolkit()
        font = dsfr.probe_font(toolkit)
        self._font = font

        app_label = "Writer" if self.app == "writer" else "Calc"
        dialog, model = dsfr.make_dialog(
            self.uno_ctx, "MIrAI — Assistant", 640, 560)
        self.dialog, self.model = dialog, model

        _, header_model = dsfr.add_control(
            dialog, model, "header", "FixedText", 0, 0, 640, 40, {
                "Label": f"  MIrAI — Assistant ({app_label})",
                "BackgroundColor": dsfr.TOKENS["primary"],
                "TextColor": dsfr.TOKENS["text_inverted"],
                "FontName": font, "FontHeight": 7, "FontWeight": 150.0,
                "VerticalAlign": 1,
            })
        self._models["header"] = header_model

        for preset in presets_module.presets_for(self.app):
            name = f"chip_{preset.id}"
            control, _chip_model = dsfr.add_chip(
                dialog, model, name, preset.label, 0, 0, 100, 28, font,
                on_click=(lambda p=preset: self._on_chip(p)))
            self._chip_names.append(name)
            self._handlers.append(control)

        # Indicateur de sélection : ce sur quoi l'action va porter, en direct.
        _, selection_model = dsfr.add_control(
            dialog, model, "selection", "FixedText", 0, 0, 100, 18, {
                "Label": "",
                "TextColor": dsfr.TOKENS["text_mention"],
                "FontName": font, "FontHeight": 7,
            })
        self._models["selection"] = selection_model

        prompt_control, prompt_model = dsfr.add_control(
            dialog, model, "prompt", "Edit", 0, 0, 100, 56, {
                "MultiLine": True, "AutoVScroll": True,
                "FontName": font, "FontHeight": 7,
                "TextColor": dsfr.TOKENS["text_body"],
                # Champ DSFR : fond contraste + bordure sombre, bien visible
                "BackgroundColor": dsfr.TOKENS["bg_contrast"],
                "Border": 2, "BorderColor": dsfr.TOKENS["text_body"],
                "HelpText": "Décrivez ce que l'assistant doit faire",
            })
        self._models["prompt"] = prompt_model

        _, status_model = dsfr.add_control(
            dialog, model, "status", "FixedText", 0, 0, 100, 18, {
                "Label": "",
                "TextColor": dsfr.TOKENS["text_mention"],
                "FontName": font, "FontHeight": 7,
            })
        self._models["status"] = status_model

        # Deux écoles chez les utilisateurs : remplacer la sélection, ou
        # ajouter le résultat à la suite entre marqueurs pour comparer avant de
        # décider. On ne tranche pas — on laisse choisir, et le choix est
        # mémorisé d'une session à l'autre.
        reasoning_control, reasoning_toggle = dsfr.add_control(
            dialog, model, "reasoning_toggle", "FixedText", 0, 0, 20, 18, {
                "Label": "",
                "TextColor": dsfr.TOKENS["primary"],
                "FontName": font, "FontHeight": 8,
                "HelpText": "Voir ce que le modèle est en train de faire",
            })
        self._models["reasoning_toggle"] = reasoning_toggle
        reasoning_handler = dsfr.ClickHandler(
            reasoning_toggle, on_click=self.toggle_reasoning,
            fg=dsfr.TOKENS["primary"], fg_hover=dsfr.TOKENS["primary_hover"])
        reasoning_control.addMouseListener(reasoning_handler)
        self._handlers.append(reasoning_handler)

        _, append_model = dsfr.add_control(
            dialog, model, "append_mode", "CheckBox", 0, 0, 150, 18, {
                "Label": "Ajouter à la suite",
                "State": 1 if self.append_mode else 0,
                "FontName": font, "FontHeight": 7,
                "TextColor": dsfr.TOKENS["text_mention"],
                "HelpText": ("Coché : le résultat est inséré après la sélection, "
                             "entre marqueurs, et l'original est conservé.\n"
                             "Décoché : le résultat remplace la sélection."),
            })
        self._models["append_mode"] = append_model
        append_handler = _AppendModeListener(self)
        dialog.getControl("append_mode").addItemListener(append_handler)
        self._handlers.append(append_handler)

        _, send_model = dsfr.add_primary_button(
            dialog, model, "send", "Envoyer  ⏎", 0, 0, 120, 32, font,
            self._on_send)
        self._models["send"] = send_model

        _, response_model = dsfr.add_control(
            dialog, model, "response", "Edit", 0, 0, 100, 200, {
                "MultiLine": True, "ReadOnly": True, "VScroll": True,
                "FontName": font, "FontHeight": 7,
                "TextColor": dsfr.TOKENS["text_body"],
                "BackgroundColor": dsfr.TOKENS["bg_alt"],
                "Border": 2, "BorderColor": dsfr.TOKENS["border"],
            })
        self._models["response"] = response_model
        response_click = _PaneClickHandler(
            dialog.getControl("response"),
            lambda offset: self._pick_from_pane("response", offset))
        dialog.getControl("response").addMouseListener(response_click)
        self._handlers.append(response_click)

        # Zone basse : UN seul rectangle, trois contenus superposés qu'on
        # bascule par setVisible(). Réempiler trois zones distinctes ferait
        # exploser la hauteur — c'est justement ce qu'on corrige ici.
        for name, color, background in (
            ("suggestions", dsfr.TOKENS["text_body"], dsfr.TOKENS["bg_accent"]),
            ("journal", dsfr.TOKENS["text_mention"], dsfr.TOKENS["bg_accent"]),
            (REASONING_PANE, dsfr.TOKENS["text_mention"], dsfr.TOKENS["bg_alt"]),
        ):
            control, control_model = dsfr.add_control(
                dialog, model, name, "Edit", 0, 0, 100, 100, {
                    "MultiLine": True, "ReadOnly": True, "VScroll": True,
                    "FontName": font, "FontHeight": 7,
                    "TextColor": color, "BackgroundColor": background,
                    "Border": 2, "BorderColor": dsfr.TOKENS["border"],
                })
            self._models[name] = control_model
            control.setVisible(False)
            # Conversation et Suggestions sont rejouables d'un clic : la ligne
            # cliquée remonte dans la zone de saisie. Le journal d'actions et
            # le raisonnement, eux, ne sont pas des demandes.
            if name in ("suggestions",):
                pane_handler = _PaneClickHandler(
                    control, lambda offset, n=name: self._pick_from_pane(n, offset))
                control.addMouseListener(pane_handler)
                self._handlers.append(pane_handler)

        # Onglets : des FixedText cliquables (pas de UnoControlTabPageContainer,
        # capricieux et peu stylable). L'onglet actif porte la couleur accent.
        for tab_id, label in TABS:
            name = f"tab_{tab_id}"
            control, tab_model = dsfr.add_control(
                dialog, model, name, "FixedText", 0, 0, 90, 16, {
                    "Label": label,
                    "TextColor": dsfr.TOKENS["text_mention"],
                    "FontName": font, "FontHeight": 7,
                })
            self._models[name] = tab_model
            handler = dsfr.ClickHandler(
                tab_model, on_click=(lambda t=tab_id: self._on_tab_click(t)),
                fg=dsfr.TOKENS["text_mention"], fg_hover=dsfr.TOKENS["primary_hover"])
            control.addMouseListener(handler)
            self._handlers.append(handler)

        # Réglages / À propos / Documentation vivent UNIQUEMENT dans le menu
        # 🤖 MIrAI : la fenêtre ne garde que ce qui sert à travailler.
        dsfr.add_link(dialog, model, "link_clear", "🗑 Nouvelle conversation",
                      0, 0, 120, 16, font, self._on_clear)
        _, hint_model = dsfr.add_control(
            dialog, model, "hint", "FixedText", 0, 0, 120, 16, {
                "Label": "Entrée : envoyer · Échap : fermer",
                "TextColor": dsfr.TOKENS["text_mention"],
                "FontName": font, "FontHeight": 6, "Align": 2,
            })
        self._models["hint"] = hint_model

        # La palette est une fenêtre POSSÉDÉE par la fenêtre de document : sur
        # macOS elle ne peut pas quitter l'écran de son propriétaire et le suit
        # au pixel près. Mesuré le 2026-08-04 sur trois écrans.
        #
        # Passer `None` en parent NE LE CORRIGE PAS — essayé et mesuré le même
        # jour : VCL rattache alors le dialogue à la fenêtre active de
        # l'application, et le suivi reste identique (+1500/+261 dans les deux
        # cas). On garde donc le parent explicite, qui a au moins le mérite
        # d'être prévisible et de tenir la palette au-dessus du document.
        #
        # Une vraie fenêtre autonome demanderait `toolkit.createWindow` avec un
        # WindowDescriptor de type TOP, au lieu d'un UnoControlDialog. Voir
        # `bring_to_front` pour ce qui est faisable sans cette refonte.
        frame = self.uno_ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.uno_ctx).getCurrentFrame()
        parent_window = frame.getContainerWindow() if frame else None
        dialog.createPeer(toolkit, parent_window)

        self._layout()

        # On s'ouvre toujours SUR la fenêtre de document — c'est là que regarde
        # l'utilisateur. Autonome ne veut pas dire posée n'importe où : elle
        # cesse seulement d'y être enchaînée ensuite.
        if parent_window is not None:
            try:
                ps = parent_window.getPosSize()
                x = ps.X + max(0, (ps.Width - self._width) // 2)
                y = ps.Y + max(0, (ps.Height - self._height) // 3)
                # Un parent qui rend des coordonnées relatives (0,0) placerait
                # la palette dans le coin de l'écran principal, hors du champ de
                # vision sur une configuration à plusieurs écrans. Dans ce cas
                # on laisse le toolkit décider plutôt que de viser à l'aveugle.
                if ps.Width > 0 and ps.Height > 0:
                    dialog.setPosSize(x, y, 0, 0, 3)  # POS
            except Exception:
                pass

        key_handler = _KeyHandler(self._on_send, self.close)
        prompt_control.addKeyListener(key_handler)
        self._handlers.append(key_handler)

        # Échap doit fermer quel que soit le contrôle qui a le focus : le
        # brancher sur le seul champ de prompt laissait la fenêtre coincée dès
        # que le focus était ailleurs (une chip, la zone de réponse…).
        escape_handler = _KeyHandler(lambda: None, self.close)
        self._handlers.append(escape_handler)
        for name in ("response", "journal", "send", *self._chip_names):
            try:
                dialog.getControl(name).addKeyListener(escape_handler)
            except Exception:
                pass          # contrôle absent selon l'application — sans gravité

        # La croix de la fenêtre : sans listener, elle ne ferme RIEN.
        self._attach_window_closer()

        self._render_conversation()

    def _attach_window_closer(self):
        """Branche fermeture et redimensionnement sur le peer (après createPeer)."""
        if _XTopWindowListener is not None:
            try:
                closer = _WindowCloser(self)
                self.dialog.addTopWindowListener(closer)
                self._handlers.append(closer)   # référence vivante (GC)
                self._window_closer = closer
            except Exception as exc:
                self.shell.log(f"[palette] listener de fenêtre indisponible : {exc}")
        if _XWindowListener is not None:
            try:
                resizer = _ResizeWatcher(self)
                self.dialog.addWindowListener(resizer)
                self._handlers.append(resizer)
                self._resize_watcher = resizer
            except Exception as exc:
                self.shell.log(f"[palette] redimensionnement indisponible : {exc}")

    # ── Redimensionnement ───────────────────────────────────────────────

    def on_resized(self, width, height):
        """Réagit à un redimensionnement : toute la hauteur gagnée va en bas.

        Les bornes empêchent d'écraser la ligne de chips (largeur minimale
        calculée au premier layout) ou de faire disparaître la zone basse.
        """
        if self._laying_out:
            return                     # _layout() appelle setPosSize : pas de boucle
        width = max(width, self._min_width or 0)
        extra = height - self._natural_height
        self._bottom_height = max(int(70 * self._scale), self._base_bottom_h + extra)
        self._width = width
        self._laying_out = True
        try:
            self._layout(width=width)
        finally:
            self._laying_out = False
        self._save_geometry()

    def _save_geometry(self):
        """Mémorise position et taille pour la prochaine ouverture."""
        try:
            ps = self.dialog.getPosSize()
            self.shell.set_config(
                "assistant_window_rect", f"{ps.X},{ps.Y},{ps.Width},{ps.Height}")
        except Exception:
            pass          # préférence d'affichage : jamais bloquant

    def _restore_geometry(self):
        """Restaure la géométrie mémorisée, si elle est encore plausible."""
        try:
            raw = str(self.shell.get_config("assistant_window_rect", "") or "")
            x, y, width, height = (int(part) for part in raw.split(","))
        except Exception:
            return
        if width < 200 or height < 150:
            return                     # valeur aberrante : on garde le défaut
        try:
            self.dialog.setPosSize(x, y, width, height, 15)
            self.on_resized(width, height)
        except Exception:
            pass

    # ── Layout mesuré ───────────────────────────────────────────────────
    def _preferred(self, name):
        try:
            return self.dialog.getControl(name).getPreferredSize()
        except Exception:
            return None

    def _place(self, name, x, y, w, h):
        control = self.dialog.getControl(name)
        if control is not None:
            control.setPosSize(int(x), int(y), int(w), int(h), 15)  # POSSIZE

    def _layout(self, width=None):
        """Positionne tout à partir des tailles réelles de rendu.

        `width` force la largeur (redimensionnement) ; sinon on repart de la
        largeur naturelle calculée à l'échelle du rendu.
        """
        # Échelle dérivée de la hauteur réelle d'une chip (HiDPI-safe)
        chip_prefs = {}
        line_h = 18
        for name in self._chip_names:
            pref = self._preferred(name)
            if pref is not None:
                chip_prefs[name] = pref
                line_h = max(line_h, pref.Height)
        scale = min(max(1.0, line_h / 16.0), 2.0)

        margin = int(10 * scale)
        gap = int(6 * scale)
        chip_h = int(line_h + 6 * scale)
        width = int(width or self._width or 500 * scale)
        self._width = width

        # Bandeau
        header_pref = self._preferred("header")
        header_h = int((header_pref.Height if header_pref else 20) + 10 * scale)
        self._place("header", 0, 0, width, header_h)
        y = header_h + gap

        # Chips avec retour à la ligne, largeur = taille préférée + padding
        x = margin
        for name in self._chip_names:
            pref = chip_prefs.get(name)
            w = int((pref.Width if pref else 90) + 12 * scale)
            if x + w > width - margin and x > margin:
                x = margin
                y += chip_h + gap
            self._place(name, x, y, w, chip_h)
            x += w + gap
        y += chip_h + int(8 * scale)

        # Indicateur de sélection — sous les chips, au-dessus du prompt : c'est
        # la réponse au « sur quoi ça va porter ? » posée avant de cliquer.
        selection_h = int(line_h + 2 * scale)
        self._place("selection", margin, y, width - 2 * margin, selection_h)
        y += selection_h + int(4 * scale)

        # Prompt (≈ 3 lignes de texte)
        prompt_h = int(line_h * 2 + 12 * scale)
        self._place("prompt", margin, y, width - 2 * margin, prompt_h)
        y += prompt_h + gap

        # Statut + bouton Envoyer (largeur mesurée)
        send_pref = self._preferred("send")
        send_w = int((send_pref.Width if send_pref else 100) + 22 * scale)
        send_h = int(line_h + 10 * scale)
        self._place("send", width - margin - send_w, y, send_w, send_h)
        append_pref = self._preferred("append_mode")
        append_w = int((append_pref.Width if append_pref else 130) + 24 * scale)
        append_x = width - margin - send_w - gap - append_w
        self._place("append_mode", append_x, y + (send_h - line_h) // 2,
                    append_w, line_h)
        # « ⓘ » collé à la fin du statut : c'est là que le regard se pose
        # pendant un run, et il ne prend de la place que s'il est actif.
        toggle_w = int(18 * scale)
        status_w = max(0, append_x - margin - gap - toggle_w)
        self._place("status", margin, y + (send_h - line_h) // 2,
                    status_w, line_h)
        self._place("reasoning_toggle", margin + status_w,
                    y + (send_h - line_h) // 2, toggle_w, line_h)
        y += send_h + int(8 * scale)

        # Zone basse : les trois contenus occupent EXACTEMENT le même
        # rectangle ; seul l'onglet actif est visible. Toute la hauteur gagnée
        # au redimensionnement lui revient — le reste garde sa taille.
        bottom_h = max(int(70 * scale), self._bottom_height or int(120 * scale))
        for pane in BOTTOM_PANES:
            self._place(pane, margin, y, width - 2 * margin, bottom_h)
            try:
                self.dialog.getControl(pane).setVisible(pane == self.active_tab)
            except Exception:
                pass
        y += bottom_h + int(4 * scale)

        # Onglets en bas à gauche, « Nouvelle conversation » et hint à droite.
        x = margin
        for tab_id, _label in TABS:
            name = f"tab_{tab_id}"
            pref = self._preferred(name)
            w = int((pref.Width if pref else 70) + 8 * scale)
            self._place(name, x, y, w, line_h)
            x += w + int(8 * scale)
        clear_pref = self._preferred("link_clear")
        clear_w = int((clear_pref.Width if clear_pref else 120) + 6 * scale)
        self._place("link_clear", max(x, width - margin - clear_w), y, clear_w, line_h)
        y += line_h + int(4 * scale)

        hint_pref = self._preferred("hint")
        hint_w = int((hint_pref.Width if hint_pref else 90) + 6 * scale)
        self._place("hint", width - margin - hint_w, y, hint_w, line_h)
        y += line_h + margin

        # Trace de géométrie : un onglet « vide » est le plus souvent un
        # contrôle hors champ ou masqué, pas un contenu manquant.
        try:
            visible = [p for p in BOTTOM_PANES
                       if self.dialog.getControl(p).isVisible()]
            bottom_y = y - bottom_h - int(4 * scale)
            self.shell.log(
                f"[palette] layout: fenêtre {width}x{y}, zone basse "
                f"y={bottom_y} h={bottom_h}, actif={self.active_tab}, "
                f"visibles={visible}")
        except Exception:
            pass

        self._height = y
        self._scale = scale
        if not self._natural_height:
            # Premier layout : il fixe les bornes du redimensionnement.
            self._natural_height = y
            self._base_bottom_h = bottom_h
            self._min_width = width
        ps = self.dialog.getPosSize()
        self.dialog.setPosSize(ps.X, ps.Y, width, self._height, 15)

    # ── Affichage ───────────────────────────────────────────────────────
    def show(self):
        self.dialog.setVisible(True)
        try:
            self.dialog.getControl("prompt").setFocus()
        except Exception:
            pass
        # Après createPeer : le contrôleur est prêt à accepter un listener.
        self.attach_selection_watcher()
        self.refresh_selection_label()
        self.select_tab(self.active_tab)
        self._restore_geometry()

    def close(self):
        """Ferme la palette et neutralise tout run encore en vol.

        L'ordre compte : on annule d'abord, on rend le dispatcher inerte
        ensuite, et seulement après on dispose. Un worker qui se réveille
        pendant la fermeture reçoit DispatcherClosed au lieu de toucher un
        contrôle détruit.
        """
        # La croix de la fenêtre ET le dispose passent tous deux par ici : le
        # résumé de session ne doit partir qu'une fois.
        if not self._closed:
            self._closed = True
            self._emit_session_summary()
        if self._cancel is not None:
            self._cancel.set()
        # Retirer les listeners AVANT dispose() : l'ordre inverse laisse
        # LibreOffice notifier un contrôle détruit.
        self.detach_selection_watcher()
        closer = getattr(self, "_window_closer", None)
        if closer is not None:
            self._window_closer = None
            try:
                self.dialog.removeTopWindowListener(closer)
            except Exception:
                pass          # peer déjà parti : rien à retirer
        self.dispatcher.close()
        try:
            self.dialog.setVisible(False)
            self.dialog.dispose()
        except Exception:
            pass
        if _open_palette[0] is self:
            _open_palette[0] = None

    def _emit_session_summary(self):
        """Ce que l'utilisateur a fait de la palette, en UN span de fermeture.

        Émettre à chaque clic coûterait une requête HTTP et un thread par
        geste — la coquille envoie une trace par span, sans lot : on agrège
        ici, et on envoie une fois. La durée de session dit si la palette est
        ouverte le temps d'une demande ou laissée ouverte toute la journée —
        deux usages qui n'appellent pas les mêmes choix d'ergonomie.
        """
        telemetry_steps.emit(self.shell, telemetry_steps.PALETTE_CLOSED, {
            "session.duration_ms": int((time.monotonic() - self._opened_at) * 1000),
            "runs.count": self._session["runs"],
            "tabs.switches": self._session["tabs"],
            "suggestions.views": self._session["suggestions"],
            "reasoning.opens": self._session["reasoning"],
            "conversation.clears": self._session["clears"],
            "append.toggles": self._session["append_toggles"],
        })

    # ── Mises à jour d'affichage ────────────────────────────────────────
    # Ces méthodes sont appelées indifféremment depuis le thread principal et
    # depuis le worker : elles postent systématiquement, ce qui garantit que
    # l'écriture dans les contrôles VCL a bien lieu sur le thread principal.

    def set_status(self, message, tone="neutral", tooltip=None):
        """Ligne de statut, colorée selon l'issue — le retour doit se VOIR.

        `tooltip` alimente l'infobulle native : pendant que le modèle réfléchit,
        survoler le statut affiche le fil de sa réflexion. C'est le seul moyen
        de le montrer sans encombrer une fenêtre déjà dense — et il n'y a rien
        à cliquer, donc rien à découvrir.
        """
        def _apply():
            status = self._models["status"]
            status.Label = message
            status.TextColor = STATUS_COLORS.get(tone, STATUS_COLORS["neutral"])
            if tooltip is not None:
                try:
                    status.HelpText = tooltip
                except Exception:
                    pass          # modèle sans HelpText : sans gravité
        self.dispatcher.post(_apply)

    def set_journal_text(self, text):
        self.dispatcher.post(lambda: self._set_text("journal", text))

    # ── Indicateur de sélection ─────────────────────────────────────────

    def attach_selection_watcher(self):
        """Branche le listener sur le contrôleur courant. Après createPeer."""
        if _XSelectionChangeListener is None:
            return
        try:
            controller = self._current_controller()
            if controller is None:
                return
            watcher = _SelectionWatcher(self)
            controller.addSelectionChangeListener(watcher)
            # Le couple est conservé sur l'instance : sans référence vivante le
            # ramasse-miettes emporterait le listener et les événements
            # cesseraient silencieusement.
            self._selection_watcher = (watcher, controller)
        except Exception as exc:
            self.shell.log(f"[palette] listener de sélection indisponible : {exc}")

    def detach_selection_watcher(self):
        """Retire le listener. À appeler AVANT dispose() — le legacy fait
        l'inverse et ne survit que grâce à un try/except."""
        pair = getattr(self, "_selection_watcher", None)
        if not pair:
            return
        watcher, controller = pair
        self._selection_watcher = None
        try:
            controller.removeSelectionChangeListener(watcher)
        except Exception as exc:
            self.shell.log(f"[palette] retrait du listener : {exc}")

    def _current_controller(self):
        desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.uno_ctx)
        model = desktop.getCurrentComponent()
        return getattr(model, "CurrentController", None) if model else None

    def heal_if_stuck(self):
        """Répare une interface restée en état « occupé » sans run vivant.

        Les mises à jour de fin de run sont postées via AsyncCallback ; si le
        dernier lot n'est pas délivré, la fenêtre reste grisée avec « Arrêter »
        alors que plus rien ne tourne. Ce filet est appelé à chaque activation
        de la fenêtre : le coût est nul, et il transforme un blocage définitif
        en gêne d'une seconde.
        """
        worker = self._worker
        if not self.busy or (worker is not None and worker.is_alive()):
            return
        self.shell.log("[palette] interface bloquée en état occupé — réparation")
        # Chaque déclenchement = un lot de messages asynchrones jamais délivré.
        # Le filet transforme un blocage définitif en gêne d'une seconde, mais
        # sa FRÉQUENCE sur le parc dit si le transport UNO tient la charge.
        telemetry_steps.emit(self.shell, telemetry_steps.UI_HEAL_STUCK)
        self.busy = False
        self._cancel = None
        self._worker = None
        self._stop_pulse()
        self.dispatcher.drain()      # rattraper ce qui n'a pas été délivré
        self._set_input_enabled(True)
        self._set_send_label(running=False)
        self.set_status("Terminé", tone="success")
        self.dispatcher.drain()

    def bring_to_front(self):
        """Remonte la fenêtre au premier plan, au-dessus du document.

        `setFocus` seul ne suffit pas sur une fenêtre autonome : il donne le
        focus clavier sans changer l'ordre d'empilement, et la palette reste
        cachée derrière LibreOffice. `toFront()` (XTopWindow, porté par le peer)
        est ce qui la fait remonter — et il faut être visible d'abord, sinon il
        s'applique à une fenêtre masquée.
        """
        try:
            self.dialog.setVisible(True)
        except Exception:
            return
        try:
            self.dialog.getPeer().toFront()
        except Exception:
            pass          # toolkit sans XTopWindow : on garde au moins le focus
        try:
            self.dialog.setFocus()
        except Exception:
            pass

    def refresh_selection_label(self):
        """Recalcule le libellé de cible ET le titre. Thread principal uniquement."""
        if self.busy:
            return
        try:
            self._models["selection"].Label = self._describe_selection()
        except Exception:
            pass          # contrôle disposé : la palette se ferme, rien à signaler
        self.refresh_title()

    def refresh_title(self):
        """Nomme dans le titre le document sur lequel la demande portera.

        La palette agit sur le document ACTIF au moment de l'envoi, pas sur
        celui qui était ouvert quand on l'a lancée. Avec plusieurs documents et
        une fenêtre désormais autonome, rien ne disait plus lequel — le titre
        le dit maintenant.
        """
        nom = self._current_document_name()
        titre = f"MIrAI — Assistant · {nom}" if nom else "MIrAI — Assistant"
        if titre == self._title_shown:
            return                    # setTitle() fait clignoter la barre
        self._title_shown = titre
        try:
            self.dialog.setTitle(titre)
        except Exception:
            pass

    def _current_document_name(self):
        """Nom court du document courant ; jamais d'exception, jamais None."""
        try:
            desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.uno_ctx)
            model = desktop.getCurrentComponent()
            if model is None:
                return ""
            titre = str(getattr(model, "Title", "") or "")
            if titre:
                return titre
            url = str(getattr(model, "URL", "") or "")
            return url.rsplit("/", 1)[-1] if url else ""
        except Exception:
            return ""

    def current_app(self):
        """Application du document ACTIF, pas celle de l'ouverture.

        `self.app` est figé à la création : une palette ouverte sur Writer
        gardait ce cap même passée sur un Calc, et l'analyse de document comme
        les suggestions se calaient sur la mauvaise application. Les outils
        envoyés au modèle, eux, suivaient déjà le document réel (`ctx.app`).
        """
        try:
            desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.uno_ctx)
            model = desktop.getCurrentComponent()
            if model is None:
                return self.app
            if hasattr(model, "Text"):
                return "writer"
            if hasattr(model, "Sheets"):
                return "calc"
        except Exception:
            pass
        return self.app

    def _describe_selection(self):
        """Décrit la cible courante ; ne rend JAMAIS None ni ne lève."""
        try:
            desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.uno_ctx)
            model = desktop.getCurrentComponent()
            if model is None:
                return ""
            if hasattr(model, "Text"):
                return selection_info.writer_label(*_writer_targets(model))
            if hasattr(model, "Sheets"):
                area = model.CurrentController.Selection.getRangeAddress()
                return selection_info.calc_label(
                    area.StartColumn, area.StartRow, area.EndColumn, area.EndRow)
        except Exception:
            pass
        return ""

    # ── Zone basse à onglets ────────────────────────────────────────────

    @staticmethod
    def _restore_append_mode(shell):
        """Préférence « ajouter à la suite » de la session précédente."""
        try:
            return str(shell.get_config("assistant_append_mode", "")) == "1"
        except Exception:
            return False

    def set_append_mode(self, enabled):
        """Mémorise le choix — il ne doit pas être à refaire à chaque ouverture."""
        self._session["append_toggles"] += 1
        self.append_mode = bool(enabled)
        try:
            self.shell.set_config("assistant_append_mode",
                                  "1" if enabled else "0")
        except Exception:
            pass          # préférence d'affichage : jamais bloquant

    @staticmethod
    def _restore_tab(shell):
        """Onglet actif de la session précédente, « Historique » par défaut."""
        try:
            stored = str(shell.get_config("assistant_active_tab", "") or "")
        except Exception:
            stored = ""
        return stored if stored in dict(TABS) else "response"

    def _on_tab_click(self, tab_id):
        """Bascule demandée par l'UTILISATEUR — la seule qui mesure un usage.

        `select_tab` est aussi appelée par l'ouverture, la restauration et le
        démarrage d'un run : les compter mêlerait le comportement du logiciel
        à celui de la personne qui s'en sert.
        """
        self._session["tabs"] += 1
        if tab_id == "suggestions":
            self._session["suggestions"] += 1
        self.select_tab(tab_id)

    def select_tab(self, tab_id):
        """Bascule la zone basse. L'onglet actif est mémorisé en configuration.

        `reasoning` fait exception : c'est bien un onglet, mais on ne le
        mémorise pas — rouvrir la palette sur le raisonnement d'un run terminé
        depuis longtemps n'apprendrait rien à personne.
        """
        if tab_id not in BOTTOM_PANES:
            return
        self.active_tab = tab_id
        if tab_id == "suggestions":
            self.refresh_suggestions()
        for other, _label in TABS:
            model = self._models.get(f"tab_{other}")
            if model is None:
                continue
            active = other == tab_id
            model.TextColor = (dsfr.TOKENS["primary"] if active
                               else dsfr.TOKENS["text_mention"])
            model.FontWeight = 150.0 if active else 100.0
        if tab_id != REASONING_PANE:
            try:
                self.shell.set_config("assistant_active_tab", tab_id)
            except Exception:
                pass          # préférence d'affichage : jamais bloquant
        self._layout()

    def refresh_suggestions(self):
        """Affiche l'analyse si elle est prête, sinon les règles statiques.

        Les propositions statiques restent le socle : elles s'affichent
        immédiatement, sans réseau. L'analyse du document les remplace quand
        elle arrive — et si elle échoue, on garde le socle plutôt que d'exposer
        un onglet vide.
        """
        if self._analysis_text:
            self._set_text("suggestions", self._analysis_text)
            return
        try:
            base = suggestions.render(self._current_suggestions())
        except Exception as exc:
            self.shell.log(f"[palette] suggestions indisponibles : {exc}")
            base = ""
        self._analysis_base = base
        self._set_text("suggestions", base)
        # L'attente n'est PAS annoncée dans l'onglet : elle s'affiche dans la
        # ligne d'état, là où le run affiche déjà la sienne.
        self.start_document_analysis()

    # ── Analyse du document (asynchrone) ────────────────────────────────

    def _document_text(self):
        """Texte intégral du document Writer. Thread principal uniquement."""
        desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.uno_ctx)
        model = desktop.getCurrentComponent()
        if model is None or not hasattr(model, "Text"):
            return ""
        return model.Text.getString()

    def start_document_analysis(self):
        """Lance l'analyse en tâche de fond. Thread principal uniquement.

        Le texte est lu ICI, avant de partir : la pompe du dispatcher ne tourne
        que pendant un run, donc un `call` depuis le thread d'analyse expirait
        au bout de 10 s (« le thread principal n'a pas répondu »). Même raison
        pour `start_pump()` — il n'est fiable que depuis le thread principal,
        et sans lui le résultat ne serait jamais affiché.

        Rend True si l'analyse a bien démarré, pour que l'appelant sache s'il
        doit annoncer « analyse en cours ».
        """
        if self._analysis_running or self._analysis_text or self.current_app() != "writer":
            return False
        try:
            text = self._document_text()
        except Exception as exc:
            self.shell.log(f"[palette] lecture du document impossible : {exc}")
            return False
        messages = doc_analysis.build_messages(text)
        if messages is None:
            self._analysis_text = doc_analysis.TOO_SHORT
            return False              # rien à analyser : pas d'attente à annoncer
        self._analysis_running = True
        self.dispatcher.start_pump()
        # Même jauge que le run : l'attente s'affiche dans la ligne d'état, au
        # format « ⠹ Analyse du document · 3 s ». Rien d'animé dans l'onglet —
        # deux animations à deux endroits apprendraient deux habitudes.
        progress = RunProgress(activity=doc_analysis.PHASE)
        self._analysis_progress = progress
        self._start_pulse(progress)
        threading.Thread(target=self._analyse_in_worker, args=(messages, progress),
                         daemon=True, name="mirai-doc-analysis").start()
        return True

    def _analyse_in_worker(self, messages, progress):
        """Interroge le modèle et remplace l'onglet.

        Toute sortie anormale laisse les suggestions statiques en place : une
        analyse qui échoue ne doit jamais faire perdre ce qui marchait avant.
        """
        try:
            step = LLMClient(self.shell,
                             max_tokens=doc_analysis.MAX_TOKENS).step(
                                 messages, progress=progress)
            if step.error or not (step.text or "").strip():
                self.shell.log("[palette] analyse du document indisponible : "
                               f"{step.error or 'réponse vide'}")
                return
            items = doc_analysis.parse(
                step.text, truncated=(step.finish_reason == "length"))
            if not items:
                self.shell.log("[palette] analyse du document : aucune "
                               "proposition exploitable")
                return
            self.shell.log(f"[palette] analyse du document : {len(items)} proposition(s)")
            self._analysis_text = doc_analysis.render(items)
        except DispatcherClosed:
            pass                      # palette fermée pendant l'analyse
        except Exception as exc:
            self.shell.log(f"[palette] analyse du document échouée : {exc}")
        finally:
            self._analysis_running = False
            self._publish_analysis(progress)

    def _publish_analysis(self, progress=None):
        """Affiche le résultat — ou rend la main au socle statique en cas d'échec."""
        final = self._analysis_text or self._analysis_base
        # La jauge n'est arrêtée que si elle est encore la NÔTRE : un run
        # démarré entre-temps a posé la sienne, et la couper afficherait un run
        # figé alors qu'il travaille.
        if progress is None or self._progress is progress:
            self._stop_pulse()
            self._analysis_progress = None
            self.set_status("Prêt", tone="neutral")
        try:
            self.dispatcher.post(lambda: self._set_text("suggestions", final))
            # La pompe a été armée pour cette analyse : on l'éteint, sauf si un
            # run l'utilise encore — il la gère alors pour son propre compte.
            self.dispatcher.post(self._stop_pump_if_idle)
        except DispatcherClosed:
            pass

    def _stop_pump_if_idle(self):
        if not self.busy:
            self.dispatcher.stop_pump()

    def invalidate_analysis(self):
        """Le document a changé : l'analyse précédente ne le décrit plus."""
        self._analysis_text = ""

    def _current_suggestions(self):
        """Décrit la situation au moteur de suggestions (aucun appel LLM)."""
        desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.uno_ctx)
        model = desktop.getCurrentComponent()
        if model is None:
            return suggestions.suggest(self.current_app())
        if hasattr(model, "Text"):
            selected, paragraph = _writer_targets(model)
            return suggestions.suggest(
                "writer", selected_text=selected, has_paragraph=bool(paragraph.strip()))
        if hasattr(model, "Sheets"):
            count, values = _calc_selection_sample(model)
            return suggestions.suggest("calc", cell_count=count, values=values)
        return suggestions.suggest(self.current_app())

    # ── Fil de conversation (main courante : le plus récent EN HAUT) ────

    def journal_line(self, text, step="", detail="", **attributes):
        """Ajoute une ligne au journal d'actions (onglet « Actions »).

        Trois destinations, trois publics :

        - l'**onglet Actions**, pour l'utilisateur, en français : `text`, suivi
          de ` — {detail}` quand `detail` (extrait du document ou du modèle)
          n'est pas vide ;
        - **le journal de l'extension** (`<profil>/user/config/mirai/mirai.log`),
          pour le diagnostic après coup : `text` et la seule longueur de
          `detail`, le fichier ne porte jamais de texte du document ;
        - la **télémétrie**, pour l'exploitation — mais uniquement si l'appelant
          nomme une étape (`step=`), et jamais le texte français : il cite le
          document, et la télémétrie quitte le poste (cf. `telemetry_steps`).
        """
        shown = f"{text} — {detail}" if detail else text
        self._journal_lines.append(shown)
        logged = f"{text} ({len(detail)} car.)" if detail else text
        self.shell.log(f"[journal] {logged}")
        if step:
            telemetry_steps.emit(self.shell, step, attributes)
        joined = "\n".join(self._journal_lines)
        self.dispatcher.post(lambda: self._set_text("journal", joined))

    def _pick_from_pane(self, name, offset):
        """Reporte la ligne cliquée dans la zone de saisie.

        On ne lance RIEN : l'utilisateur relit, ajuste, puis envoie. Un clic
        qui déclencherait une action sur le document serait irrattrapable dans
        une zone où l'on clique aussi pour lire.
        """
        if self.busy:
            return
        try:
            texte = self._models[name].Text or ""
        except Exception:
            return
        propos = clickable.payload_at(texte, offset)
        # Une ligne par clic. Sans elle, un contrôle en lecture seule qui ne
        # rendrait pas de position de curseur donnerait un clic sans effet ET
        # sans trace — la panne muette qu'on a passé la journée à traquer.
        self.shell.log(f"[palette] clic {name} offset={offset} "
                       f"ligne={clickable.line_at(texte, offset)} "
                       f"repris={len(propos)}c")
        if not propos:
            return                    # ligne vide, titre de section, statut
        try:
            self._models["prompt"].Text = propos
            self.dialog.getControl("prompt").setFocus()
        except Exception:
            return

    def toggle_reasoning(self):
        """Raccourci « ⓘ » vers l'onglet Raisonnement, et retour.

        L'onglet reste accessible normalement dans la barre ; le « ⓘ » n'est
        qu'un chemin court depuis la ligne de statut, qui ramène d'un second
        clic à l'onglet d'où l'on vient.
        """
        if self.active_tab == REASONING_PANE:
            self.select_tab(self._tab_before_reasoning or "response")
            return
        self._session["reasoning"] += 1
        self._tab_before_reasoning = self.active_tab
        self.select_tab(REASONING_PANE)

    def set_reasoning(self, text):
        """Alimente l'onglet et fait apparaître le « ⓘ » s'il y a à voir.

        Onglet vide = onglet suspect : sans texte de repli, l'utilisateur qui
        clique sur « Raisonnement » avant tout run voit un rectangle blanc et
        croit à une panne.
        """
        self._set_text(REASONING_PANE, text or REASONING_EMPTY)
        label = "ⓘ" if text else ""
        model = self._models.get("reasoning_toggle")
        if model is not None:
            try:
                model.Label = label
            except Exception:
                pass

    def reload_history(self):
        """Force la relecture du fil persisté au prochain rendu."""
        self._history_cache = None
        self._render_conversation()

    def _render_conversation(self):
        """Recompose le fil : échange en cours d'abord, puis l'historique.

        Ordre antichronologique — on lit une main courante par le haut, sans
        avoir à faire défiler pour voir ce qui vient d'arriver.
        """
        blocks = []
        if self._current_exchange:
            blocks.append("\n".join(self._current_exchange))

        # L'historique est mis en CACHE : cette méthode est rappelée à chaque
        # fragment du flux (~8 fois par seconde). Relire le fichier JSON à
        # chaque fois, c'est une entrée-sortie disque dans le worker et une
        # grande chaîne postée au thread principal — de quoi saturer sa file
        # d'événements et donner l'impression d'une interface figée.
        if self._history_cache is None:
            self._history_cache = self.conversation.load()
        entries = self._history_cache
        # Les entrées arrivent dans l'ordre chronologique, par paires
        # (utilisateur, assistant) : on regroupe puis on inverse les groupes,
        # sans inverser l'intérieur d'un échange — une réponse au-dessus de sa
        # question serait illisible.
        exchange, grouped = [], []
        for entry in entries:
            if entry["role"] == "user" and exchange:
                grouped.append(exchange)
                exchange = []
            prefix = "Vous : " if entry["role"] == "user" else "MIrAI : "
            exchange.append(prefix + entry["text"])
        if exchange:
            grouped.append(exchange)

        for group in reversed(grouped):
            blocks.append("\n".join(group))

        text = "\n———\n".join(blocks)

        self.dispatcher.post(lambda: self._set_text("response", text))

    def _set_text(self, name, text):
        """Écrit dans un contrôle texte ET force son rafraîchissement.

        Écrire `model.Text` met bien la donnée — une relecture le confirme —
        mais un `UnoControlEdit` déjà doté d'un peer ne repeint pas toujours
        pour autant : la zone reste vide à l'écran alors qu'elle contient le
        texte. C'est `setText()` sur le CONTRÔLE qui met à jour l'affichage.
        On fait les deux : le modèle porte l'état, le contrôle l'affiche.
        """
        model = self._models.get(name)
        if model is not None:
            try:
                model.Text = text
            except Exception:
                pass
        try:
            control = self.dialog.getControl(name)
            if control is not None:
                control.setText(text)
        except Exception:
            pass          # contrôle sans setText (FixedText) ou déjà disposé

    def _append_response(self, prefix, text=""):
        """Ajoute une ligne à l'échange EN COURS, affiché en tête du fil."""
        self._current_exchange.append((prefix + text) if text else prefix)
        self._render_conversation()

    def _stream_response(self, chunk):
        """Entrée du flux : on accumule, le tampon décide quand publier."""
        self._delta_buffer.add(chunk)

    def _flush_deltas(self, text):
        """Le flux alimente la DERNIÈRE ligne de l'échange en cours."""
        if not self._current_exchange:
            self._current_exchange.append("MIrAI : ")
        self._current_exchange[-1] += text
        self._render_conversation()

    def _on_clear(self):
        if self.busy:
            return
        self._session["clears"] += 1
        self.conversation.clear()
        self._current_exchange = []
        self._history_cache = None
        self._models["response"].Text = ""
        self.set_journal_text("")
        self.set_status("Conversation effacée.")

    # ── Exécution ───────────────────────────────────────────────────────
    def _current_context(self):
        """Résout le document courant. À appeler depuis le thread principal.

        Le document est re-résolu à chaque run plutôt que mémorisé à
        l'ouverture : l'utilisateur peut avoir changé d'onglet entre-temps.
        Le contexte porte le dispatcher — c'est par lui que les tools et les
        sinks remonteront sur le thread principal depuis le worker.
        """
        desktop = self.uno_ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.uno_ctx)
        model = desktop.getCurrentComponent()
        if model is None:
            return None
        if hasattr(model, "Text"):
            app = "writer"
        elif hasattr(model, "Sheets"):
            app = "calc"
        else:
            return None
        return ToolContext(self.uno_ctx, model, model.CurrentController,
                           app, self.shell, dispatcher=self.dispatcher)

    def _prompt_text(self):
        try:
            return str(self._models["prompt"].Text or "")
        except Exception:
            return ""

    def _defer(self, fn):
        """Planifie fn hors du dispatch d'événement courant (voir _DeferredCall)."""
        try:
            async_callback = self.uno_ctx.getServiceManager() \
                .createInstanceWithContext("com.sun.star.awt.AsyncCallback",
                                           self.uno_ctx)
            deferred = _DeferredCall(fn)
            self._handlers.append(deferred)   # référence vivante jusqu'au notify
            async_callback.addCallback(deferred, None)
        except Exception:
            fn()   # repli : exécution directe (mieux que rien)

    def _on_chip(self, preset):
        if self.busy:
            return
        self._defer(lambda: self._start_run(preset=preset))

    def _on_send(self):
        """Envoyer, ou Arrêter si un run est déjà en cours."""
        if self.busy:
            self._cancel_run()
            return
        self._defer(lambda: self._start_run(preset=None))

    def _cancel_run(self):
        """Demande l'arrêt du run en cours. Le worker s'arrête entre deux chunks."""
        if self._cancel is not None:
            self._cancel.set()
            self.set_status("Arrêt en cours…")

    def _start_run(self, preset=None):
        """Valide la demande sur le thread principal, puis lance le worker.

        Les contrôles préalables (prompt vide, type de document) lisent l'UI et
        le document : ils doivent rester ici. Dès que la demande est valide, la
        main est rendue à LibreOffice et tout le travail part dans le worker.
        """
        if self.busy:
            return
        prompt_text = self._prompt_text().strip()
        if preset is None and not prompt_text:
            return self._refuse("empty_prompt", "Tapez d'abord votre demande.")
        if preset is not None and preset.needs_input and not prompt_text:
            return self._refuse(
                "preset_needs_input",
                preset.input_hint or "Précisez votre demande.", preset)

        ctx = self._current_context()
        if ctx is None:
            return self._refuse("no_document",
                                "Ouvrez un document Writer ou Calc.", preset)
        if preset is not None and ctx.app not in preset.apps:
            wanted = "Writer" if "writer" in preset.apps else "Calc"
            return self._refuse(
                "wrong_app", f"Cette action nécessite un document {wanted}.",
                preset)

        self._last_refusal = ""
        self.busy = True
        self._cancel = threading.Event()
        self._delta_buffer.reset()
        self._current_exchange = []    # nouvel échange : le précédent est persisté
        self._history_cache = None     # le tour précédent a rejoint l'historique
        self._journal_lines = []
        self._progress = RunProgress()
        self._set_input_enabled(False)
        self._start_pulse()
        self._set_send_label(running=True)
        # La réponse arrive dans l'onglet Conversation : si l'utilisateur
        # regarde ailleurs, il ne verrait RIEN se produire. On bascule pour lui.
        if self.active_tab != "response":
            self.select_tab("response")
        self.set_status("L'assistant travaille…")
        shown = prompt_text if preset is None else (
            preset.label + ((" — " + prompt_text) if prompt_text else ""))
        self._append_response("Vous : ", shown)

        # Instantané pris ICI, sur le thread principal. Un `call()` depuis le
        # worker dépend d'AsyncCallback, qui n'est délivré qu'au prochain
        # réveil de la boucle d'événements : LibreOffice au repos, l'attente
        # peut dépasser le délai et le run échoue sur « LibreOffice était
        # occupé ». Tout ce qui est lisible d'avance l'est donc maintenant.
        snapshot = self._document_snapshot(ctx, preset, prompt_text)

        # La pompe doit être armée depuis le thread principal — c'est ici, et
        # nulle part ailleurs, que ce démarrage est fiable.
        self.dispatcher.start_pump()

        self._worker = threading.Thread(
            target=self._run_in_worker,
            args=(preset, prompt_text, ctx, shown, snapshot),
            daemon=True, name="mirai-run")
        self._worker.start()

    def _refuse(self, reason, message, preset=None):
        """Refuse le lancement en le DISANT — à l'utilisateur et au parc.

        Ces refus étaient invisibles : un preset systématiquement lancé sans
        sélection, ou un raccourci utilisé dans la mauvaise application, ne
        laissaient qu'un statut rouge que personne ne remonte. La télémétrie
        ne part pas par `journal_line` : le journal est vidé au démarrage du
        run suivant, et un refus n'est pas une étape de run.
        """
        self.set_status(message, tone="error")
        # Entrée martelée sur un prompt vide : un span, pas une rafale.
        if self._last_refusal != reason:
            self._last_refusal = reason
            attributes = {"refuse.reason": reason}
            if preset is not None:
                attributes["preset.name"] = preset.id
            telemetry_steps.emit(self.shell, telemetry_steps.RUN_REFUSED,
                                 attributes)

    def _model_can_chain_tools(self):
        """Le modèle sait-il enchaîner lecture → écriture ?

        Verdict MESURÉ par le menu « Tester le modèle », mis en cache par couple
        (endpoint, modèle). En l'absence de mesure on répond NON : le chemin
        déterministe aboutit toujours, là où le mode agentique peut laisser le
        document intact sans que rien ne le signale. Mieux vaut un défaut
        prudent qu'une action silencieusement sans effet.
        """
        try:
            verdict = capabilities.load_cached(
                self.shell,
                self.shell.get_config("llm_base_urls", ""),
                self.shell.get_config("llm_default_models", ""))
        except Exception:
            return False
        return bool(verdict and verdict.supports_agentic)

    def _document_sink(self, ctx):
        """Destination du texte : remplacer la sélection, ou l'ajouter après.

        Le choix appartient à l'utilisateur (case « Ajouter à la suite ») : les
        deux usages sont légitimes — remplacer va plus vite, ajouter permet de
        comparer avant de décider. Les marqueurs reprennent la forme historique.
        """
        if self.append_mode:
            return WriterInsertSink(
                ctx,
                "\n\n---début-du-texte-modifié---\n",
                "\n---fin-du-texte-modifié---\n")
        return WriterReplaceSink(ctx)

    def _document_snapshot(self, ctx, preset, prompt_text):
        """Lit d'avance ce dont le run aura besoin. Thread principal uniquement."""
        snapshot = {"selection": "", "paragraphs": [], "styles": [],
                    "rewrite": False, "rewrite_selection": False}
        try:
            snapshot["selection"] = _selection_string(ctx) or ""
        except Exception:
            pass
        wants_edit = (preset is None and ctx.app == "writer"
                      and doc_rewrite.wants_document_rewrite(prompt_text)
                      and not self._model_can_chain_tools())
        if wants_edit and snapshot["selection"].strip():
            # Une demande de modification AVEC sélection porte sur elle. Sans
            # ce chemin, le prompt libre partait en mode agentique dont le sink
            # est la palette : le texte s'affichait dans la fenêtre et le
            # document restait inchangé.
            snapshot["rewrite_selection"] = True
            return snapshot
        if wants_edit and not snapshot["selection"].strip():
            try:
                from ..core.tools.writer_tools import _paragraphs, paragraph_style
                items = _paragraphs(ctx)
                snapshot["paragraphs"] = [p.getString() for p in items]
                snapshot["styles"] = [paragraph_style(p) for p in items]
                snapshot["rewrite"] = True
            except Exception as exc:
                self.shell.log(f"[palette] lecture du document impossible : {exc}")
        return snapshot

    def _run_in_worker(self, preset, prompt_text, ctx, shown, snapshot=None):
        """Corps du run — s'exécute HORS du thread principal.

        Aucun accès direct à l'UI ni au document ici : tout passe par
        `self.dispatcher` (post pour l'affichage, call pour le document).
        """
        snapshot = snapshot or {}
        started = time.monotonic()
        self._session["runs"] += 1
        self.shell.log("[palette] run: début")
        # Chaque branche rend un résumé {ok, reason, …} : c'est lui qui alimente
        # le span de fin. Les valeurs par défaut couvrent le cas où la branche
        # lève avant d'avoir rendu quoi que ce soit.
        kind, summary = "agentic", {"ok": False, "reason": "exception"}
        try:
            if preset is not None and preset.mode == "pipeline":
                kind = "pipeline"
                summary = self._run_pipeline(preset, prompt_text, ctx, shown)
            elif snapshot.get("rewrite_selection"):
                kind = "selection_rewrite"
                summary = self._run_selection_rewrite(ctx, prompt_text,
                                                      snapshot["selection"])
            elif snapshot.get("rewrite"):
                kind = "document_rewrite"
                summary = self._run_document_rewrite(ctx, prompt_text,
                                                     snapshot["paragraphs"],
                                                     snapshot["styles"])
            else:
                summary = self._run_agentic(preset, prompt_text, ctx)
            self._delta_buffer.flush()
            self._finish_run()
        except DispatcherClosed:
            summary = {"ok": False, "reason": "palette_closed"}
            self.shell.log("[palette] run interrompu : palette fermée")
        except Exception as exc:
            summary = {"ok": False, "reason": "exception"}
            self.shell.log(f"[palette] run error: {exc}")
            self._delta_buffer.flush()
            self.set_status(_friendly_error(exc), tone="error")
            self._append_response("MIrAI : ", f"⚠ {_friendly_error(exc)}")
        finally:
            # Le span AVANT de libérer l'état : `_cancel` est remis à None juste
            # après, et l'annulation ne serait plus lisible.
            self._emit_run_span(kind, preset, summary, started)
            # Ordre important : libérer l'état AVANT de poster les mises à
            # jour, pour qu'un filet déclenché entre-temps voie un run terminé.
            self.busy = False
            self._cancel = None
            self._worker = None
            self._stop_pulse()
            self._close_reasoning()
            self._set_input_enabled(True)
            self._set_send_label(running=False)
            # Dernier drain, puis extinction : la pompe ne doit pas tourner
            # au repos.
            self.dispatcher.post(self.dispatcher.stop_pump)
            self.shell.log("[palette] run: terminé, interface restaurée")

    def _emit_run_span(self, kind, preset, summary, started):
        """Un run = un span, quel que soit le chemin emprunté.

        Le mode agentique était seul instrumenté : un preset, une réécriture de
        sélection ou de document ne laissaient aucune trace de durée ni
        d'issue. `assistant.reason` nomme la cause de fin — sans elle, une
        annulation, un plafond d'itérations et un 429 se ressemblent tous.
        """
        summary = summary or {}
        attributes = {
            "run.kind": kind,
            "assistant.preset": preset.id if preset is not None else "free",
            "assistant.ok": bool(summary.get("ok")),
            "assistant.cancelled": self._cancelled(),
            "append.mode": bool(self.append_mode),
            "assistant.duration_ms": int((time.monotonic() - started) * 1000),
        }
        if summary.get("reason"):
            attributes["assistant.reason"] = str(summary["reason"])
        if summary.get("mode"):
            attributes["assistant.mode"] = str(summary["mode"])
        if summary.get("iterations") is not None:
            attributes["assistant.iterations"] = int(summary["iterations"])
        telemetry_steps.emit_run(self.shell, attributes)

    def _run_pipeline(self, preset, prompt_text, ctx, shown):
        """Preset piloté par Python : le LLM n'est qu'une fonction texte.

        Le runner touche le document ; il le fait via ctx.on_main. Son appel LLM
        reste dans ce worker.
        """
        self.journal_line(f"⚙ {preset.label} — préparation",
                          step=telemetry_steps.PRESET_START,
                          **{"preset.name": preset.id})
        message = preset.runner(ctx, self.shell, prompt_text, None,
                                cancel_event=self._cancel,
                                dispatcher=self.dispatcher,
                                append_mode=self.append_mode)
        # Le texte du message vient du modèle : l'onglet Actions le montre, le
        # fichier n'en garde que la longueur et la télémétrie aussi.
        self.journal_line(f"✓ {preset.label}", detail=message[:70],
                          step=telemetry_steps.PRESET_DONE,
                          **{"preset.name": preset.id,
                             "result.chars": len(message or "")})
        self._append_response("MIrAI : ", message)
        self.conversation.append("user", shown, ctx.app)
        self.conversation.append("assistant", message, ctx.app)
        return {"ok": True}

    def _run_agentic(self, preset, prompt_text, ctx):
        """Run piloté par le LLM, qui appelle les outils du registre.

        Exception : une demande de réécriture du document entier emprunte un
        chemin DÉTERMINISTE (voir `_run_document_rewrite`). Les modèles de
        taille moyenne lisent le document puis répondent du texte sans jamais
        appeler l'outil d'écriture ; on cesse donc d'en dépendre.
        """

        orchestrator = Orchestrator(
            LLMClient(self.shell), self.registry, ctx,
            observer=_JournalObserver(self),
            conversation=self.conversation,
            cancel_event=self._cancel,
            dispatcher=self.dispatcher,
            progress=self._progress)

        extra, user_prompt, sink = self._prepare_agentic_run(preset, prompt_text, ctx)
        self._append_response("MIrAI : ")
        result = orchestrator.run_agentic(user_prompt, sink, preset_extra=extra)
        self._delta_buffer.flush()
        if not result.ok:
            self._stream_response("⚠ " + (result.text or result.reason))
        elif not isinstance(sink, PaletteSink):
            self._stream_response(result.text or "Modification appliquée.")
        return {"ok": result.ok, "reason": result.reason,
                "mode": orchestrator.llm.effective_mode(),
                "iterations": result.iterations}

    def _run_selection_rewrite(self, ctx, instruction, selection):
        """Applique une demande libre À LA SÉLECTION, par un chemin sûr.

        Même principe que la réécriture du document : Python tient le texte, le
        LLM n'est qu'une fonction texte, et le résultat est écrit par le sink
        choisi dans la palette — remplacement, ou ajout entre marqueurs si la
        case « Ajouter à la suite » est cochée.
        """
        from ..core.presets import text_sink

        self.journal_line(f"⚙ Modification de la sélection ({len(selection)} car.)",
                          step=telemetry_steps.SELECTION_START,
                          **{"selection.chars": len(selection),
                             "append.mode": bool(self.append_mode)})
        self._append_response("MIrAI : ")
        sink = self.dispatcher.call(
            lambda: text_sink(ctx, self.append_mode,
                              "\n\n---début-du-texte-modifié---\n",
                              "\n---fin-du-texte-modifié---\n"),
            timeout=10)

        llm = LLMClient(self.shell)
        self.dispatcher.call(lambda: ctx.undo_begin("Modifier la sélection"),
                             timeout=10)
        try:
            step = llm.step(
                [{"role": "system", "content": prompts.LEGACY_TEXT_SYSTEM},
                 {"role": "user",
                  "content": (f"TEXTE :\n{selection}\n\n"
                              f"DEMANDE : {instruction}\n\n"
                              "Réponds UNIQUEMENT avec le texte modifié, sans "
                              "introduction ni commentaire.")}],
                on_text_delta=self._stream_response,
                cancel_event=self._cancel,
                progress=self._progress)
            self._delta_buffer.flush()
            if step.error:
                self._stream_response("⚠ " + error_message(step.error))
                return {"ok": False, "reason": step.error}
            if self._cancelled():
                return {"ok": False, "reason": "cancelled"}
            self.dispatcher.call(
                lambda: sink.finish(step.text, step.streamed), timeout=30)
        finally:
            self.dispatcher.call(ctx.undo_end, timeout=10)

        how = "ajouté après la sélection" if self.append_mode else "remplacée"
        self.journal_line(f"✓ Sélection {how}",
                          step=telemetry_steps.SELECTION_DONE,
                          **{"append.mode": bool(self.append_mode)})
        summary = f"Sélection {how}. Ctrl+Z pour annuler."
        self.conversation.append("user", instruction, ctx.app)
        self.conversation.append("assistant", summary, ctx.app)
        return {"ok": True}

    def _run_document_rewrite(self, ctx, instruction, originals, styles=None):
        """Réécrit le document : Python lit, le LLM rédige, Python applique.

        Aucun tool call n'est demandé au modèle — c'est ce qui rend l'opération
        fiable là où le mode agentique échouait silencieusement.
        """
        from ..core.tools.writer_tools import replace_paragraphs

        if not originals:
            self._append_response("MIrAI : ", "Le document est vide.")
            return {"ok": False, "reason": "empty_document"}

        # Les titres restent en place : les inclure dans la plage réécrite y
        # ferait tomber du corps de texte, qui hériterait du style Titre.
        styles = styles or [""] * len(originals)
        span = doc_rewrite.body_range(styles)
        if span is None:
            self._append_response(
                "MIrAI : ", "Ce document ne contient que des titres.")
            return {"ok": False, "reason": "headings_only"}
        first, last = span
        body = originals[first - 1:last]
        headings = [text for text, style in zip(originals, styles, strict=False)
                    if doc_rewrite.is_heading(style) and text.strip()]

        self.journal_line(f"✓ Lecture du document — {len(originals)} paragraphe(s)",
                          step=telemetry_steps.DOCUMENT_READ,
                          **{"document.paragraphs": len(originals),
                             "document.headings": len(headings)})
        if headings:
            self.journal_line("↳ Titre conservé", detail=f"« {headings[0][:50]} »")
        self.journal_line(f"⚙ Réécriture des paragraphes {first} à {last}",
                          step=telemetry_steps.DOCUMENT_START,
                          **{"body.paragraphs": len(body),
                             "append.mode": bool(self.append_mode)})
        self._progress.set_phase("Rédaction")
        self._append_response("MIrAI : ")
        llm = LLMClient(self.shell)
        step = llm.step(
            [{"role": "system", "content": prompts.LEGACY_TEXT_SYSTEM},
             {"role": "user",
              "content": doc_rewrite.build_rewrite_prompt(
                  body, instruction, headings=headings)}],
            on_text_delta=self._stream_response,
            cancel_event=self._cancel,
            progress=self._progress)
        self._delta_buffer.flush()

        if step.error:
            self._stream_response("⚠ " + error_message(step.error))
            return {"ok": False, "reason": step.error}
        if self._cancelled():
            return {"ok": False, "reason": "cancelled"}

        rewritten = doc_rewrite.parse_rewritten(step.text)
        if not rewritten:
            # Dire CE QUI s'est passé : « inexploitable » n'oriente vers aucune
            # action. Si le modèle a consommé son budget en raisonnement sans
            # rien répondre — et que la reprise élargie du client n'a pas suffi —
            # le remède est un autre modèle, pas un autre prompt.
            if getattr(step, "starved_by_reasoning", False):
                self.journal_line(
                    "✗ Budget épuisé par le raisonnement",
                    step=telemetry_steps.REASONING_STARVED,
                    **{"reasoning.chars": int(step.reasoning_chars),
                       "finish.reason": "length"})
                self._stream_response(
                    "\n⚠ Ce modèle a consacré tout son budget à réfléchir sans "
                    "produire de réponse. Le document n'a pas été modifié. "
                    "Essayez un modèle qui raisonne moins (Paramètres), ou une "
                    "demande portant sur une partie du document.")
                return {"ok": False, "reason": "reasoning_starved"}
            else:
                self.journal_line(
                    "✗ Réponse inexploitable",
                    step=telemetry_steps.DOCUMENT_EMPTY,
                    **{"reply.chars": len(step.text or ""),
                       "finish.reason": (step.finish_reason or "unknown")})
                self._stream_response(
                    "\n⚠ Réponse inexploitable — le document n'a pas été modifié.")
            return {"ok": False, "reason": "empty_reply"}

        self._progress.set_phase("Application au document")

        def _apply():
            if self.append_mode:
                # L'original est conservé : le résultat s'ajoute après le
                # dernier paragraphe traité, encadré par des marqueurs.
                body_text = "\n".join(rewritten)
                replace_paragraphs(ctx, {
                    "start": last, "end": last,
                    "text": "\n".join([originals[last - 1],
                                       "---début-du-texte-réécrit---",
                                       body_text,
                                       "---fin-du-texte-réécrit---"])})
            else:
                replace_paragraphs(ctx, {"start": first, "end": last,
                                         "text": "\n".join(rewritten)})
            ctx.undo_end()

        # `post` et non `call` : on n'a pas besoin du résultat, et attendre
        # exposerait au délai d'AsyncCallback décrit plus haut.
        self.dispatcher.post(_apply)
        self.journal_line(
            f"✓ Écriture appliquée — {len(body)} → {len(rewritten)} paragraphe(s)",
            step=telemetry_steps.DOCUMENT_DONE,
            **{"body.paragraphs": len(body),
               "result.paragraphs": len(rewritten),
               "headings.kept": len(headings),
               "append.mode": bool(self.append_mode)})
        kept = " (titre conservé)" if headings else ""
        how = "ajouté à la suite" if self.append_mode else "réécrit"
        summary = (f"Document {how} : {len(body)} → {len(rewritten)} "
                   f"paragraphe(s){kept}. Ctrl+Z pour annuler.")
        self._stream_response("\n" + summary)
        self.conversation.append("user", instruction, ctx.app)
        self.conversation.append("assistant", summary, ctx.app)
        return {"ok": True}

    def _prepare_agentic_run(self, preset, prompt_text, ctx):
        """Résout prompt, contexte supplémentaire et destination de la sortie."""
        extra = ""
        user_prompt = prompt_text
        sink = None
        if preset is not None:
            if preset.build_extra:
                extra = preset.build_extra(ctx, self.shell, prompt_text)
            if preset.prompt_template:
                user_prompt = preset.prompt_template(prompt_text)
            if preset.sink_spec == "auto_edit":
                # Lecture du document → thread principal obligatoire.
                selection = self.dispatcher.call(
                    lambda: _selection_string(ctx), timeout=10)
                if selection.strip():
                    sink = self._document_sink(ctx)
        if sink is None:
            sink = PaletteSink(on_delta=self._stream_response)
        return extra, user_prompt, sink

    def _finish_run(self):
        """Statut de fin : arrêté, ou terminé avec le champ de prompt vidé."""
        # Un run touche presque toujours au document : l'analyse précédente
        # décrit alors un état révolu. La suivante sera relancée à la prochaine
        # visite de l'onglet, plutôt que d'afficher un constat périmé.
        self.invalidate_analysis()
        if self._cancelled():
            self.set_status("Arrêté.", tone="neutral")
            return
        self.dispatcher.post(
            lambda: self._models["prompt"].__setattr__("Text", ""))
        self.set_status("Terminé", tone="success")

    def _set_input_enabled(self, enabled):
        """Grise le champ de saisie et les chips pendant un run.

        Un champ qui reste actif pendant le travail invite à retaper une
        demande qui sera refusée par le drapeau `busy` — sans que rien ne
        l'explique. Le griser dit la même chose, visuellement.
        """
        def _apply():
            for name in ("prompt", *self._chip_names):
                model = self._models.get(name)
                if model is None:
                    continue
                try:
                    model.Enabled = enabled
                except Exception:
                    pass          # certains modèles n'exposent pas Enabled
            prompt = self._models.get("prompt")
            if prompt is not None:
                try:
                    prompt.BackgroundColor = (dsfr.TOKENS["bg_contrast"] if enabled
                                              else dsfr.TOKENS["bg_alt"])
                except Exception:
                    pass
        self.dispatcher.post(_apply)

    def _start_pulse(self, progress=None):
        """Anime la ligne d'état tant qu'une opération longue dure.

        Sert le run ET l'analyse du document : même emplacement, même format
        (« ⠹ Analyse du document · 3 s »). Une seule mécanique à maintenir, et
        surtout un seul endroit où l'utilisateur apprend à regarder.
        """
        self._stop_pulse()
        if progress is not None:
            self._progress = progress
        stop = threading.Event()

        def _tick():
            while not stop.wait(PULSE_INTERVAL_S):
                progress = self._progress
                if progress is None:
                    return
                self.set_reasoning(progress.tooltip)
                self.set_status(progress.render(), tone="neutral")

        thread = threading.Thread(target=_tick, daemon=True, name="mirai-pulse")
        self._pulse = (thread, stop)
        thread.start()

    def _close_reasoning(self):
        """Fin de run : on quitte le panneau et le « ⓘ » s'efface.

        Le contenu, lui, est conservé : l'utilisateur peut vouloir relire ce que
        le modèle a fait juste après coup.
        """
        def _apply():
            if self.active_tab == REASONING_PANE:
                self.select_tab(self._tab_before_reasoning or "response")
            model = self._models.get("reasoning_toggle")
            if model is not None:
                model.Label = ""
        self.dispatcher.post(_apply)

    def _stop_pulse(self):
        pulse = self._pulse
        self._pulse = None
        if pulse is not None:
            pulse[1].set()

    def _set_send_label(self, running):
        """Bascule Envoyer ⇄ Arrêter.

        Toujours par le dispatcher, même depuis le thread principal : mélanger
        écriture directe et écriture postée fait diverger l'affichage de l'état
        réel — le bouton restait sur « Arrêter » après la fin du run.
        """
        label = "Arrêter" if running else "Envoyer  ⏎"
        self.dispatcher.post(
            lambda: self._models["send"].__setattr__("Label", label))

    def _cancelled(self):
        return self._cancel is not None and self._cancel.is_set()


def open_or_focus(uno_ctx, shell, app, callbacks):
    """Ouvre la palette (ou la ramène au premier plan si déjà ouverte).

    Ce chemin sert le menu « 🤖 MIrAI », le bouton de la barre d'outils et le
    raccourci : tous trois passent par l'action `OpenAssistant`. C'est donc ici,
    et nulle part ailleurs, que se règle « ramène-moi la fenêtre » — devenu
    indispensable depuis qu'elle est autonome et peut passer derrière.
    """
    existing = _open_palette[0]
    if existing is not None:
        try:
            existing.bring_to_front()
            # Rouvrir une palette DÉJÀ ouverte n'est pas une ouverture : le
            # confondre avec elle gonflerait AssistantOpen d'un geste qui dit
            # surtout que la fenêtre s'était perdue derrière le document.
            telemetry_steps.emit(shell, telemetry_steps.PALETTE_REFOCUSED)
            return existing
        except Exception:
            _open_palette[0] = None
    palette = AssistantPalette(uno_ctx, shell, app, callbacks)
    _open_palette[0] = palette
    palette.show()
    return palette
