"""État du processus de la coquille.

LibreOffice réinstancie MainJob à chaque action : les drapeaux, verrous et
références qui doivent survivre d'une action à l'autre vivent ici, dans un
objet unique par processus, et non sur la classe MainJob.
"""

import threading

from .. import credentials, local_config, log_setup


class ShellRuntime:
    def __init__(self):
        self.enrollment_dismissed = False
        self.enrollment_wizard_active = False
        self.enrollment_wizard_lock = threading.Lock()

        self.update_in_progress = False
        self.update_lock = threading.Lock()
        # Versions cibles dont l'installation automatique n'a pas abouti : on ne
        # les retélécharge ni ne les repropose en boucle.
        self.update_launch_blocked = set()
        # Réconciliation de la mise à jour précédente après redémarrage : une
        # seule fois par processus.
        self.update_reconcile_started = False
        self.self_update_in_flight = False

        # Diagnostic passif du feed natif : une seule fois par processus.
        self.feed_check_started = False
        # Réécriture de l'adresse du feed dans description.xml : planifiée une
        # fois par processus au démarrage ; verrou partagé avec les réécritures
        # déclenchées par /config.
        self.feed_rewrite_started = False
        self.feed_rewrite_lock = threading.Lock()
        self.feed_rewrite_last = None           # dernière version inscrite avec succès
        self.feed_rewrite_last_result = None    # (résultat, version) du dernier appel

        # Préparation du stockage local (journal, migration, empreinte) :
        # retentée par chaque MainJob jusqu'à ce qu'elle réussisse.
        self.storage_ready = False
        self.storage_lock = threading.Lock()
        # Secrets de settings.json recopiés dans le coffre une seule fois : si
        # leur retrait de settings.json échoue, la reprise écraserait un secret
        # renouvelé depuis.
        self.secrets_copied = False
        self.uninstall_listener = None
        self.wiped = False

        # Une action non gérée est signalée une fois par nom et par session :
        # un utilisateur qui reclique sur une entrée de menu morte ne doit pas
        # produire une rafale de traces.
        self.unhandled_reported = set()


_current = ShellRuntime()


def get():
    return _current


def reset_for_tests():
    """Repart d'un processus neuf : état de la coquille, journal et gel des
    modules communs."""
    global _current
    log_setup.uninstall()
    _current = ShellRuntime()
    local_config.unfreeze_for_tests()
    credentials.unfreeze_for_tests()
    log_setup.unfreeze_for_tests()
