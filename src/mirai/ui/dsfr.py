"""Primitives DSFR pour dialogs UNO — tokens vérifiés contre @gouvfr/dsfr@1.14.

Contraintes assumées du toolkit awt : coins carrés (conformes DSFR), pas
d'ombres, d'animations ni de focus ring. Les « chips » sont des FixedText
cliquables — jamais des UnoControlButton, dont les thèmes natifs (gtk3/macOS)
écrasent BackgroundColor.

La police Marianne n'est JAMAIS embarquée (licence État hors MIT du DSFR) :
sonde runtime sur le poste, repli Arial puis Liberation Sans.
"""

import unohelper
from com.sun.star.awt import XMouseListener

try:
    from com.sun.star.awt.PosSize import POS, POSSIZE
except Exception:
    POS, POSSIZE = 3, 15

# Tokens DSFR (thème clair) — sous-ensemble « feels DSFR »
TOKENS = {
    "primary":        0x000091,   # blue-france-sun-113-625
    "primary_hover":  0x1212FF,
    "chip_bg":        0xE3E3FD,   # blue-france-925-125 (action low)
    "chip_bg_hover":  0xC1C1FB,
    "bg":             0xFFFFFF,
    "bg_alt":         0xF6F6F6,
    "bg_contrast":    0xEEEEEE,
    "bg_accent":      0xF5F5FE,
    "text_body":      0x3A3A3A,
    "text_mention":   0x666666,
    "text_inverted":  0xFFFFFF,
    "border":         0xDDDDDD,
    "error":          0xCE0500,
    "success":        0x18753C,
}

FONT_CANDIDATES = ("Marianne", "Arial", "Liberation Sans", "Helvetica")

_font_cache = [None]


def probe_font(toolkit):
    """Première police disponible parmi les candidates (résultat mis en cache)."""
    if _font_cache[0]:
        return _font_cache[0]
    chosen = FONT_CANDIDATES[1]  # repli raisonnable : Arial
    try:
        device = toolkit.createScreenCompatibleDevice(0, 0)
        available = {str(d.Name) for d in device.getFontDescriptors()}
        for candidate in FONT_CANDIDATES:
            if candidate in available:
                chosen = candidate
                break
    except Exception:
        pass
    _font_cache[0] = chosen
    return chosen


def make_dialog(uno_ctx, title, width, height):
    """Crée un UnoControlDialog vide (pattern programmatique du repo)."""
    smgr = uno_ctx.getServiceManager()
    dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", uno_ctx)
    model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", uno_ctx)
    dialog.setModel(model)
    dialog.setVisible(False)
    dialog.setTitle(title)
    dialog.setPosSize(0, 0, width, height, POSSIZE)
    try:
        model.BackgroundColor = TOKENS["bg"]
    except Exception:
        pass
    # Fenêtre redimensionnable : `Sizeable` ne fait qu'autoriser la poignée —
    # c'est un XWindowListener qui doit ensuite replacer les contrôles, sinon
    # le cadre grandit et le contenu reste figé.
    for prop, value in (("Sizeable", True), ("Moveable", True), ("Closeable", True)):
        try:
            setattr(model, prop, value)
        except Exception:
            pass          # propriété non supportée par ce toolkit : sans gravité
    return dialog, model


def add_control(dialog, model, name, kind, x, y, w, h, props=None):
    """Ajoute un contrôle (kind ∈ FixedText, Edit, Button, FixedLine…)."""
    control_model = model.createInstance(f"com.sun.star.awt.UnoControl{kind}Model")
    model.insertByName(name, control_model)
    control = dialog.getControl(name)
    control.setPosSize(x, y, w, h, POSSIZE)
    for key, value in (props or {}).items():
        try:
            setattr(control_model, key, value)
        except Exception:
            pass
    return control, control_model


class ClickHandler(unohelper.Base, XMouseListener):
    """Clic + rollover sur un FixedText (la primitive « chip » du DSFR UNO)."""

    def __init__(self, control_model, on_click=None,
                 bg=None, bg_hover=None, fg=None, fg_hover=None):
        self._model = control_model
        self._on_click = on_click
        self._bg = bg
        self._bg_hover = bg_hover
        self._fg = fg
        self._fg_hover = fg_hover

    def mousePressed(self, event):
        if self._on_click:
            try:
                self._on_click()
            except Exception:
                pass

    def mouseReleased(self, event):
        pass

    def mouseEntered(self, event):
        try:
            if self._bg_hover is not None:
                self._model.BackgroundColor = self._bg_hover
            if self._fg_hover is not None:
                self._model.TextColor = self._fg_hover
        except Exception:
            pass

    def mouseExited(self, event):
        try:
            if self._bg is not None:
                self._model.BackgroundColor = self._bg
            if self._fg is not None:
                self._model.TextColor = self._fg
        except Exception:
            pass

    def disposing(self, event):
        pass


def add_chip(dialog, model, name, label, x, y, width, height, font, on_click):
    """Chip DSFR (bouton tertiaire) : FixedText cliquable fond bleu clair.
    Les tailles passées sont provisoires — le layout mesuré les recalcule."""
    control, control_model = add_control(
        dialog, model, name, "FixedText", x, y, width, height, {
            "Label": "  " + label + "  ",
            "BackgroundColor": TOKENS["chip_bg"],
            "TextColor": TOKENS["primary"],
            "Border": 2,
            "BorderColor": TOKENS["chip_bg"],
            "FontName": font,
            "FontHeight": 8,
            "FontWeight": 110.0,   # SEMIBOLD ≈ Marianne Medium
            "Align": 1,            # centré
            "VerticalAlign": 1,
        })
    handler = ClickHandler(control_model, on_click=on_click,
                           bg=TOKENS["chip_bg"], bg_hover=TOKENS["chip_bg_hover"])
    control.addMouseListener(handler)
    return control, control_model


def add_link(dialog, model, name, label, x, y, width, height, font, on_click):
    """Lien discret du pied de palette (Nouvelle conversation)."""
    control, control_model = add_control(
        dialog, model, name, "FixedText", x, y, width, height, {
            "Label": label,
            "TextColor": TOKENS["primary"],
            "FontName": font,
            "FontHeight": 8,
        })
    handler = ClickHandler(control_model, on_click=on_click,
                           fg=TOKENS["primary"], fg_hover=TOKENS["primary_hover"])
    control.addMouseListener(handler)
    return control, control_model


def add_primary_button(dialog, model, name, label, x, y, w, h, font, on_click):
    """Bouton primaire DSFR simulé en FixedText (couleurs exactes garanties)."""
    control, control_model = add_control(
        dialog, model, name, "FixedText", x, y, w, h, {
            "Label": label,
            "BackgroundColor": TOKENS["primary"],
            "TextColor": TOKENS["text_inverted"],
            "FontName": font,
            "FontHeight": 9,
            "FontWeight": 150.0,
            "Align": 1,
            "VerticalAlign": 1,
        })
    handler = ClickHandler(control_model, on_click=on_click,
                           bg=TOKENS["primary"], bg_hover=TOKENS["primary_hover"])
    control.addMouseListener(handler)
    return control, control_model
