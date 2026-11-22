"""File d'envoi vers Éloignement : rien n'est perdu sans que l'agent le voie.

Ctrl+S enregistre d'abord le fichier sur le poste (LibreOffice, comme
d'habitude). Le plugin en fige une copie ici, puis l'envoie. Si Éloignement ne
répond pas, l'envoi reste en attente et repart plus tard ; s'il refuse parce
que le document a changé de son côté (412), l'entrée passe en conflit et
attend le choix de l'agent ; jamais d'écrasement.

Une entrée par document : un nouvel enregistrement remplace l'envoi en attente
(la dernière version gagne). Une entrée n'est retirée qu'après un succès.

Stockage : `<dossier>/file_envoi.json` et `<dossier>/attente/<clé>.odt`,
écrits de façon atomique. Pas d'UNO ici : ce module tourne dans un thread de
travail.
"""

import dataclasses
import hashlib
import json
import os
import tempfile
import threading
import time

from .api import EloignementError, server_version

WAITING = "attente"
CONFLICT = "conflit"
REFUSED = "refuse"

FIRST_DELAY_S = 30
MAX_DELAY_S = 600


@dataclasses.dataclass
class Entry:
    key: str                 # identifiant de l'acte, ou clé locale avant création
    dossier: str
    document: str = ""       # vide : l'acte n'existe pas encore dans Éloignement
    version: str = ""        # version de base envoyée en If-Match ("" = sans contrôle)
    nom: str = ""
    snapshot: str = ""       # copie figée du fichier enregistré
    state: str = WAITING
    attempts: int = 0
    next_at: float = 0.0
    message: str = ""
    server_version: str = ""  # version courante annoncée par la 412
    modele: str = ""          # modèle d'origine, pour un premier versement


def entry_key(dossier, document, location=""):
    """Clé stable d'un document : l'acte s'il existe, sinon dossier + emplacement."""
    if document:
        return document
    digest = hashlib.sha256(str(location or "").encode("utf-8")).hexdigest()[:16]
    return f"nouveau-{dossier}-{digest}"


def retry_delay(attempts, retry_after=None):
    if retry_after is not None:
        return min(max(int(retry_after), 1), MAX_DELAY_S)
    return min(FIRST_DELAY_S * (2 ** max(attempts - 1, 0)), MAX_DELAY_S)


def atomic_write(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Outbox:
    def __init__(self, directory, clock=time.time):
        self.directory = directory
        self._clock = clock
        self._lock = threading.RLock()
        self._path = os.path.join(directory, "file_envoi.json")
        self._entries = self._load()

    # -- persistance -----------------------------------------------------------

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return {}
        except Exception:
            # Fichier abîmé : on le met de côté au lieu de l'écraser, les
            # copies figées restent dans attente/.
            try:
                os.replace(self._path, self._path + ".illisible")
            except OSError:
                pass
            return {}
        entries = {}
        fields = {f.name for f in dataclasses.fields(Entry)}
        for item in raw.get("entries", []) if isinstance(raw, dict) else []:
            if isinstance(item, dict) and item.get("key") and item.get("dossier"):
                entries[item["key"]] = Entry(**{k: v for k, v in item.items() if k in fields})
        return entries

    def _save(self):
        data = {"entries": [dataclasses.asdict(e) for e in self._entries.values()]}
        atomic_write(self._path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))

    # -- lecture ----------------------------------------------------------------

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            return dataclasses.replace(entry) if entry else None

    def entries(self):
        with self._lock:
            return [dataclasses.replace(e) for e in self._entries.values()]

    def due(self, now=None):
        now = self._clock() if now is None else now
        with self._lock:
            return [dataclasses.replace(e) for e in self._entries.values()
                    if e.state == WAITING and e.next_at <= now]

    def next_wakeup(self):
        """Prochaine échéance d'un envoi en attente, ou None."""
        with self._lock:
            times = [e.next_at for e in self._entries.values() if e.state == WAITING]
        return min(times) if times else None

    # -- transitions --------------------------------------------------------------

    def put(self, key, dossier, document, version, nom, source_path, modele=""):
        """Fige le fichier enregistré et (re)met l'envoi en attente."""
        with open(source_path, "rb") as fh:
            data = fh.read()
        with self._lock:
            snapshot = os.path.join(self.directory, "attente", f"{key}.odt")
            atomic_write(snapshot, data)
            previous = self._entries.get(key)
            # Un conflit non tranché garde sa version de base : le nouvel
            # enregistrement ne doit pas le faire disparaître en silence.
            state = CONFLICT if previous and previous.state == CONFLICT else WAITING
            self._entries[key] = Entry(
                key=key, dossier=dossier, document=document, version=version,
                nom=nom, snapshot=snapshot, state=state, attempts=0, modele=modele,
                next_at=self._clock(),
                message=previous.message if state == CONFLICT else "")
            self._save()
            return dataclasses.replace(self._entries[key])

    def sent(self, key):
        with self._lock:
            entry = self._entries.pop(key, None)
            self._save()
        if entry and entry.snapshot:
            try:
                os.unlink(entry.snapshot)
            except OSError:
                pass

    def retry(self, key, message, retry_after=None):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry.attempts += 1
            entry.next_at = self._clock() + retry_delay(entry.attempts, retry_after)
            entry.message = message
            entry.state = WAITING
            self._save()
            return dataclasses.replace(entry)

    def conflict(self, key, message, current_version=""):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry.state = CONFLICT
            entry.message = message
            entry.server_version = current_version or entry.server_version
            self._save()
            return dataclasses.replace(entry)

    def refused(self, key, message):
        return self._set_state(key, REFUSED, message)

    def keep_mine(self, key, base_version=""):
        """« Garder ma version comme nouvelle version » : renvoi sur la version
        courante du dossier (celle de la 412, ou celle donnée)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry.version = base_version or entry.server_version or entry.version
            entry.state = WAITING
            entry.attempts = 0
            entry.next_at = self._clock()
            entry.message = ""
            self._save()
            return dataclasses.replace(entry)

    def drop(self, key):
        """Abandonne l'envoi (l'agent garde son fichier local)."""
        self.sent(key)

    def _set_state(self, key, state, message):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry.state = state
            entry.message = message
            self._save()
            return dataclasses.replace(entry)


@dataclasses.dataclass
class Outcome:
    key: str
    state: str          # "envoye" | WAITING | CONFLICT | REFUSED
    message: str = ""
    document: str = ""
    version: str = ""
    nom: str = ""


SENT = "envoye"



def send_one(outbox, client, entry):
    """Envoie une entrée et range le résultat. Ne lève pas."""
    try:
        with open(entry.snapshot, "rb") as fh:
            data = fh.read()
    except OSError:
        outbox.refused(entry.key, "copie locale introuvable : réenregistrer le document")
        return Outcome(entry.key, REFUSED, "copie locale introuvable")
    try:
        if entry.document:
            result = client.verser(entry.dossier, entry.document, data, entry.version, entry.nom)
        else:
            # Premier versement d'un document rempli dans Writer. La clé
            # d'idempotence suit le contenu : un renvoi après coupure ne crée
            # pas un second acte.
            digest = hashlib.sha256(data).hexdigest()[:24]
            result = client.creer_acte(entry.dossier, data, entry.nom, entry.modele,
                                       idempotency_key=f"fichier:{entry.key}:{digest}")
    except EloignementError as exc:
        if exc.status == 412:
            outbox.conflict(entry.key, exc.message, server_version(exc))
            return Outcome(entry.key, CONFLICT, exc.message)
        if exc.transient:
            outbox.retry(entry.key, exc.message, exc.retry_after)
            return Outcome(entry.key, WAITING, exc.message)
        outbox.refused(entry.key, exc.message)
        return Outcome(entry.key, REFUSED, exc.message)
    except ValueError as exc:
        outbox.refused(entry.key, str(exc))
        return Outcome(entry.key, REFUSED, str(exc))
    result = result if isinstance(result, dict) else {}
    outbox.sent(entry.key)
    return Outcome(entry.key, SENT, "",
                   document=str(result.get("id") or entry.document or ""),
                   version=str(result.get("version") or ""),
                   nom=str(result.get("nom") or entry.nom or ""))


def send_due(outbox, client, now=None):
    return [send_one(outbox, client, entry) for entry in outbox.due(now)]
