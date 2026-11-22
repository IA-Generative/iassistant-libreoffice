"""État du processus de la coquille.

LibreOffice réinstancie MainJob à chaque action : les drapeaux, verrous et
références qui doivent survivre d'une action à l'autre vivent ici, dans un
objet unique par processus, et non sur la classe MainJob.
"""

import threading

from .. import credentials, local_config, log_setup

# Un enrôlement de fond rend la main en une ou deux secondes (refresh Keycloak,
# POST /enroll) ; au-delà, on le laisse finir sans l'attendre.
ENROLLMENT_WAIT_SECONDS = 10


class ShellRuntime:
    def __init__(self):
        self.enrollment_dismissed = False
        self.enrollment_wizard_active = False
        self.enrollment_wizard_lock = threading.Lock()
        # Le DM fait tourner la paire relais à chaque POST /enroll : deux enrôlements
        # concurrents laisseraient le poste avec une paire révoquée. Un seul à la
        # fois dans le processus ; le compteur dit à celui qui a attendu qu'un
        # autre a abouti entre-temps.
        self.enrollment_lock = threading.Lock()
        self.enrollment_generation = 0
        # Le SSO fait tourner le jeton de renouvellement : un seul échange à la
        # fois (shell/token_refresh.py).
        self.token_refresh_lock = threading.Lock()
        # Bases DM (normalisées) qui n'ont répondu à aucune récupération de config
        # dans ce processus : essayées en dernier et ignorées par la passation,
        # sinon chaque récupération paierait leur délai ; une base redevient saine
        # dès qu'elle répond.
        self.unreachable_dm_bases = set()

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
        self.terminate_listener = None
        # Sans changement notifié par le Gestionnaire des extensions pendant la
        # session, la fermeture de LibreOffice n'a rien à vérifier.
        self.extensions_changed = False
        # Une minuterie et la fermeture de LibreOffice peuvent conclure en même
        # temps : l'effacement se réclame sous ce verrou, une seule fois.
        self.wiped = False
        self.wipe_lock = threading.Lock()

        # Une action non gérée est signalée une fois par nom et par session :
        # un utilisateur qui reclique sur une entrée de menu morte ne doit pas
        # produire une rafale de traces.
        self.unhandled_reported = set()
        # Anti-tempête LlmRelayError : heure du dernier envoi par code d'erreur.
        self.llm_error_last_sent = {}

    def enrollment_settled(self):
        """Attend la fin de l'enrôlement en cours, au plus ENROLLMENT_WAIT_SECONDS ;
        faux s'il tourne encore."""
        if not self.enrollment_lock.acquire(timeout=ENROLLMENT_WAIT_SECONDS):
            return False
        self.enrollment_lock.release()
        return True


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
