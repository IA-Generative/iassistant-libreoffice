"""Faux dialogue de la palette : des contrôles à état réel, montés sans LibreOffice."""

from unittest.mock import MagicMock


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
        # Ce que l'écran affiche : écrire `model.Text` ne repeint pas un
        # contrôle doté d'un peer, seul `setText()` le fait.
        self.shown_text = ""

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
        self.shown_text = text

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

    def getControls(self):
        return list(self.controls.values())

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


def _build(palette_module, app="writer"):
    shell = MagicMock()
    shell.toolkit.return_value = MagicMock()
    shell.data_dir.return_value = "/tmp/mirai-test-palette"
    shell.get_config.side_effect = lambda key, default=None: default
    shell.log = lambda _m: None

    uno_ctx = MagicMock()
    return palette_module.AssistantPalette(uno_ctx, shell, app)
