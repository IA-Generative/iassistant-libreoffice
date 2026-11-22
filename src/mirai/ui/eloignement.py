"""Écrans Éloignement : nouvel acte, recherche de dossier, volet du document lié.

- E1 « Nouvel acte depuis Éloignement » : catalogue des modèles, recherche.
- E2 « Rechercher un dossier » : un seul champ, résultats, en-tête du dossier.
- E3 volet « Éloignement » du document lié (fenêtre non modale posée sur la
  fenêtre du document) : dossier, personne, modèle, version, état de
  l'enregistrement, balises à compléter, actions.
- E2b « Options de génération » : ce que le modèle exige, décrit par Éloignement.
- E4 conflit de version, E5 actualisation depuis le dossier.

Libellés en français, ceux de la conception de la messagerie (« Rechercher un
dossier », « Valider »). Tout le réseau part dans un thread de travail (tools
du moteur) ; ce qui touche UNO revient par le dispatcher de la session.
"""

import threading

import uno
import unohelper

from ..core.context import ToolContext
from ..core.eloignement import fill
from ..core.eloignement import link as link_mod
from ..core.eloignement import options as options_mod
from ..core.eloignement.session import DISABLED, get_session
from ..core.eloignement.tools import registry as tools_registry
from ..core.tool_calls import ToolResult
from . import dsfr

try:
    from com.sun.star.awt import XItemListener as _XItemListener
    from com.sun.star.awt import XKeyListener as _XKeyListener
    from com.sun.star.awt import XTopWindowListener as _XTopWindowListener
except ImportError:  # hors LibreOffice
    _XItemListener = _XKeyListener = _XTopWindowListener = None

try:
    from com.sun.star.awt.Key import RETURN as KEY_RETURN
except Exception:
    KEY_RETURN = 1280

try:
    from com.sun.star.beans import PropertyValue
except Exception:
    PropertyValue = None

try:
    from com.sun.star.awt.MessageBoxType import INFOBOX, QUERYBOX
except Exception:
    INFOBOX = QUERYBOX = None

TITLE = "Éloignement"
TONES = {"neutral": "text_body", "success": "success", "warning": "warning", "error": "error"}

STATUTS = {
    "brouillon": "brouillon",
    "en_construction": "en construction",
    "en_instruction": "en instruction",
    "signe": "signé",
    "archive": "archivé",
    "actif": "actif",
}
ARCHIVED = "Ce dossier est archivé : aucune pièce ne peut plus y être ajoutée."
DRAFT = "Ce dossier est un brouillon : il ne peut pas encore recevoir d'acte."


def _bases(*interfaces):
    return (unohelper.Base,) + tuple(i for i in interfaces if isinstance(i, type))


# -- présentation des dossiers (pur) --------------------------------------------------

def fr_date(value):
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return text


def statut_label(dossier):
    detail = str(dossier.get("statut_detaille") or dossier.get("statut") or "")
    return STATUTS.get(detail, detail.replace("_", " "))


def mesure_label(dossier):
    mesure = dossier.get("mesure_envisagee") or dossier.get("mesure")
    if isinstance(mesure, dict):
        return str(mesure.get("libelle") or mesure.get("type") or "")
    return str(mesure or "")


def person_label(dossier):
    ident = dossier.get("identite") or {}
    who = f"{str(ident.get('nom') or '').upper()} {ident.get('prenom') or ''}".strip()
    return who or "identité non renseignée"


def resume_line(dossier):
    """« NOM Prénom · né(e) le JJ/MM/AAAA · mesure · statut · dossier n° X »."""
    ident = dossier.get("identite") or {}
    parts = [person_label(dossier)]
    if ident.get("date_naissance"):
        parts.append(f"né(e) le {fr_date(ident['date_naissance'])}")
    if ident.get("numero_etranger"):
        parts.append(f"n° étranger {ident['numero_etranger']}")
    parts += [mesure_label(dossier), statut_label(dossier), f"dossier n° {dossier.get('id')}"]
    return " · ".join(p for p in parts if p)


def blocked_reason(dossier):
    """Pourquoi ce dossier ne peut pas recevoir d'acte, ou ""."""
    detail = str(dossier.get("statut_detaille") or "")
    if detail == "archive" or dossier.get("archive") is True:
        return ARCHIVED
    if detail == "brouillon" or dossier.get("statut") == "brouillon":
        return DRAFT
    return ""


def header_text(dossier):
    ident = dossier.get("identite") or {}
    lines = [f"Dossier n° {dossier.get('id')} — {statut_label(dossier)}", person_label(dossier)]
    born = []
    if ident.get("date_naissance"):
        born.append(f"Né(e) le {fr_date(ident['date_naissance'])}")
    if ident.get("nationalite"):
        born.append(f"Nationalité : {ident['nationalite']}")
    if born:
        lines.append("  ·  ".join(born))
    if ident.get("numero_etranger"):
        lines.append(f"N° étranger : {ident['numero_etranger']}")
    if mesure_label(dossier):
        lines.append(f"Mesure envisagée : {mesure_label(dossier)}")
    return "\n".join(lines)


def modele_line(modele):
    chemin = str(modele.get("chemin") or "").strip()
    nom = str(modele.get("nom") or modele.get("id") or "")
    return f"{chemin} › {nom}" if chemin else nom


def save_hint(dossier):
    """Rappel du volet : il n'y a pas de bouton d'envoi, c'est Ctrl+S."""
    return f"Ctrl+S enregistre dans le dossier n° {dossier} d'Éloignement."


def change_line(change, labels):
    """Une ligne de E5. Un passage retouché à la main n'est pas cité : son
    étendue n'est plus sûre (une frappe par-dessus la valeur peut ne garder
    que la première lettre dans le signet)."""
    label = labels.get(change["balise"], change["balise"])
    if change.get("modified_by_hand"):
        return f"{label} : modifié à la main, non remplacé (dossier : « {change['new']} »)"
    return f"{label} : « {change['current']} » → « {change['new']} »"


# -- tâches de fond ------------------------------------------------------------------

def _noop():
    return None


def background(session, work, on_done):
    """Lance `work()` dans un thread ; `on_done(résultat, erreur)` revient sur le
    thread principal. Un pouls garde la pompe du dispatcher éveillée pendant
    l'attente du réseau (voir core/ui_thread.py)."""
    dispatcher = session.dispatcher
    done = threading.Event()
    dispatcher.start_pump()

    def pulse():
        while not done.wait(0.2):
            dispatcher.post(_noop)

    def run():
        try:
            result, error = work(), None
        except Exception as exc:  # remonté à l'écran, jamais avalé
            result, error = None, exc
        done.set()
        dispatcher.post(lambda: on_done(result, error))

    threading.Thread(target=pulse, name="mirai-eloignement-pouls", daemon=True).start()
    threading.Thread(target=run, name="mirai-eloignement", daemon=True).start()


_registry = [None]


def call_tool(session, model, name, args, on_done):
    """Exécute un tool Éloignement en fond ; on_done(ToolResult) sur le thread principal."""
    if _registry[0] is None:
        _registry[0] = tools_registry()
    try:
        controller = model.getCurrentController() if model is not None else None
    except Exception:
        controller = None
    ctx = ToolContext(model, controller, "writer", session.shell, dispatcher=session.dispatcher)

    def finish(result, error):
        if error is not None or result is None:
            result = ToolResult("", False, "", error=str(error or "échec"))
        on_done(result)

    background(session, lambda: _registry[0].call_tool(name, args, ctx), finish)


# -- petits composants UNO -------------------------------------------------------------

class _Items(*_bases(_XItemListener)):
    def __init__(self, on_change):
        self._on_change = on_change

    def itemStateChanged(self, _event):
        try:
            self._on_change()
        except Exception:
            pass

    def disposing(self, _event):
        pass


class _Enter(*_bases(_XKeyListener)):
    def __init__(self, on_enter):
        self._on_enter = on_enter

    def keyPressed(self, event):
        if getattr(event, "KeyCode", None) == KEY_RETURN:
            try:
                self._on_enter()
            except Exception:
                pass

    def keyReleased(self, _event):
        pass

    def disposing(self, _event):
        pass


class _Closer(*_bases(_XTopWindowListener)):
    def __init__(self, on_close):
        self._on_close = on_close

    def windowClosing(self, _event):
        self._on_close()

    def windowOpened(self, _event):
        pass

    def windowClosed(self, _event):
        pass

    def windowMinimized(self, _event):
        pass

    def windowNormalized(self, _event):
        pass

    def windowActivated(self, _event):
        pass

    def windowDeactivated(self, _event):
        pass

    def disposing(self, _event):
        pass


class Form:
    """Un dialogue DSFR construit en coordonnées pixel, et ses contrôles."""

    def __init__(self, session, title, width, height):
        self.session = session
        self.dialog, self.model = dsfr.make_dialog(session.shell.uno_ctx, title, width, height)
        self.font = dsfr.probe_font(session.shell.toolkit())
        self.models = {}
        self.handlers = []
        self.result = None

    def control(self, name):
        return self.dialog.getControl(name)

    def label(self, name, text, x, y, w, h, bold=False, tone="text_body", size=8,
              multiline=False):
        _, model = dsfr.add_control(self.dialog, self.model, name, "FixedText", x, y, w, h, {
            "Label": text, "FontName": self.font, "FontHeight": size,
            "FontWeight": 150.0 if bold else 100.0,
            "TextColor": dsfr.TOKENS.get(tone, dsfr.TOKENS["text_body"]),
            "MultiLine": multiline,
        })
        self.models[name] = model

    def set_label(self, name, text, tone=None):
        model = self.models.get(name)
        if model is None:
            return
        try:
            model.Label = text
            if tone:
                model.TextColor = dsfr.TOKENS.get(tone, dsfr.TOKENS["text_body"])
            self.control(name).setText(text)
        except Exception:
            pass

    def edit(self, name, x, y, w, h, on_enter=None):
        control, model = dsfr.add_control(self.dialog, self.model, name, "Edit", x, y, w, h, {
            "FontName": self.font, "FontHeight": 9})
        self.models[name] = model
        if on_enter is not None:
            handler = _Enter(on_enter)
            control.addKeyListener(handler)
            self.handlers.append(handler)

    def text(self, name):
        try:
            return str(self.control(name).getText() or "")
        except Exception:
            return str(getattr(self.models.get(name), "Text", "") or "")

    def listbox(self, name, x, y, w, h, on_select=None, multi=False, dropdown=False):
        control, model = dsfr.add_control(self.dialog, self.model, name, "ListBox", x, y, w, h, {
            "FontName": self.font, "FontHeight": 8, "Dropdown": dropdown,
            "MultiSelection": multi, "StringItemList": ()})
        self.models[name] = model
        if on_select is not None:
            handler = _Items(on_select)
            control.addItemListener(handler)
            self.handlers.append(handler)

    def set_items(self, name, lines, selected=()):
        model = self.models[name]
        model.StringItemList = tuple(lines)
        model.SelectedItems = tuple(selected)

    def selected(self, name):
        try:
            return list(self.control(name).getSelectedItemsPos())
        except Exception:
            return list(getattr(self.models[name], "SelectedItems", ()) or ())

    def button(self, name, text, x, y, w, h, on_click, primary=False):
        maker = dsfr.add_primary_button if primary else dsfr.add_chip
        control, model = maker(self.dialog, self.model, name, text, x, y, w, h, self.font,
                               on_click)
        self.models[name] = model
        self.handlers.append(control)

    def link(self, name, text, x, y, w, h, on_click):
        control, model = dsfr.add_link(self.dialog, self.model, name, text, x, y, w, h,
                                       self.font, on_click)
        self.models[name] = model
        self.handlers.append(control)

    def show(self, name, visible):
        try:
            self.control(name).setVisible(bool(visible))
        except Exception:
            pass

    def close(self, result=None):
        self.result = result
        try:
            self.dialog.endExecute()
        except Exception:
            pass

    def run(self):
        """Exécution modale, posée et centrée sur la fenêtre du document ; rend `result`.

        Sans pair créé sur une fenêtre parente, `execute()` rend la main aussitôt
        sous macOS, sans rien afficher."""
        try:
            parent = _parent_window(self.session)
            if parent is not None:
                self.dialog.createPeer(self.session.shell.toolkit(), parent)
                _center_on(self.dialog, parent)
            self.dialog.execute()
        finally:
            try:
                self.dialog.dispose()
            except Exception:
                pass
        return self.result


# -- E1 : nouvel acte -------------------------------------------------------------------

class ModeleDialog(Form):
    def __init__(self, session, model, can_fill):
        super().__init__(session, "Nouvel acte depuis Éloignement", 580, 470)
        self.doc = model
        self.modeles = []
        self.label("intro", "Choisissez un modèle d'acte de votre préfecture.", 16, 12, 548, 20)
        self.edit("q", 16, 40, 420, 28, on_enter=self.search)
        self.button("go", "Rechercher", 446, 40, 118, 28, self.search)
        self.listbox("list", 16, 78, 548, 280)
        self.label("status", "Chargement du catalogue…", 16, 366, 548, 20, tone="text_mention")
        if can_fill:
            self.link("fill", "Ce document contient des balises : le remplir depuis un dossier",
                      16, 392, 548, 20, lambda: self.close(("remplir", None)))
        self.link("cancel", "Annuler", 360, 426, 80, 28, lambda: self.close(None))
        self.button("ok", "Valider", 450, 424, 114, 32, self.validate, primary=True)
        self.search()

    def search(self):
        self.set_label("status", "Recherche…", "text_mention")
        call_tool(self.session, self.doc, "eloignement_lister_modeles",
                  {"q": self.text("q").strip()}, self._loaded)

    def _loaded(self, result):
        if not result.ok:
            self.set_label("status", "Catalogue indisponible : " + result.error, "error")
            return
        self.modeles = [m for m in result.data.get("modeles", []) if isinstance(m, dict)]
        self.set_items("list", [modele_line(m) for m in self.modeles])
        self.set_label("status", f"{len(self.modeles)} modèle(s)." if self.modeles
                       else "Aucun modèle ne correspond.", "text_mention")

    def validate(self):
        picked = self.selected("list")
        if not picked or picked[0] >= len(self.modeles):
            self.set_label("status", "Choisissez un modèle dans la liste.", "warning")
            return
        self.close(("modele", self.modeles[picked[0]]))


# -- E2 : rechercher un dossier -----------------------------------------------------------

class DossierDialog(Form):
    def __init__(self, session, model, intro):
        super().__init__(session, "Rechercher un dossier", 640, 540)
        self.doc = model
        self.dossiers = []
        self.header = {}
        self.label("intro", intro, 16, 12, 608, 20)
        self.label("hint",
                   "Prénom, nom et date de naissance, numéro étranger ou numéro de dossier.",
                   16, 32, 608, 18, tone="text_mention", size=7)
        self.edit("q", 16, 54, 480, 28, on_enter=self.search)
        self.button("go", "Rechercher", 506, 54, 118, 28, self.search)
        self.listbox("list", 16, 92, 608, 200, on_select=self.on_select)
        self.label("header_title", "En-tête du dossier", 16, 302, 608, 18, bold=True)
        self.label("header", "Sélectionnez un dossier pour contrôler son en-tête.",
                   16, 322, 608, 110, multiline=True, tone="text_mention")
        self.label("status", "", 16, 440, 608, 20, tone="text_mention")
        self.link("cancel", "Annuler", 420, 494, 80, 28, lambda: self.close(None))
        self.button("ok", "Valider", 510, 492, 114, 32, self.validate, primary=True)

    def search(self):
        q = self.text("q").strip()
        if len(q) < 2:
            self.set_label("status", "Saisissez au moins deux caractères.", "warning")
            return
        self.set_label("status", "Recherche…", "text_mention")
        call_tool(self.session, self.doc, "eloignement_rechercher_dossier", {"q": q},
                  lambda result: self._found(result, q))

    def _found(self, result, q):
        if not result.ok:
            self.set_label("status", "Recherche impossible : " + result.error, "error")
            return
        self.dossiers = [d for d in result.data.get("dossiers", []) if isinstance(d, dict)]
        self.set_items("list", [resume_line(d) for d in self.dossiers])
        self.header = {}
        self.set_label("header", "Sélectionnez un dossier pour contrôler son en-tête.",
                       "text_mention")
        self.set_label("status", f"{len(self.dossiers)} dossier(s)." if self.dossiers
                       else f"Aucun dossier ne correspond à « {q} ».", "text_mention")

    def _current(self):
        picked = self.selected("list")
        if picked and picked[0] < len(self.dossiers):
            return self.dossiers[picked[0]]
        return None

    def on_select(self):
        dossier = self._current()
        if dossier is None:
            return
        self.header = dict(dossier)
        self.set_label("header", header_text(dossier), "text_body")
        reason = blocked_reason(dossier)
        self.set_label("status", reason, "warning" if reason else "text_mention")
        wanted = dossier.get("id")
        call_tool(self.session, self.doc, "eloignement_lire_dossier", {"dossier": str(wanted)},
                  lambda result: self._header(result, wanted))

    def _header(self, result, wanted):
        current = self._current()
        if not result.ok or current is None or current.get("id") != wanted:
            return
        full = dict(current)
        full.update(result.data.get("dossier") or {})
        self.header = full
        self.set_label("header", header_text(full), "text_body")
        reason = blocked_reason(full)
        if reason:
            self.set_label("status", reason, "warning")

    def validate(self):
        dossier = self.header if self.header else self._current()
        if not dossier:
            self.set_label("status", "Choisissez un dossier dans la liste.", "warning")
            return
        reason = blocked_reason(dossier)
        if reason:
            self.set_label("status", reason, "warning")
            return
        self.close(dossier)


# -- E2b : options de génération ---------------------------------------------------------

class OptionsDialog(Form):
    """Une ligne par option exigée : liste si Éloignement donne les valeurs, saisie sinon."""

    ROW = 54

    def __init__(self, session, modele, wanted):
        height = 120 + self.ROW * len(wanted)
        super().__init__(session, "Options de génération", 560, height)
        self.wanted = wanted
        self.label("intro", f"Éloignement a besoin de ces options pour « {modele_line(modele)} ».",
                   16, 12, 528, 34, multiline=True)
        y = 52
        for i, option in enumerate(wanted):
            self.label(f"l{i}", option.libelle, 16, y, 528, 18, bold=True)
            choices = option.choices()
            if choices:
                self.listbox(f"o{i}", 16, y + 20, 528, 26, dropdown=True)
                picked = [k for k, c in enumerate(choices) if c["valeur"] == option.defaut]
                self.set_items(f"o{i}", [c["libelle"] for c in choices], picked[:1])
            else:
                self.edit(f"o{i}", 16, y + 20, 528, 26)
                if option.defaut is not None:
                    self.models[f"o{i}"].Text = str(option.defaut)
            y += self.ROW
        self.label("status", "", 16, y, 528, 20, tone="warning")
        self.link("cancel", "Annuler", 330, y + 28, 80, 28, lambda: self.close(None))
        self.button("ok", "Valider", 420, y + 26, 124, 32, self.validate, primary=True)

    def _answers(self):
        answers = {}
        for i, option in enumerate(self.wanted):
            choices = option.choices()
            if not choices:
                answers[option.option] = self.text(f"o{i}")
                continue
            picked = self.selected(f"o{i}")
            if picked and picked[0] < len(choices):
                answers[option.option] = choices[picked[0]]["valeur"]
        return answers

    def validate(self):
        try:
            body = options_mod.build(self.wanted, self._answers())
        except ValueError as exc:
            self.set_label("status", str(exc), "warning")
            return
        self.close(body)


# -- E4 : conflit, E5 : actualiser -----------------------------------------------------------

class ConflictDialog(Form):
    def __init__(self, session, dossier):
        super().__init__(session, "Conflit de version", 560, 220)
        self.label("text", "Le document a été modifié dans Éloignement depuis votre version.\n"
                           f"Rien n'a été écrasé dans le dossier n° {dossier}.",
                   16, 14, 528, 60, multiline=True)
        self.button("mine", "Garder ma version comme nouvelle version", 16, 90, 528, 32,
                    lambda: self.close("mine"), primary=True)
        self.button("theirs", "Ouvrir la version du dossier", 16, 132, 528, 30,
                    lambda: self.close("theirs"))
        self.link("cancel", "Annuler (l'envoi reste en attente)", 16, 178, 528, 24,
                  lambda: self.close(None))


class RefreshDialog(Form):
    def __init__(self, session, changes, labels):
        super().__init__(session, "Actualiser depuis le dossier", 640, 420)
        self.changes = changes
        self.label("intro", "Ces valeurs du dossier diffèrent du document : sélectionnez celles "
                            "à reprendre. Un passage modifié à la main n'est jamais remplacé.",
                   16, 12, 608, 40, multiline=True)
        self.listbox("list", 16, 58, 608, 280, multi=True)
        self.set_items("list", [change_line(c, labels) for c in changes],
                       [i for i, c in enumerate(changes) if not c.get("modified_by_hand")])
        self.link("cancel", "Annuler", 420, 374, 80, 28, lambda: self.close(None))
        self.button("ok", "Appliquer", 510, 372, 114, 32, self.apply, primary=True)

    def apply(self):
        self.close([self.changes[i] for i in self.selected("list")
                    if i < len(self.changes) and not self.changes[i].get("modified_by_hand")])


# -- E3 : volet du document lié -------------------------------------------------------------

class Panel:
    WIDTH = 330
    HEIGHT = 520

    def __init__(self, ui, model):
        self.ui = ui
        self.session = ui.session
        self.doc = model
        self.form = Form(self.session, TITLE, self.WIDTH, self.HEIGHT)
        self.labels_shown = []
        f = self.form
        w = self.WIDTH - 32
        f.label("title", "", 16, 12, w, 22, bold=True, size=10)
        f.label("person", "", 16, 38, w, 18)
        f.label("modele", "", 16, 58, w, 18, tone="text_mention")
        f.label("version", "", 16, 78, w, 18, tone="text_mention")
        f.label("save_hint", "", 16, 98, w, 18, bold=True, tone="primary")
        f.label("state", "", 16, 118, w, 36, multiline=True)
        f.label("todo", "À compléter", 16, 158, w, 18, bold=True)
        f.listbox("empty", 16, 178, w, 118, on_select=self.go_to)
        y = 306
        for name, text, action in (
                ("fill", "Remplir depuis un dossier", self.fill_from_dossier),
                ("refresh", "Actualiser depuis le dossier", self.refresh_values),
                ("conflict", "Résoudre le conflit…", self.resolve),
                ("retry", "Réessayer l'envoi", self.retry),
                ("copy", "Enregistrer une copie sur le poste", self.save_copy),
                ("open", "Ouvrir le dossier dans Éloignement", self.open_dossier)):
            f.button(name, text, 16, y, w, 28, action)
            y += 34
        closer = _Closer(self.close)
        f.handlers.append(closer)
        frame = model.getCurrentController().getFrame()
        parent = frame.getContainerWindow()
        f.dialog.createPeer(self.session.shell.toolkit(), parent)
        try:
            f.dialog.addTopWindowListener(closer)
        except Exception:
            pass
        try:
            ps = parent.getPosSize()
            if ps.Width > 0 and ps.Height > 0:
                f.dialog.setPosSize(ps.X + max(0, ps.Width - self.WIDTH - 24), ps.Y + 90, 0, 0,
                                    dsfr.POS)
        except Exception:
            pass
        self.render()
        f.dialog.setVisible(True)

    def bring_to_front(self):
        try:
            self.form.dialog.setVisible(True)
            self.form.dialog.getPeer().toFront()
        except Exception:
            pass

    def render(self):
        f = self.form
        session = self.session
        state = session.attach(self.doc)
        link = link_mod.read_link(self.doc)
        if link is None:
            f.set_label("title", "Document non lié")
            f.set_label("person", "Ce document n'est lié à aucun dossier.")
            for name in ("modele", "version", "save_hint", "state"):
                f.set_label(name, "")
        else:
            f.set_label("title", f"Lié au dossier n° {link.dossier}")
            header = state.header or {}
            f.set_label("person", person_label(header) if header else "")
            f.set_label("modele", "Modèle : " + (state.modele_nom or link.modele or "—"))
            f.set_label("version", "Version : " + (link.version or "—"))
            f.set_label("save_hint", save_hint(link.dossier))
            text, tone = session.describe(self.doc)
            f.set_label("state", text, TONES.get(tone, "text_body"))
        try:
            names = fill.placeholder_names(fill.find_placeholders(self.doc))
        except Exception:
            names = list(state.empty)
        self.labels_shown = names
        f.set_items("empty", [f"{state.labels.get(n, n)}  ({n})" if state.labels.get(n, n) != n
                              else n for n in names])
        f.set_label("todo", f"À compléter ({len(names)})" if names else "À compléter : rien")
        entry = session.outbox.get(state.key) if state.key else None
        f.show("fill", link is None and bool(names))
        f.show("refresh", link is not None)
        f.show("conflict", entry is not None and entry.state == "conflit")
        f.show("retry", entry is not None and entry.state == "attente")
        f.show("copy", link is not None)
        interface = ((state.header or {}).get("_links") or {}).get("interface")
        f.show("open", bool(interface))

    def fill_from_dossier(self):
        self.ui.fill_flow_for(self.doc)

    def go_to(self):
        picked = self.form.selected("empty")
        if picked and picked[0] < len(self.labels_shown):
            fill.go_to(self.doc, self.labels_shown[picked[0]])

    def refresh_values(self):
        self.form.set_label("state", "Lecture des valeurs du dossier…", "text_mention")
        call_tool(self.session, self.doc, "eloignement_lire_ecarts", {}, self._changes)

    def _changes(self, result):
        if not result.ok:
            self.form.set_label("state", "Actualisation impossible : " + result.error, "error")
            return
        changes = result.data.get("ecarts") or []
        if not changes:
            self.session.set_status(self.doc, "Le document est à jour avec le dossier.", "success")
            return
        accepted = RefreshDialog(self.session, changes, result.data.get("libelles") or {}).run()
        if not accepted:
            self.render()
            return
        call_tool(self.session, self.doc, "eloignement_appliquer_ecarts", {"ecarts": accepted},
                  lambda r: self.session.set_status(
                      self.doc, r.content if r.ok else "Actualisation impossible : " + r.error,
                      "success" if r.ok else "error"))

    def resolve(self):
        self.ui.show_conflict(self.doc)

    def retry(self):
        state = self.session.attach(self.doc)
        if state.key:
            self.session.outbox.keep_mine(state.key)
        self.session.kick()
        self.render()

    def save_copy(self):
        """Copie sur le poste : rien n'est envoyé, la copie garde le lien."""
        try:
            ctx = self.session.shell.uno_ctx
            picker = ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.ui.dialogs.FilePicker", ctx)
            picker.initialize((1,))   # FILESAVE_SIMPLE
            picker.appendFilter("Texte ODF (.odt)", "*.odt")
            try:
                title = self.doc.getDocumentProperties().Title or ""
            except Exception:
                title = ""
            picker.setDefaultName((title or "acte") + ".odt")
            if picker.execute() != 1:
                return
            url = picker.getSelectedFiles()[0]
            if not url.lower().endswith(".odt"):
                url += ".odt"
            args = ()
            if PropertyValue is not None:
                prop = PropertyValue()
                prop.Name, prop.Value = "FilterName", "writer8"
                args = (prop,)
            self.doc.storeToURL(url, args)
            self.session.set_status(
                self.doc, "Copie enregistrée sur le poste (elle garde le lien).", "success")
        except Exception as exc:
            self.form.set_label("state", f"Copie impossible : {exc}", "error")

    def open_dossier(self):
        state = self.session.attach(self.doc)
        url = ((state.header or {}).get("_links") or {}).get("interface")
        if url:
            try:
                self.session.shell.open_link(url)
            except Exception as exc:
                self.form.set_label("state", f"Ouverture impossible : {exc}", "error")

    def close(self):
        self.ui.panels.pop(id(self.doc), None)
        try:
            self.form.dialog.setVisible(False)
            self.form.dialog.dispose()
        except Exception:
            pass


# -- contrôleur -------------------------------------------------------------------------

class EloignementUI:
    """L'interface branchée sur la session (`session.ui`)."""

    def __init__(self, session):
        self.session = session
        self.panels = {}

    # appelés par la session -----------------------------------------------------
    def show_panel(self, model):
        panel = self.panels.get(id(model))
        if panel is not None:
            panel.render()
            panel.bring_to_front()
            return panel
        panel = Panel(self, model)
        self.panels[id(model)] = panel
        self.load_context(model)
        return panel

    def refresh(self, model):
        panel = self.panels.get(id(model))
        if panel is not None:
            panel.render()

    def document_closed(self, model):
        panel = self.panels.pop(id(model), None)
        if panel is not None:
            panel.close()

    def confirm_unknown_link(self, _model, link):
        return ask(self.session, TITLE,
                   f"Ce document est lié au dossier n° {link.dossier}, mais ce lien n'a pas "
                   "été créé sur ce poste.\n\nEnregistrer ce document dans ce dossier "
                   "d'Éloignement ?")

    def show_conflict(self, model):
        link = link_mod.read_link(model)
        if link is None:
            return
        state = self.session.attach(model)
        choice = ConflictDialog(self.session, link.dossier).run()
        if choice == "mine":
            key = state.key
            background(self.session,
                       lambda: self.session.keep_mine(key, link.dossier, link.document),
                       lambda _r, _err: self.refresh(model))
        elif choice == "theirs":
            self.open_dossier_version(model, link)
        self.refresh(model)

    # chargements -----------------------------------------------------------------
    def load_context(self, model):
        """En-tête du dossier et nom du modèle, pour le volet."""
        link = link_mod.read_link(model)
        if link is None:
            return

        def work():
            header = self.session.client.dossier(link.dossier)
            modele_nom = ""
            if link.document:
                for acte in self.session.client.actes(link.dossier):
                    if str(acte.get("acte_id") or acte.get("id")) == link.document:
                        modele_nom = str((acte.get("modele") or {}).get("nom") or "")
            return header, modele_nom

        def done(result, error):
            if error is not None or not result:
                return
            state = self.session.attach(model)
            state.header = result[0] if isinstance(result[0], dict) else {}
            state.modele_nom = state.modele_nom or result[1]
            self.refresh(model)

        if self.session.enabled():
            background(self.session, work, done)

    def open_dossier_version(self, _model, link):
        def work():
            return self.session.download_act(link.dossier, link.document,
                                             "", suffix="(version du dossier)")

        def done(result, error):
            if error is not None:
                reason = getattr(error, "message", error)
                message(self.session, TITLE, f"Ouverture impossible : {reason}")
                return
            path, version = result
            self.session.open_linked(path, link_mod.Link(link.dossier, link.modele,
                                                          link.document, version))

        background(self.session, work, done)

    # parcours -------------------------------------------------------------------
    def start(self, model):
        """Point d'entrée de la commande « Éloignement »."""
        if not self.session.enabled():
            message(self.session, TITLE, DISABLED)
            return
        is_writer = model is not None and hasattr(model, "Text")
        if is_writer and link_mod.read_link(model) is not None:
            self.session.attach(model)
            self.show_panel(model)
            return
        can_fill = is_writer and bool(fill.find_placeholders(model))
        choice = ModeleDialog(self.session, model if is_writer else None, can_fill).run()
        if not choice:
            return
        kind, modele = choice
        if kind == "remplir":
            self.fill_flow_for(model)
            return
        dossier = DossierDialog(self.session, model, f"Acte : {modele_line(modele)}").run()
        if not dossier:
            return
        wanted = options_mod.required(modele)
        chosen = OptionsDialog(self.session, modele, wanted).run() if wanted else {}
        if chosen is None:
            return
        self.generate(model, modele, dossier, chosen)

    def generate(self, model, modele, dossier, chosen=None):
        progress = Progress(self.session, "Génération de l'acte dans Éloignement…")
        args = {"dossier": str(dossier.get("id")), "modele": str(modele.get("id")),
                "modele_nom": str(modele.get("nom") or ""), "_header": dossier}
        if chosen:
            args["options"] = chosen

        def done(result):
            progress.close()
            if not result.ok:
                message(self.session, TITLE, "Génération impossible : " + result.error
                        + "\n\nSi le dossier ne permet pas cette mesure, complétez-le dans "
                          "Éloignement, puis recommencez.")

        call_tool(self.session, model, "eloignement_generer_acte", args, done)

    def fill_flow_for(self, model):
        if model is None or not hasattr(model, "Text"):
            return
        dossier = DossierDialog(self.session, model,
                                "Remplir ce document avec les valeurs d'un dossier").run()
        if not dossier:
            return
        progress = Progress(self.session, "Lecture des valeurs du dossier…")

        def done(result):
            progress.close()
            if not result.ok:
                message(self.session, TITLE, "Remplissage impossible : " + result.error)
                return
            self.show_panel(model)
            self.refresh(model)

        call_tool(self.session, model, "eloignement_remplir_depuis_dossier",
                  {"dossier": str(dossier.get("id")), "_header": dossier}, done)


class Progress:
    """Petite fenêtre non modale pendant une attente réseau."""

    def __init__(self, session, text):
        self.form = Form(session, TITLE, 380, 70)
        self.form.label("text", text, 16, 22, 348, 24)
        try:
            frame = session.shell.uno_ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", session.shell.uno_ctx).getCurrentFrame()
            parent = frame.getContainerWindow() if frame else None
            self.form.dialog.createPeer(session.shell.toolkit(), parent)
            self.form.dialog.setVisible(True)
        except Exception:
            pass

    def close(self):
        try:
            self.form.dialog.setVisible(False)
            self.form.dialog.dispose()
        except Exception:
            pass


def _center_on(dialog, parent):
    try:
        outer, own = parent.getPosSize(), dialog.getPosSize()
        dialog.setPosSize(outer.X + max(0, (outer.Width - own.Width) // 2),
                          outer.Y + max(0, (outer.Height - own.Height) // 3),
                          own.Width, own.Height, dsfr.POSSIZE)
    except Exception:
        pass


def _parent_window(session):
    try:
        frame = session.shell.uno_ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", session.shell.uno_ctx).getCurrentFrame()
        return frame.getContainerWindow() if frame else None
    except Exception:
        return None


def _message_box(session, kind, buttons, title, text):
    toolkit = session.shell.toolkit()
    box = toolkit.createMessageBox(_parent_window(session), kind, buttons, title, text)
    try:
        return box.execute()
    finally:
        try:
            box.dispose()
        except Exception:
            pass


def message(session, title, text):
    try:
        _message_box(session, INFOBOX, 1, title, text)   # BUTTONS_OK
    except Exception as exc:
        session.log(f"[eloignement] message non affiché : {exc}")


def ask(session, title, text):
    """Question oui/non ; non si la boîte ne peut pas s'afficher."""
    try:
        return _message_box(session, QUERYBOX, 3, title, text) == 2   # YES_NO → YES
    except Exception as exc:
        session.log(f"[eloignement] question non affichée : {exc}")
        return False


TOOLBAR = "private:resource/toolbar/custom_mirai_eloignement"
TOOLBAR_COMMAND = "service:fr.gouv.interieur.mirai.do?Eloignement&src=toolbar"
_TOOLBAR_STYLE = 256 | 32   # ItemStyle.TEXT | ItemStyle.AUTO_SIZE


def _prop(name, value):
    prop = PropertyValue()
    prop.Name, prop.Value = name, value
    return prop


def sync_toolbar(shell, enabled, frame=None):
    """Barre d'outils « Éloignement » de Writer, créée à l'exécution.

    Un bouton déclaré dans Addons.xcu serait visible de tous, interrupteur
    éteint compris : la barre n'existe donc que quand Éloignement est actif,
    et elle est retirée quand il ne l'est plus."""
    if PropertyValue is None:
        return False
    ctx = shell.uno_ctx
    supplier = ctx.getServiceManager().createInstanceWithContext(
        "com.sun.star.ui.ModuleUIConfigurationManagerSupplier", ctx)
    manager = supplier.getUIConfigurationManager("com.sun.star.text.TextDocument")
    present = bool(manager.hasSettings(TOOLBAR))
    if enabled and not present:
        settings = manager.createSettings()
        settings.UIName = "MIrAI — Éloignement"
        button = (_prop("CommandURL", TOOLBAR_COMMAND), _prop("Label", TITLE),
                  _prop("Type", 0), _prop("Style", _TOOLBAR_STYLE))
        # Un tuple Python n'est pas reconnu comme séquence de PropertyValue.
        uno.invoke(settings, "insertByIndex",
                   (0, uno.Any("[]com.sun.star.beans.PropertyValue", button)))
        manager.insertSettings(TOOLBAR, settings)
        manager.store()
        shell.set_config("eloignement_toolbar", True)
    elif not enabled and present:
        manager.removeSettings(TOOLBAR)
        manager.store()
        shell.set_config("eloignement_toolbar", False)
    if enabled and frame is not None:
        try:
            layout = frame.LayoutManager
            if layout.getElement(TOOLBAR) is None:
                layout.createElement(TOOLBAR)
            layout.showElement(TOOLBAR)
        except Exception:
            pass
    return enabled != present


def open_eloignement(shell, model):
    """Commande « Éloignement » : volet du document lié, ou nouvel acte."""
    session = get_session(shell)
    if session.ui is None:
        session.ui = EloignementUI(session)
    session.ui.start(model)
    try:
        sync_toolbar(shell, session.enabled(), _frame_of(model))
    except Exception as exc:
        shell.log(f"[eloignement] barre d'outils : {exc}")


def _frame_of(model):
    try:
        return model.getCurrentController().getFrame()
    except Exception:
        return None


def on_document_loaded(shell, model):
    """Ouverture d'un document : s'il est lié (et la fonction active), on le suit."""
    if model is None or not hasattr(model, "getDocumentProperties"):
        return False
    if link_mod.read_link(model) is None:
        return False
    session = get_session(shell)
    if not session.enabled():
        return False
    if session.ui is None:
        session.ui = EloignementUI(session)
    return session.on_loaded(model)


def document_event(shell, model, enabled):
    """Tout document ouvert : la barre d'outils suit l'interrupteur, et un
    document lié est suivi."""
    try:
        sync_toolbar(shell, enabled, _frame_of(model))
    except Exception as exc:
        shell.log(f"[eloignement] barre d'outils : {exc}")
    return on_document_loaded(shell, model) if enabled else False

