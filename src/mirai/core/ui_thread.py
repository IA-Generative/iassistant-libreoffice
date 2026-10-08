"""Marshalling vers le thread principal UNO.

RÈGLE D'ARCHITECTURE : aucun appel UNO — document, contrôles, undo — ne doit
partir d'un thread de fond. Le run vit dans un worker ; tout ce qui touche
LibreOffice repasse par ce dispatcher.

Deux primitives, à choisir selon qu'on attend un résultat :

- `post(fn)`   : « fais ça quand tu peux », rend la main tout de suite.
                 Pour les mises à jour d'affichage, qui n'ont pas de retour.
- `call(fn)`   : exécute sur le thread principal et RAPPORTE la valeur.
                 Pour lire ou modifier le document depuis le worker.

`call()` est borné par un délai : si le thread principal est retenu (boîte de
dialogue modale), on abandonne proprement au lieu d'attendre indéfiniment.

Le transport est `com.sun.star.awt.AsyncCallback`, déjà éprouvé dans la
coquille et dans `_DeferredCall` de la palette. Quand il est indisponible
(tests hors LibreOffice, service absent), on retombe sur une exécution
directe : mieux vaut un comportement dégradé qu'une fonction morte.
"""

from __future__ import annotations

import queue
import threading

try:
    import unohelper
    from com.sun.star.awt import XCallback
except ImportError:  # hors LibreOffice (tests)
    unohelper = None
    XCallback = None


_CALLBACK_BASES = (unohelper.Base, XCallback) if XCallback is not None else ()


class _Task(*_CALLBACK_BASES):
    """Une unité de travail livrée au thread principal par AsyncCallback."""

    def __init__(self, fn):
        self._fn = fn

    def notify(self, _data=None):
        self.run()

    def run(self):
        """S'exécute sur le thread principal ; ne laisse jamais fuir d'exception."""
        try:
            self._fn()
        except Exception:
            pass


class DispatcherClosed(RuntimeError):
    """La palette a été fermée : plus rien ne doit être exécuté."""


class DispatcherTimeout(RuntimeError):
    """Le thread principal n'a pas répondu dans le délai imparti."""


class MainThreadDispatcher:
    """Fait exécuter du code sur le thread principal UNO depuis n'importe quel thread.

    Une instance par palette ouverte. Après `close()`, `post()` devient inerte
    et `call()` lève `DispatcherClosed` — ce qui met fin proprement à un run
    encore en vol quand l'utilisateur ferme la fenêtre.
    """

    def __init__(self, uno_ctx, log=None):
        self.uno_ctx = uno_ctx
        self._log = log
        self._closed = False
        self._callback_service = None   # créé une fois, conservé (voir _async_callback)
        self._queue = queue.Queue()     # tâches en attente du thread principal
        self._pump = None               # tâche de pompe en vol (auto-réarmée)
        self._pumping = False

    def close(self):
        """Rend le dispatcher inerte. Idempotent, appelable de n'importe où."""
        self._closed = True
        self._pumping = False
        self._pump = None
        self._callback_service = None

    def post(self, fn) -> bool:
        """Planifie fn sur le thread principal sans attendre. True si accepté.

        La tâche part dans une FILE, drainée par la pompe (voir `start_pump`).
        Poster directement un AsyncCallback depuis un thread de fond ne suffit
        pas : l'événement est mis en attente mais ne RÉVEILLE pas la boucle
        d'événements de LibreOffice. Au repos, il n'est délivré qu'au prochain
        geste de l'utilisateur — d'où une interface qui semble figée alors que
        le travail est terminé depuis longtemps.
        """
        if self._closed:
            return False
        if is_main_thread():
            # Déjà au bon endroit : inutile de faire un détour par la file.
            _Task(fn).run()
            return True
        self._queue.put(fn)
        # Relancer la pompe si elle s'est éteinte faute de travail. L'armement
        # depuis un worker n'est pas garanti d'être délivré immédiatement — il
        # le sera au premier réveil de la boucle — mais tant qu'un run produit
        # du trafic régulier (fragments, jauge), la pompe reste vivante.
        if not self._pumping:
            self._arm_pump_from_worker()
        return True

    def call(self, fn, timeout: float = 30.0):
        """Exécute fn sur le thread principal et rend son résultat.

        À appeler depuis le worker. Lève DispatcherClosed si la palette est
        fermée, DispatcherTimeout au-delà du délai, et propage telle quelle
        l'exception levée par fn.
        """
        if self._closed:
            raise DispatcherClosed("palette fermée")

        callback = self._async_callback()
        if callback is None:
            return fn()

        if is_main_thread():
            return fn()

        result_queue = queue.Queue(maxsize=1)

        def _run_and_report():
            try:
                result_queue.put(("ok", fn()))
            except Exception as exc:      # relayée à l'appelant
                result_queue.put(("error", exc))

        self._queue.put(_run_and_report)
        try:
            status, payload = result_queue.get(timeout=timeout)
        except queue.Empty:
            raise DispatcherTimeout(
                f"le thread principal n'a pas répondu en {timeout:g} s "
                "(pompe arrêtée, ou boîte de dialogue modale ouverte ?)"
            ) from None
        if status == "error":
            raise payload
        return payload

    def start_pump(self):
        """Démarre le drain de la file. À APPELER DEPUIS LE THREAD PRINCIPAL.

        La pompe s'exécute sur le thread principal, vide la file, puis se
        RÉARME elle-même via AsyncCallback. Le réarmement partant du thread
        principal, il est délivré de façon fiable — contrairement à un
        addCallback émis depuis un worker, qui attend le prochain réveil de la
        boucle. Elle ne tourne que pendant un run : `stop_pump()` l'éteint.
        """
        if self._pumping or self._closed:
            return
        self._pumping = True
        self._arm_pump()

    def stop_pump(self):
        """Arrête la pompe après un dernier drain."""
        self._pumping = False
        self.drain()

    def drain(self):
        """Exécute les tâches en attente. Thread principal uniquement."""
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                pass          # une mise à jour d'affichage ratée n'est pas fatale

    def _arm_pump_from_worker(self):
        """Tentative d'armement depuis un thread de fond (best effort)."""
        self._pumping = True
        try:
            self._arm_pump()
        except Exception:
            self._pumping = False

    def _arm_pump(self):
        callback = self._async_callback()
        if callback is None:
            self._pumping = False
            self.drain()
            return
        task = _Task(self._pump_once)
        self._pump = task           # référence vivante jusqu'au notify
        try:
            callback.addCallback(task, None)
        except Exception as exc:
            self._note(f"pompe : réarmement impossible ({exc})")
            self._pumping = False

    def _pump_once(self):
        """Un tour de pompe : drainer, puis se réarmer S'IL RESTE DU TRAVAIL.

        Se réarmer inconditionnellement monopolise la boucle d'événements :
        LibreOffice passe son temps à traiter des tours de pompe à vide et ne
        répond plus à la souris ni au clavier. La pompe s'éteint donc dès que
        la file est vide ; c'est `post()` qui la relance à la tâche suivante.
        """
        self.drain()
        if self._closed or self._queue.empty():
            self._pumping = False
            return
        self._arm_pump()

    def _async_callback(self):
        """Rend le service AsyncCallback, créé une seule fois et CONSERVÉ.

        Le garder est nécessaire, pas seulement économique : un service créé
        en variable locale perd sa dernière référence dès le retour de `post()`
        et peut disparaître **avant d'avoir délivré** l'événement. Le symptôme
        est déroutant — une mise à jour d'affichage sur deux se perd, par
        exemple un bouton qui reste sur « Arrêter » après la fin du run.

        On ne teste PAS la disponibilité de l'interface XCallback : hors
        LibreOffice elle n'existe pas, mais le service peut être simulé. Le
        seul juge fiable est la création du service elle-même.
        """
        if self._callback_service is not None:
            return self._callback_service
        if self.uno_ctx is None:
            return None
        try:
            self._callback_service = (
                self.uno_ctx.getServiceManager().createInstanceWithContext(
                    "com.sun.star.awt.AsyncCallback", self.uno_ctx))
            return self._callback_service
        except Exception as exc:
            self._note(f"AsyncCallback indisponible ({exc})")
            return None

    def _note(self, message):
        if self._log is not None:
            try:
                self._log(f"[ui-thread] {message}")
            except Exception:
                pass


class DirectDispatcher:
    """Dispatcher synchrone pour les tests : exécute tout sur place. Même interface publique."""

    def __init__(self, log=None):
        self._log = log
        self._closed = False

    def close(self):
        self._closed = True

    def post(self, fn) -> bool:
        if self._closed:
            return False
        try:
            fn()
        except Exception:
            pass
        return True

    def call(self, fn, timeout: float = 30.0):
        if self._closed:
            raise DispatcherClosed("dispatcher fermé")
        return fn()

    def start_pump(self):
        pass          # exécution immédiate : rien à pomper

    def stop_pump(self):
        pass

    def drain(self):
        pass


def is_main_thread() -> bool:
    """Vrai si l'appelant est le thread principal du processus.

    Sert aux garde-fous : une fonction qui doit rester sur le thread principal
    peut le vérifier au lieu de l'espérer.
    """
    return threading.current_thread() is threading.main_thread()
