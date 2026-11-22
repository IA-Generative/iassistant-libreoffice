"""Client des routes /v1 d'Éloignement, derrière le relais du DM.

Le transport (URL `toolsEndpoint`, paire relais, jeton de l'agent, proxy, TLS)
appartient à la façade : ce module reçoit une fonction
`send(method, path, body, headers, timeout) -> (status, headers, octets)` et
ne connaît ni hôte ni secret d'Éloignement.

Les chemins sont regroupés dans ROUTES : une partie n'existe pas encore côté
Éloignement (voir docs/eloignement-conception.md §6) et leurs noms peuvent
changer. Un seul endroit à retoucher.

Aucune méthode ne doit être appelée depuis le thread principal : elles font
du réseau bloquant.
"""

import json
import re
import uuid
from urllib.parse import urlencode

SERVICE = "eloignement"

ROUTES = {
    "modeles": "v1/modeles",
    "dossiers": "v1/dossiers",
    "dossier": "v1/dossiers/{dossier}",
    "actes": "v1/dossiers/{dossier}/actes",
    "contenu": "v1/dossiers/{dossier}/actes/{document}/contenu",
    "versions": "v1/dossiers/{dossier}/actes/{document}/versions",
    "balises": "v1/dossiers/{dossier}/balises",
}

DEFAULT_TIMEOUT = 20
UPLOAD_TIMEOUT = 120
ODT_TYPE = "application/vnd.oasis.opendocument.text"

# Messages repris de la messagerie (ELO_STATUS_MESSAGES) : l'agent lit les
# mêmes mots dans les deux outils.
STATUS_MESSAGES = {
    0: "service injoignable",
    401: "session MirAI expirée ou refusée",
    403: "accès refusé par Éloignement",
    404: "dossier ou élément introuvable",
    409: "opération impossible dans l'état actuel du dossier",
    412: "le document a été modifié dans Éloignement entre-temps",
    413: "pièce trop volumineuse pour le relais",
    415: "Éloignement n'accepte que des documents ODT",
    428: "version de référence manquante",
    429: "trop de demandes : réessayer dans une minute",
}
SERVER_ERROR = "service indisponible"

# Pannes passagères : l'envoi est gardé et rejoué.
TRANSIENT = {0, 408, 429, 502, 503, 504}

# Un identifiant finit dans un chemin d'URL. Il peut venir d'un fichier reçu
# (propriétés du document) : rien d'autre que ces caractères n'y entre.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class EloignementError(Exception):
    """Refus ou panne, avec un message lisible par l'agent."""

    def __init__(self, status, message, retry_after=None, problem=None):
        super().__init__(message)
        self.status = int(status or 0)
        self.message = message
        self.retry_after = retry_after
        self.problem = problem or {}

    @property
    def transient(self):
        return self.status in TRANSIENT or self.status >= 500


def safe_id(value):
    """L'identifiant tel quel s'il est sûr dans un chemin, sinon ValueError."""
    text = str(value or "").strip()
    if not _SAFE_ID.match(text):
        raise ValueError("identifiant Éloignement invalide")
    return text


def status_message(status):
    status = int(status or 0)
    if status in STATUS_MESSAGES:
        return STATUS_MESSAGES[status]
    if status >= 500:
        return SERVER_ERROR
    return f"erreur inattendue (HTTP {status})"


def header(headers, name):
    """En-tête de réponse, sans tenir compte de la casse (le relais peut la changer)."""
    if not headers:
        return None
    try:
        value = headers.get(name)
        if value is not None:
            return value
        lowered = name.lower()
        for key in headers.keys():
            if str(key).lower() == lowered:
                return headers[key]
    except Exception:
        return None
    return None


def _retry_after(headers):
    try:
        value = header(headers, "Retry-After")
        return max(0, int(str(value).strip())) if value is not None else None
    except (TypeError, ValueError):
        return None


def error_from_response(status, headers, body):
    """EloignementError à partir d'une réponse en échec.

    Ordre de la messagerie : `detail` du problem+json (et ses `erreurs`), puis
    `title`, puis le message du relais, puis le libellé du code."""
    problem = {}
    try:
        problem = json.loads((body or b"").decode("utf-8")) or {}
    except Exception:
        problem = {}
    if not isinstance(problem, dict):
        problem = {}
    message = ""
    detail = problem.get("detail")
    if isinstance(detail, str) and detail.strip():
        message = detail.strip()
        erreurs = problem.get("erreurs")
        if isinstance(erreurs, list):
            extra = [str(e.get("message", "")).strip() for e in erreurs
                     if isinstance(e, dict) and e.get("message")]
            if extra:
                message += " (" + " ; ".join(extra) + ")"
    elif isinstance(detail, dict) and detail.get("message"):
        message = str(detail["message"]).strip()
    elif isinstance(problem.get("title"), str) and problem["title"].strip():
        message = problem["title"].strip()
    elif isinstance(problem.get("error"), dict) and problem["error"].get("message"):
        message = str(problem["error"]["message"]).strip()
    if not message:
        message = status_message(status)
    elif status:
        message = f"{message} (HTTP {int(status)})"
    return EloignementError(status, message, _retry_after(headers), problem)


def multipart(fields, files):
    """Corps multipart/form-data, sans dépendance.

    `fields` : {nom: texte} ; `files` : {nom: (nom_de_fichier, type, octets)}.
    Rend (octets, content_type)."""
    boundary = "mirai-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
            f"Content-Type: application/json; charset=utf-8\r\n\r\n".encode("utf-8")
            + str(value).encode("utf-8") + b"\r\n")
    for name, (filename, content_type, data) in files.items():
        safe_name = re.sub(r'["\r\n\\]', "_", filename or "document.odt")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{safe_name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode("utf-8")
            + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Client:
    """Les besoins du parcours Writer, une méthode chacun."""

    def __init__(self, send):
        self._send = send

    # -- transport -----------------------------------------------------------

    def _call(self, method, path, body=None, headers=None, timeout=DEFAULT_TIMEOUT,
              raw=False):
        all_headers = {"Accept": "application/json"}
        all_headers.update(headers or {})
        try:
            status, resp_headers, data = self._send(
                method, f"{SERVICE}/{path}", body, all_headers, timeout)
        except EloignementError:
            raise
        except Exception as exc:
            raise EloignementError(0, status_message(0)) from exc
        if not 200 <= int(status) < 300:
            raise error_from_response(status, resp_headers, data)
        if raw:
            return data, resp_headers
        if not data:
            return {}, resp_headers
        try:
            return json.loads(data.decode("utf-8")), resp_headers
        except Exception as exc:
            raise EloignementError(status, "réponse illisible d'Éloignement") from exc

    @staticmethod
    def _query(params):
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        return ("?" + urlencode(clean)) if clean else ""

    # -- lecture ---------------------------------------------------------------

    def modeles(self, q=""):
        data, _ = self._call("GET", ROUTES["modeles"] + self._query({"q": q}))
        return _items(data)

    def rechercher(self, q):
        data, _ = self._call("GET", ROUTES["dossiers"] + self._query({"q": q, "limit": 20}))
        return _items(data)

    def dossier(self, dossier_id):
        data, _ = self._call("GET", ROUTES["dossier"].format(dossier=safe_id(dossier_id)))
        return data

    def actes(self, dossier_id):
        data, _ = self._call("GET", ROUTES["actes"].format(dossier=safe_id(dossier_id)))
        return _items(data)

    def balises(self, dossier_id, modele_id=""):
        """Valeurs des balises du dossier : {nom: valeur} et {nom: libellé}.

        Éloignement filtre par modèle, pas par liste de balises : le tri des
        balises utiles se fait chez l'appelant."""
        path = ROUTES["balises"].format(dossier=safe_id(dossier_id))
        data, _ = self._call("GET", path + self._query({"modele_id": modele_id}))
        valeurs, libelles = {}, {}
        for item in _items(data):
            if isinstance(item, dict) and item.get("nom"):
                nom = str(item["nom"])
                valeurs[nom] = item.get("valeur")
                libelles[nom] = str(item.get("libelle") or nom)
        return valeurs, libelles

    def contenu(self, dossier_id, document_id):
        """Octets ODT de l'acte et sa version (ETag)."""
        path = ROUTES["contenu"].format(dossier=safe_id(dossier_id),
                                        document=safe_id(document_id))
        data, headers = self._call("GET", path, headers={"Accept": ODT_TYPE},
                                   timeout=UPLOAD_TIMEOUT, raw=True)
        return data, etag(headers)

    # -- écriture --------------------------------------------------------------

    def generer(self, dossier_id, modele_id, options=None):
        """Demande à Éloignement de remplir le modèle avec le dossier.

        Rend l'acte décrit par Éloignement, normalisé : id, nom attribué,
        version, balises restées vides [{nom, libelle}]. Les options de
        génération qui ne se déduisent pas du dossier sont refusées par
        Éloignement (422 OPTION_GENERATION_MANQUANTE, 409 CHAMPS_A_CONFIRMER) :
        l'agent les règle dans Éloignement."""
        payload = {"modele_id": str(modele_id)}
        if options:
            payload["options"] = options
        body = json.dumps(payload).encode("utf-8")
        data, headers = self._call(
            "POST", ROUTES["actes"].format(dossier=safe_id(dossier_id)), body,
            {"Content-Type": "application/json",
             "Idempotency-Key": f"acte:{dossier_id}:{modele_id}:{uuid.uuid4().hex}"},
            timeout=UPLOAD_TIMEOUT)
        return normalize_acte(data, headers)

    def verser(self, dossier_id, document_id, odt_bytes, version, nom=""):
        """Nouvelle version de l'acte. Éloignement exige la version de
        référence (If-Match, 428 sinon) et refuse (412) si elle a bougé ; la
        412 porte la version courante, que garde l'erreur (`problem`)."""
        if not version:
            raise EloignementError(428, status_message(428))
        path = ROUTES["versions"].format(dossier=safe_id(dossier_id),
                                         document=safe_id(document_id))
        body, content_type = multipart(
            {}, {"fichier": (nom or "acte.odt", ODT_TYPE, odt_bytes)})
        headers = {"Content-Type": content_type,
                   "If-Match": version if version.startswith(('"', "W/")) else f'"{version}"'}
        data, resp_headers = self._call("POST", path, body, headers, timeout=UPLOAD_TIMEOUT)
        return normalize_acte(data, resp_headers)


def normalize_acte(data, headers=None):
    """Acte tel que le plugin le manipule : {id, nom, version, balises_vides}."""
    data = data if isinstance(data, dict) else {}
    vides = []
    for item in data.get("balises_vides") or []:
        if isinstance(item, dict) and (item.get("nom") or item.get("balise")):
            nom = str(item.get("nom") or item.get("balise"))
            vides.append({"nom": nom, "libelle": str(item.get("libelle") or nom)})
    return {
        "id": str(data.get("acte_id") or data.get("id") or ""),
        "nom": str(data.get("nom") or ""),
        "version": str(data.get("version") or data.get("etag") or etag(headers) or ""),
        "balises_vides": vides,
        "modele": data.get("modele") if isinstance(data.get("modele"), dict) else {},
    }


def server_version(error):
    """Version courante annoncée par une 412, ou ""."""
    problem = getattr(error, "problem", None) or {}
    return str(problem.get("version") or "") if isinstance(problem, dict) else ""


def etag(headers):
    """Valeur de l'ETag sans guillemets ni préfixe faible."""
    try:
        value = str(header(headers, "ETag") or "").strip()
    except Exception:
        return ""
    if value.startswith("W/"):
        value = value[2:]
    return value.strip('"')


def _items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return []


def announces(discovery):
    """La découverte du relais annonce-t-elle le service Éloignement ?"""
    if not isinstance(discovery, dict):
        return False
    services = discovery.get("services")
    if isinstance(services, list) and SERVICE in services:
        return True
    for entry in discovery.get("passthrough") or []:
        if isinstance(entry, dict) and entry.get("service") == SERVICE:
            return True
    for tool in discovery.get("tools") or []:
        if isinstance(tool, dict) and tool.get("service") == SERVICE:
            return True
    return False
