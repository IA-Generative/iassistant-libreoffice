"""Session Éloignement du processus : documents liés, enregistrement, file d'envoi.

Une seule session par processus LibreOffice. Elle tient :

- le client du relais (`api.Client`) et la file d'envoi (`outbox.Outbox`) ;
- l'écouteur d'enregistrement posé sur chaque document LIÉ, et seulement sur
  eux : un document non lié s'enregistre exactement comme d'habitude ;
- le thread qui envoie la file, sans UNO ; ses résultats repassent par le
  dispatcher sur le thread principal.

L'interface (volet, confirmations) se branche par `session.ui` : le moteur ne
l'importe jamais.
"""

import dataclasses
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from ..ui_thread import MainThreadDispatcher
from . import api
from . import link as link_mod
from . import outbox as outbox_mod
from . import snapshot as snapshot_mod

try:
    import unohelper
    from com.sun.star.document import XDocumentEventListener
    _LISTENER_BASES = ((unohelper.Base, XDocumentEventListener)
                       if isinstance(XDocumentEventListener, type)
                       and isinstance(unohelper.Base, type) else ())
except ImportError:  # hors LibreOffice (tests)
    _LISTENER_BASES = ()

FEATURE = "eloignement"
DISCOVERY_MAX_AGE_S = 300
MAX_SLEEP_S = 600

DISABLED = "Éloignement est désactivé sur ce poste."
NOT_OFFERED = "Éloignement n'est pas proposé par le device management de ce poste."
NOT_SIGNED_IN = ("Session MirAI indisponible : reconnectez-vous depuis le menu « 🤖 MIrAI », "
                 "puis recommencez.")
UNREACHABLE = "Device management injoignable pour le moment : réessayez dans un instant."

_session = [None]


def get_session(shell):
    """La session du processus, créée au premier usage."""
    session = _session[0]
    if session is None:
        session = Session(shell)
        _session[0] = session
    return session


def reset_for_tests():
    _session[0] = None


# -- fichiers -----------------------------------------------------------------------

def file_url(path):
    return urllib.parse.urljoin("file:", urllib.request.pathname2url(os.path.abspath(path)))


def file_path(url):
    url = str(url or "")
    if not url.startswith("file:"):
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in ("", "localhost"):
        return ""
    return urllib.request.url2pathname(parsed.path)


def safe_filename(name, default="acte"):
    """Nom de fichier sans séparateur ni caractère réservé, extension .odt."""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    if not base:
        base = default
    if not base.lower().endswith(".odt"):
        base += ".odt"
    return base[:150]


def unique_path(directory, name):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    stem, ext = os.path.splitext(name)
    counter = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem} ({counter}){ext}")
        counter += 1
    return path


class KnownLinks:
    """Liens créés sur ce poste. Un document lié reçu d'ailleurs (pièce jointe,
    clé USB) n'envoie rien sans confirmation de l'agent : sinon un fichier
    piégé verserait en silence dans le dossier qu'il désigne."""

    def __init__(self, directory):
        self._path = os.path.join(directory, "liens_connus.json")
        self._lock = threading.Lock()
        try:
            with open(self._path, encoding="utf-8") as fh:
                self._items = set(json.load(fh))
        except Exception:
            self._items = set()

    def contains(self, dossier, key):
        with self._lock:
            return f"{dossier}:{key}" in self._items

    def add(self, dossier, key):
        with self._lock:
            self._items.add(f"{dossier}:{key}")
            data = json.dumps(sorted(self._items)).encode("utf-8")
        outbox_mod.atomic_write(self._path, data)


@dataclasses.dataclass
class DocState:
    model: object
    listener: object = None
    ignore_next_save: bool = False
    key: str = ""
    status: str = ""            # dernier état affiché
    tone: str = "neutral"       # neutral | success | warning | error
    empty: list = dataclasses.field(default_factory=list)    # balises vides
    labels: dict = dataclasses.field(default_factory=dict)   # balise → libellé
    header: dict = dataclasses.field(default_factory=dict)   # en-tête du dossier
    modele_nom: str = ""


class _SaveListener(*_LISTENER_BASES):
    """Écouteur posé sur un document lié : réagit à SON enregistrement."""

    def __init__(self, session, model):
        self._session = session
        self._model = model

    def documentEventOccured(self, event):
        name = str(getattr(event, "EventName", "") or "")
        try:
            if name in ("OnSaveDone", "OnSaveAsDone"):
                self._session.on_saved(self._model)
            elif name == "OnUnload":
                self._session.forget(self._model)
        except Exception as exc:
            self._session.log(f"[eloignement] écouteur : {exc}")

    def disposing(self, _source):
        self._session.forget(self._model)


class Session:
    def __init__(self, shell):
        self.shell = shell
        root = os.path.join(shell.data_dir(), "eloignement")
        self.root = root
        self.workdir = os.path.join(root, "actes")
        self.client = api.Client(shell.tools_request)
        self.outbox = outbox_mod.Outbox(root)
        self.known = KnownLinks(root)
        self.dispatcher = MainThreadDispatcher(shell.uno_ctx, log=shell.log)
        self.ui = None                     # branché par ui/eloignement.py
        self._docs = {}                    # id(model) → DocState
        self._discovery = (0.0, False)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._worker = None

    def log(self, message):
        self.shell.log(message)

    # -- activation -------------------------------------------------------------

    def enabled(self):
        try:
            return bool(self.shell.feature_enabled(FEATURE))
        except Exception:
            return False

    def discover(self):
        """Vérifie que le relais annonce Éloignement ; réseau : thread de travail.

        Lève une EloignementError qui dit la vraie cause : session MirAI
        absente, device management injoignable, ou service non proposé. Seul
        un succès est gardé en cache : un échec passager se réessaie au
        geste suivant."""
        if time.time() - self._discovery[0] < DISCOVERY_MAX_AGE_S and self._discovery[1]:
            return
        try:
            status, _headers, body = self.shell.tools_request(
                "GET", "", None, {"Accept": "application/json"}, 10)
        except PermissionError as exc:
            self.log(f"[eloignement] découverte impossible : {exc}")
            raise api.EloignementError(401, NOT_SIGNED_IN) from exc
        except Exception as exc:
            self.log(f"[eloignement] découverte impossible : {exc}")
            raise api.EloignementError(0, UNREACHABLE) from exc
        status = int(status)
        if status in (401, 403):
            raise api.EloignementError(status, NOT_SIGNED_IN)
        if not 200 <= status < 300:
            raise api.EloignementError(status, UNREACHABLE)
        try:
            announced = api.announces(json.loads(body or b"{}"))
        except ValueError:
            announced = False
        if not announced:
            raise api.EloignementError(status, NOT_OFFERED)
        self._discovery = (time.time(), True)

    def require(self):
        """Lève une EloignementError lisible si la fonction n'est pas utilisable."""
        if not self.enabled():
            raise api.EloignementError(0, DISABLED)
        self.discover()

    # -- documents liés ------------------------------------------------------------

    def state(self, model):
        return self._docs.get(id(model))

    def attach(self, model):
        """Pose l'écouteur d'enregistrement sur un document lié (une fois)."""
        state = self._docs.get(id(model))
        if state is not None:
            return state
        state = DocState(model=model)
        listener = _SaveListener(self, model)
        try:
            model.addDocumentEventListener(listener)
            state.listener = listener
        except Exception as exc:
            self.log(f"[eloignement] écouteur impossible : {exc}")
        self._docs[id(model)] = state
        return state

    def forget(self, model):
        state = self._docs.pop(id(model), None)
        if state is None:
            return
        if state.listener is not None:
            try:
                model.removeDocumentEventListener(state.listener)
            except Exception:
                pass
        if self.ui is not None:
            try:
                self.ui.document_closed(model)
            except Exception:
                pass

    def key_for(self, model, link):
        try:
            location = model.getLocation()
        except Exception:
            location = ""
        return outbox_mod.entry_key(link.dossier, link.document, location)

    def on_loaded(self, model):
        """Un document vient d'être ouvert : s'il est lié, on le suit."""
        link = link_mod.read_link(model)
        if link is None:
            return False
        self.attach(model)
        if self.ui is not None:
            try:
                self.ui.show_panel(model)
            except Exception as exc:
                self.log(f"[eloignement] volet non affiché : {exc}")
        self.kick()
        return True

    def on_saved(self, model):
        """Ctrl+S (ou Enregistrer sous) a abouti sur un document lié."""
        state = self.attach(model)
        if state.ignore_next_save:
            state.ignore_next_save = False
            return None
        link = link_mod.read_link(model)
        if link is None:
            return None
        path = file_path(model.getLocation())
        if not path or not os.path.isfile(path):
            return None
        key = self.key_for(model, link)
        state.key = key
        if not self.known.contains(link.dossier, key):
            confirm = getattr(self.ui, "confirm_unknown_link", None)
            if confirm is None or not confirm(model, link):
                self.set_status(model, "Enregistré sur le poste seulement : envoi non confirmé.",
                                "warning")
                return None
            self.known.add(link.dossier, key)
        entry = self.outbox.put(key, link.dossier, link.document, link.version,
                                os.path.basename(path), path, modele=link.modele)
        if not self.enabled():
            self.set_status(model, "En attente d'envoi : " + DISABLED, "warning")
            return entry
        self.set_status(model, "En attente d'envoi…", "warning")
        self.kick()
        return entry

    # -- statut affiché ----------------------------------------------------------------

    def set_status(self, model, text, tone="neutral"):
        state = self.attach(model)
        state.status, state.tone = text, tone
        self._refresh(model)

    def _refresh(self, model):
        if self.ui is not None:
            try:
                self.ui.refresh(model)
            except Exception as exc:
                self.log(f"[eloignement] volet : {exc}")

    def describe(self, model):
        """(état, ton) de l'enregistrement, pour le volet."""
        state = self.attach(model)
        link = link_mod.read_link(model)
        if link is None:
            return "", "neutral"
        entry = self.outbox.get(state.key or self.key_for(model, link))
        if entry is not None:
            if entry.state == outbox_mod.CONFLICT:
                return "Conflit de version : " + (entry.message or "à résoudre"), "error"
            if entry.state == outbox_mod.REFUSED:
                return "Envoi refusé : " + entry.message, "error"
            detail = f" ({entry.message})" if entry.message else ""
            return "En attente d'envoi" + detail, "warning"
        if state.status:
            return state.status, state.tone
        try:
            if model.isModified():
                return "Modifications non enregistrées", "neutral"
        except Exception:
            pass
        return f"Lié au dossier n° {link.dossier}", "neutral"

    # -- envoi ------------------------------------------------------------------------

    def _switch(self):
        try:
            return self.shell.feature_state(FEATURE)
        except Exception:
            return False

    def kick(self):
        """Réveille (ou démarre) le thread d'envoi. Au démarrage, tant que le DM
        n'a pas dit si la fonction est allumée, le thread attend sa réponse :
        un envoi resté en file repart ainsi sans geste de l'agent."""
        if self._switch() is False:
            return
        with self._lock:
            self._wake.set()
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._send_loop,
                                            name="mirai-eloignement-envoi", daemon=True)
            self._worker.start()

    def _send_loop(self):
        waited = 0
        while True:
            self._wake.clear()
            switch = self._switch()
            if switch is None and waited < 20:
                waited += 1
                self._wake.wait(30)
                continue
            if switch is not True:
                with self._lock:
                    self._worker = None
                return
            for outcome in outbox_mod.send_due(self.outbox, self.client):
                self.dispatcher.post(lambda o=outcome: self.apply_outcome(o))
            wake_at = self.outbox.next_wakeup()
            if wake_at is None:
                with self._lock:
                    if not self._wake.is_set():
                        self._worker = None
                        return
                continue
            self._wake.wait(max(1.0, min(wake_at - time.time(), MAX_SLEEP_S)))

    def _model_for_key(self, key):
        for state in list(self._docs.values()):
            if state.key == key:
                return state.model
            link = link_mod.read_link(state.model)
            if link and self.key_for(state.model, link) == key:
                state.key = key
                return state.model
        return None

    def apply_outcome(self, outcome):
        """Résultat d'un envoi, appliqué sur le thread principal."""
        model = self._model_for_key(outcome.key)
        self.log(f"[eloignement] envoi {outcome.state}")
        if model is None:
            return
        state = self.attach(model)
        if outcome.state == outbox_mod.SENT:
            link = link_mod.read_link(model)
            if link is None:
                return
            self.known.add(link.dossier, outcome.document or outcome.key)
            was_modified = bool(model.isModified())
            link.document = outcome.document or link.document
            link.version = outcome.version or link.version
            link_mod.write_link(model, link)
            state.key = self.key_for(model, link)
            if not was_modified:
                # Le fichier local porte la nouvelle version. Si l'agent a déjà
                # retouché le document, on ne l'enregistre PAS à sa place : la
                # version partira avec son prochain Ctrl+S.
                state.ignore_next_save = True
                try:
                    model.store()
                except Exception as exc:
                    state.ignore_next_save = False
                    self.log(f"[eloignement] réenregistrement local impossible : {exc}")
            name = f" sous « {outcome.nom} »" if outcome.nom else ""
            self.set_status(model, f"Enregistré dans le dossier n° {link.dossier}{name}.",
                            "success")
        elif outcome.state == outbox_mod.CONFLICT:
            self._refresh(model)
            if self.ui is not None:
                self.ui.show_conflict(model)
        else:
            self._refresh(model)

    # -- conflit (thread de travail) --------------------------------------------------

    def keep_mine(self, key, dossier, document):
        """Renvoie ma version comme nouvelle version, sur la version courante
        du dossier : celle annoncée par la 412, sinon relue dans la liste des
        actes. Réseau : thread de travail."""
        entry = self.outbox.get(key)
        version = entry.server_version if entry else ""
        if not version:
            try:
                for acte in self.client.actes(dossier):
                    if str(acte.get("acte_id") or acte.get("id")) == str(document):
                        version = str(acte.get("version") or "")
                        break
            except api.EloignementError as exc:
                self.log(f"[eloignement] version courante illisible : {exc.message}")
        self.outbox.keep_mine(key, version)
        self.kick()

    def download_act(self, dossier, document, nom, suffix=""):
        """Télécharge l'acte dans le dossier de travail. Rend (chemin, version)."""
        data, version = self.client.contenu(dossier, document)
        name = safe_filename(nom or f"acte-{document}")
        if suffix:
            stem, ext = os.path.splitext(name)
            name = f"{stem} {suffix}{ext}"
        path = unique_path(self.workdir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path, version

    # -- ouverture (thread principal) ---------------------------------------------------

    def open_linked(self, path, link, empty=None, labels=None, header=None, modele_nom="",
                    snapshot=None):
        """Ouvre un ODT dans Writer, sans conversion, et le lie au dossier.

        `snapshot` : valeurs insérées par Éloignement, gardées dans le document
        pour « Actualiser depuis le dossier » (voir snapshot.py)."""
        ctx = self.shell.uno_ctx
        desktop = ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx)
        model = desktop.loadComponentFromURL(file_url(path), "_blank", 0, ())
        state = self.attach(model)
        link_mod.write_link(model, link)
        if snapshot:
            snapshot_mod.write_snapshot(model, snapshot)
        state.key = self.key_for(model, link)
        self.known.add(link.dossier, state.key)
        state.empty = list(empty or [])
        state.labels = dict(labels or {})
        state.header = dict(header or {})
        state.modele_nom = modele_nom
        state.ignore_next_save = True
        try:
            model.store()           # le fichier de travail porte le lien
        except Exception as exc:
            state.ignore_next_save = False
            self.log(f"[eloignement] enregistrement initial impossible : {exc}")
        state.status, state.tone = f"Lié au dossier n° {link.dossier}", "success"
        if self.ui is not None:
            self.ui.show_panel(model)
        return model
