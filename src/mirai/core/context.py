"""Contexte d'exécution des tools : document, contrôleur, undo, marshalling.

Un seul contexte undo par run d'orchestrateur (ouvert paresseusement par le
premier tool mutant, fermé dans le finally du run) → l'action complète
s'annule en un seul Ctrl+Z.

Le contexte porte aussi le **dispatcher** : c'est le
point de passage unique par lequel tout accès au document remonte sur le
thread principal. Un run vit dans un thread worker ; toucher `model` ou
`controller` directement depuis là serait un accès VCL sans SolarMutex.
"""

from __future__ import annotations


class ToolContext:
    def __init__(self, model, controller, app, shell, dispatcher=None):
        self.model = model            # document UNO (Writer ou Calc)
        self.controller = controller  # model.CurrentController
        self.app = app                # "writer" | "calc"
        self.shell = shell            # ShellServices
        self.dispatcher = dispatcher  # MainThreadDispatcher | None
        self._undo_manager = None
        self._undo_open = False

    def on_main(self, fn, timeout: float = 30.0):
        """Exécute fn sur le thread principal et rend son résultat.

        Sans dispatcher (tests, ou appel déjà sur le thread principal), fn
        s'exécute sur place — c'est le repli qui garde le moteur testable
        hors LibreOffice.
        """
        if self.dispatcher is None:
            return fn()
        return self.dispatcher.call(fn, timeout=timeout)

    def undo_begin(self, label):
        if self._undo_open:
            return
        try:
            self._undo_manager = self.on_main(self.model.getUndoManager)
            self.on_main(lambda: self._undo_manager.enterUndoContext(label))
            self._undo_open = True
        except Exception:
            self._undo_manager = None

    def undo_end(self):
        if not self._undo_open or self._undo_manager is None:
            self._undo_open = False
            return
        try:
            self.on_main(self._undo_manager.leaveUndoContext)
        except Exception:
            pass
        self._undo_open = False
