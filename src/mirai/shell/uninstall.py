"""Effacement des données de l'extension à sa désinstallation depuis le
Gestionnaire des extensions.

entrypoint importe ce module à son chargement, sur le thread principal : un
import UNO paresseux échouerait hors de ce thread.
"""

import logging
import threading

import unohelper

from .. import credentials, local_config, log_setup

try:
    from com.sun.star.util import XModifyListener as _XMODIFY_LISTENER_IFACE
except Exception:
    _XMODIFY_LISTENER_IFACE = None
try:
    from com.sun.star.frame import XTerminateListener as _XTERMINATE_LISTENER_IFACE
except Exception:
    _XTERMINATE_LISTENER_IFACE = None

_UNINSTALL_CHECK_DELAY_SECONDS = 3.0
_DEPLOYMENT_REPOSITORIES = ("user", "shared", "bundled")
_EXTENSION_MANAGER = "/singletons/com.sun.star.deployment.ExtensionManager"

if _XMODIFY_LISTENER_IFACE is not None:
    class MirAIUninstallListener(unohelper.Base, _XMODIFY_LISTENER_IFACE):
        """Notifié à chaque changement du Gestionnaire des extensions : LibreOffice
        n'offre aucun crochet de désinstallation, seule cette notification, sans
        détail, arrive (dans le processus qui désinstalle)."""

        def __init__(self, on_modified):
            self._on_modified = on_modified

        def modified(self, _event):
            try:
                self._on_modified()
            except Exception as exc:
                logging.info(f"[désinstallation] {exc}")

        def disposing(self, _event):
            return
else:
    MirAIUninstallListener = None

if _XTERMINATE_LISTENER_IFACE is not None:
    class MirAITerminateListener(unohelper.Base, _XTERMINATE_LISTENER_IFACE):
        """Notifié quand LibreOffice se ferme ou redémarre, sur le thread qui
        demande la fermeture : le principal en général, celui de la mise à jour
        quand elle ferme LibreOffice sous Windows. Ne s'oppose jamais à la
        fermeture."""

        def __init__(self, on_termination):
            self._on_termination = on_termination

        def queryTermination(self, _event):
            return

        def notifyTermination(self, _event):
            try:
                self._on_termination()
            except Exception as exc:
                logging.info(f"[désinstallation] {exc}")

        def disposing(self, _event):
            return
else:
    MirAITerminateListener = None


class UninstallWatch:
    """Guette la désinstallation de l'extension `identifier` et efface alors ce
    qu'elle a écrit sous `user_config_dir`. L'écoute et l'effacement valent pour
    le processus : leur état vit dans `runtime`."""

    def __init__(self, ctx, runtime, identifier, user_config_dir):
        self._ctx = ctx
        self._runtime = runtime
        self._identifier = identifier
        self._user_config_dir = user_config_dir

    def register(self, desktop):
        """Une fois par processus : écoute du Gestionnaire des extensions et de
        la fermeture de LibreOffice (`desktop`)."""
        self._listen_to_extension_manager()
        self._listen_to_termination(desktop)

    def _listen_to_extension_manager(self):
        if self._runtime.uninstall_listener is not None:
            return
        if MirAIUninstallListener is None:
            logging.info("[désinstallation] écoute non branchée : XModifyListener indisponible")
            return
        manager = self._ctx.getValueByName(_EXTENSION_MANAGER)
        if manager is None:
            logging.info("[désinstallation] écoute non branchée : "
                         "gestionnaire des extensions introuvable")
            return
        listener = MirAIUninstallListener(self.schedule_check)
        manager.addModifyListener(listener)
        self._runtime.uninstall_listener = listener

    def _listen_to_termination(self, desktop):
        if self._runtime.terminate_listener is not None:
            return
        if MirAITerminateListener is None:
            logging.info("[désinstallation] fermeture non écoutée : "
                         "XTerminateListener indisponible")
            return
        listener = MirAITerminateListener(self._on_termination)
        desktop.addTerminateListener(listener)
        self._runtime.terminate_listener = listener

    def schedule_check(self):
        self._runtime.extensions_changed = True
        # Différé : la base des extensions doit être stabilisée (remplacement en
        # cours, mise à jour native) avant de conclure à une désinstallation.
        timer = threading.Timer(_UNINSTALL_CHECK_DELAY_SECONDS, self._check)
        timer.daemon = True
        timer.start()

    def still_deployed(self):
        """True si l'extension figure encore dans un dépôt ; True aussi dans le
        doute (API en échec) : on n'efface jamais sur une absence d'information.
        Une extension désactivée reste listée."""
        try:
            manager = self._ctx.getValueByName(_EXTENSION_MANAGER)
            for repository in _DEPLOYMENT_REPOSITORIES:
                packages = manager.getDeployedExtensions(
                    repository, manager.createAbortChannel(), None)
                for package in packages or ():
                    identifier = package.getIdentifier()
                    if getattr(identifier, "Value", identifier) == self._identifier:
                        return True
        except Exception as exc:
            logging.info(f"[désinstallation] état du déploiement illisible : {exc}")
            return True
        return False

    def _check(self):
        try:
            if self._runtime.self_update_in_flight or self._runtime.wiped:
                return
            if self.still_deployed():
                return
            # Un remplacement (addPackage) efface l'ancienne entrée avant d'insérer
            # la nouvelle : une seule absence ne prouve rien.
            timer = threading.Timer(_UNINSTALL_CHECK_DELAY_SECONDS, self._confirm)
            timer.daemon = True
            timer.start()
        except Exception as exc:
            logging.info(f"[désinstallation] contrôle impossible : {exc}")

    def _confirm(self):
        try:
            if self._runtime.self_update_in_flight or self._runtime.wiped:
                return
            if self.still_deployed():
                return
            self.wipe("désinstallation depuis le Gestionnaire des extensions")
        except Exception as exc:
            logging.info(f"[désinstallation] effacement impossible : {exc}")

    def _on_termination(self):
        # Les minuteries meurent avec le processus : une fermeture ou un
        # redémarrage pendant la confirmation différée perdrait l'effacement.
        # Le Gestionnaire des extensions refuse la fermeture tant qu'il
        # travaille : un remplacement est terminé ici, un seul constat suffit.
        if not self._runtime.extensions_changed or self._runtime.self_update_in_flight:
            return
        if self._wiped() or self.still_deployed():
            return
        self.wipe("désinstallation constatée à la fermeture de LibreOffice")

    def _wiped(self):
        # Sous le verrou : la fermeture attend la fin d'un effacement lancé par
        # une minuterie au lieu de laisser LibreOffice s'arrêter au milieu.
        with self._runtime.wipe_lock:
            return self._runtime.wiped

    def wipe(self, reason):
        with self._runtime.wipe_lock:
            if self._runtime.wiped:
                return
            self._runtime.wiped = True
            logging.info(f"[désinstallation] {reason} : effacement des données de l'extension")
            credentials.freeze()
            local_config.freeze()
            log_setup.uninstall()
            log_setup.freeze()
            credentials.wipe()
            local_config.wipe(self._user_config_dir)
