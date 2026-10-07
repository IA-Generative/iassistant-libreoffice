"""Jauge d'activité : montrer que ça travaille, et à quoi.

Sur une opération longue, une interface qui ne bouge pas est indiscernable
d'une interface plantée. Ce module produit la matière de la jauge — phase en
cours, nombre de jetons, durée — sans rien connaître d'UNO : la palette se
contente de l'afficher.

Comptage des jetons : estimation locale (caractères ÷ 4, contrainte no-pip),
**remplacée par la valeur exacte** si le relais envoie spontanément un bloc
`usage`. On ne le réclame jamais — ajouter `stream_options` au corps de la
requête fait rejeter celle-ci par certains relais.
"""

from __future__ import annotations

import threading
import time

# Cycle braille : rendu fiable dans les contrôles UNO et largeur stable, donc
# aucun tremblement de mise en page d'une image à l'autre.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

CHARS_PER_TOKEN = 4        # approximation usuelle, suffisante pour une jauge

# Le raisonnement est conservé pour l'infobulle de survol : on garde la FIN,
# c'est là que se trouve l'état d'avancement de la pensée du modèle. Plafonné,
# car une infobulle de plusieurs milliers de caractères est illisible et
# certains toolkits la tronquent brutalement.
REASONING_TOOLTIP_CHARS = 900


class NullProgress:
    """Jauge inerte — utilisée hors interface (tests, appels programmatiques)."""

    def on_text(self, _text):
        pass

    def on_reasoning(self, _text):
        pass

    @property
    def tooltip(self):
        return ""

    def exact_tokens(self, _count):
        pass

    def set_phase(self, _phase):
        pass


class RunProgress:
    """État d'avancement d'un run, alimenté depuis le thread worker.

    Thread-safe : le worker écrit, le thread principal lit pour afficher.
    """

    def __init__(self, now=time.monotonic, activity=None):
        """`activity` nomme une tâche de fond et TIENT d'un bout à l'autre.

        Les phases automatiques (« Rédaction », « Réflexion ») sont posées par
        `on_text`/`on_reasoning` dès que les jetons arrivent. Pour un run c'est
        ce qu'on veut : l'utilisateur a lancé l'action et suit son déroulé. Pour
        une tâche partie d'un simple clic d'onglet — l'analyse du document —
        elles effaceraient la seule information utile, et la ligne d'état
        deviendrait indiscernable d'un run que l'utilisateur n'a pas demandé.
        """
        self._now = now
        self._lock = threading.Lock()
        self._started = now()
        self._frame = 0
        self._chars = 0
        self._reasoning_chars = 0
        self._reasoning = ""
        self._preview = ""
        self._exact = None
        self._activity = activity
        self._phase = activity or "Connexion…"

    def on_text(self, text):
        with self._lock:
            chunk = text or ""
            self._chars += len(chunk)
            # Tous les modèles n'émettent pas de `reasoning_content` — la
            # plupart n'exposent que le texte. L'infobulle montre donc AUSSI ce
            # qui s'écrit : sans cela, elle resterait vide sur ces modèles-là et
            # l'indice de survol ne s'afficherait jamais.
            self._preview = (self._preview + chunk)[-REASONING_TOOLTIP_CHARS:]
            if self._activity is None:
                self._phase = "Rédaction"

    def on_reasoning(self, text):
        with self._lock:
            chunk = text or ""
            self._reasoning_chars += len(chunk)
            # On ne garde que la fin : c'est l'état courant de la réflexion.
            self._reasoning = (self._reasoning + chunk)[-REASONING_TOOLTIP_CHARS:]
            if self._activity is None:
                self._phase = "Réflexion"

    def exact_tokens(self, count):
        """Valeur faisant autorité, transmise par le relais."""
        with self._lock:
            self._exact = int(count)

    def set_phase(self, phase):
        with self._lock:
            if self._activity is None:
                self._phase = phase

    @property
    def tokens(self):
        with self._lock:
            if self._exact is not None:
                return self._exact
            return (self._chars + self._reasoning_chars) // CHARS_PER_TOKEN

    @property
    def is_exact(self):
        with self._lock:
            return self._exact is not None

    @property
    def tooltip(self):
        """Contenu de l'infobulle : le raisonnement s'il existe, sinon le texte.

        Préfixé de sa nature, pour que l'utilisateur sache s'il regarde la
        réflexion du modèle ou le texte qu'il est en train d'écrire.
        """
        with self._lock:
            if self._reasoning:
                return "Réflexion du modèle :\n\n" + self._reasoning
            if self._preview:
                return "Texte en cours :\n\n" + self._preview
            return ""

    @property
    def phase(self):
        with self._lock:
            return self._phase

    @property
    def elapsed(self):
        return self._now() - self._started

    def spin(self):
        """Avance le rotor d'un cran et rend son image courante."""
        with self._lock:
            frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
            self._frame += 1
        return frame

    def render(self, spin=True):
        """Ligne d'état compacte : « ⠹ Rédaction · 1 240 tk · 12 s ».

        Le nombre de jetons est suivi de « ~ » tant qu'il est estimé, pour ne
        pas faire passer une approximation pour une mesure.
        """
        frame = self.spin() if spin else SPINNER_FRAMES[0]
        tokens = self.tokens
        mark = "" if self.is_exact else "~"
        parts = [frame, self.phase]
        if tokens:
            parts.append(f"{mark}{tokens:,} tk".replace(",", " "))
        parts.append(f"{int(self.elapsed)} s")
        return " · ".join([parts[0] + " " + parts[1], *parts[2:]])
