"""Vérification de la signature d'un paquet servi par un Device Management
(ADR-0006 du DM, « chaîne de confiance des paquets »).

Bibliothèque standard seulement : le Python que LibreOffice embarque n'a pas la
bibliothèque `cryptography`. La vérification ECDSA est donc écrite ici, en
entiers Python. Elle ne manipule que des données publiques (clé publique,
signature, message) : rien n'y dépend d'un secret, le temps de calcul n'a pas à
être constant. Ce module ne signe pas et ne doit jamais le faire.

Format. Un seul algorithme : ES256 (ECDSA sur la courbe P-256, SHA-256),
signature brute `r||s` sur 64 octets. `keyid` = 16 premiers caractères
hexadécimaux du SHA-256 de la clé publique (SPKI DER).

    enveloppe    {schema: "dm-package-signature/1", payload: <base64>,
                  signatures: [{keyid, alg: "ES256", sig: <base64 r||s>}],
                  delegation?: {payload: <base64>, signatures: [...]}}
    déclaration  payload de l'enveloppe, "dm-package-statement/1" :
                  kind, slug, version, file, sha256, size, extension_id…
    délégation   payload "dm-signing-delegation/1" : dm, keyid, public_key
                  (SPKI en base64), not_before, not_after ; signée par une racine
    ancre        dm-trust.json, "dm-trust/1" : {roots: [{keyid, alg, public_key}]}

Une signature porte sur les OCTETS du payload décodé : aucune canonisation JSON.

Accepté : une enveloppe signée par une racine de l'ancre, ou par une clé
couverte par une délégation valide d'une racine (signée par la racine, dans ses
dates, keyid égal à celui de la clé portée). Puis, dans cet ordre :
`kind == "served"`, SHA-256 des octets, version égale à la cible, et le lien
avec cette extension : l'`extension_id` de la déclaration égal à l'identifiant
de l'extension installée, ou, pour une déclaration sans `extension_id`, le slug.

Écrit pour tourner sur tout Python 3.6 ou plus récent.
"""

import base64
import binascii
import calendar
import datetime
import hashlib
import json
import os
import re
import time

ENVELOPE_SCHEMA = "dm-package-signature/1"
STATEMENT_SCHEMA = "dm-package-statement/1"
DELEGATION_SCHEMA = "dm-signing-delegation/1"
TRUST_SCHEMA = "dm-trust/1"
TRUST_FILE = "dm-trust.json"
ALG = "ES256"

# Codes de refus, stables : journalisés, envoyés au DM et testés. Ce sont ceux
# du DM et du plugin Thunderbird pour les mêmes contrôles.
OK = "ok"
NO_ENVELOPE = "no_envelope"
MALFORMED = "malformed"
UNKNOWN_KEY = "unknown_key"
BAD_SIGNATURE = "bad_signature"
DELEGATION_INVALID = "delegation_invalid"
WRONG_KIND = "wrong_kind"
DIGEST_MISMATCH = "digest_mismatch"
VERSION_MISMATCH = "version_mismatch"
SLUG_MISMATCH = "slug_mismatch"
# La déclaration nomme une autre extension que celle qui est installée.
EXTENSION_MISMATCH = "extension_mismatch"
# L'ancre (dm-trust.json) est illisible ou ne nomme aucune racine utilisable.
NO_TRUST = "no_trust"

# ─── Courbe P-256 (secp256r1, FIPS 186-4 D.1.2.3) ───────────────────────────

_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5

# SubjectPublicKeyInfo d'une clé P-256 non compressée : cet en-tête, puis
# 0x04 || X || Y. AlgorithmIdentifier { id-ecPublicKey, prime256v1 }, puis une
# BIT STRING de 66 octets sans bit inutilisé.
_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
_SPKI_LENGTH = len(_SPKI_PREFIX) + 65

_INFINITY = (0, 1, 0)


def _inverse(value, modulus):
    """Inverse modulaire par le petit théorème de Fermat (module premier)."""
    return pow(value, modulus - 2, modulus)


def _on_curve(x, y):
    return (y * y - (x * x * x - 3 * x + _B)) % _P == 0


def _double(point):
    """2·P en coordonnées jacobiennes (a = -3)."""
    x, y, z = point
    if not z or not y:
        return _INFINITY
    yy = y * y % _P
    s = 4 * x * yy % _P
    zz = z * z % _P
    m = 3 * (x - zz) * (x + zz) % _P
    x3 = (m * m - 2 * s) % _P
    y3 = (m * (s - x3) - 8 * yy * yy) % _P
    return x3, y3, 2 * y * z % _P


def _add(left, right):
    """P + Q en coordonnées jacobiennes."""
    if not left[2]:
        return right
    if not right[2]:
        return left
    x1, y1, z1 = left
    x2, y2, z2 = right
    z1z1 = z1 * z1 % _P
    z2z2 = z2 * z2 % _P
    u1 = x1 * z2z2 % _P
    u2 = x2 * z1z1 % _P
    s1 = y1 * z2 * z2z2 % _P
    s2 = y2 * z1 * z1z1 % _P
    if u1 == u2:
        return _double(left) if s1 == s2 else _INFINITY
    h = (u2 - u1) % _P
    r = (s2 - s1) % _P
    hh = h * h % _P
    hhh = h * hh % _P
    v = u1 * hh % _P
    x3 = (r * r - hhh - 2 * v) % _P
    y3 = (r * (v - x3) - s1 * hhh) % _P
    return x3, y3, h * z1 * z2 % _P


def _multiply(scalar, point):
    """k·P, de gauche à droite."""
    result = _INFINITY
    for bit in bin(scalar)[2:]:
        result = _double(result)
        if bit == "1":
            result = _add(result, point)
    return result


def _affine_x(point):
    """Abscisse affine, ou None pour le point à l'infini."""
    x, _y, z = point
    if not z:
        return None
    inverse = _inverse(z, _P)
    return x * inverse * inverse % _P


def public_point(spki):
    """Point (X, Y) d'une clé publique P-256 au format SPKI DER, ou None.

    Seul l'encodage non compressé `0x04 || X || Y` sous l'en-tête exact d'une
    clé P-256 est lu ; tout autre encodage (point compressé, autre courbe,
    paramètres explicites, octets en trop) est refusé. Le point doit être sur
    la courbe : il n'est alors pas le point à l'infini, et P-256 n'a pas de
    sous-groupe (cofacteur 1).
    """
    if not isinstance(spki, (bytes, bytearray)) or len(spki) != _SPKI_LENGTH:
        return None
    spki = bytes(spki)
    if not spki.startswith(_SPKI_PREFIX) or spki[len(_SPKI_PREFIX)] != 0x04:
        return None
    body = spki[len(_SPKI_PREFIX) + 1:]
    x = int.from_bytes(body[:32], "big")
    y = int.from_bytes(body[32:], "big")
    if x >= _P or y >= _P or not _on_curve(x, y):
        return None
    return x, y


def key_id(spki):
    """16 premiers caractères hexadécimaux du SHA-256 de la clé (SPKI DER)."""
    return hashlib.sha256(bytes(spki)).hexdigest()[:16]


def verify_es256(point, signature, message):
    """Vrai si `signature` (r||s brut, 64 octets) est une signature ECDSA
    P-256 / SHA-256 de `message` par la clé publique `point` (X, Y)."""
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    if not point or len(point) != 2:
        return False
    x, y = point
    if not (0 <= x < _P and 0 <= y < _P and _on_curve(x, y)):
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    digest = int.from_bytes(hashlib.sha256(bytes(message)).digest(), "big")
    w = _inverse(s, _N)
    total = _add(_multiply(digest * w % _N, (_GX, _GY, 1)),
                 _multiply(r * w % _N, (x, y, 1)))
    found = _affine_x(total)
    return found is not None and found % _N == r


# ─── Enveloppe, délégation, ancre ───────────────────────────────────────────

class _Refusal(Exception):
    def __init__(self, reason, detail):
        Exception.__init__(self, detail)
        self.reason = reason
        self.detail = detail


class Result:
    """Issue d'une vérification. `reason` vaut `"ok"` quand `ok` ; `keyid` est
    le signataire et `statement` la déclaration, dès que la signature tient."""

    def __init__(self, reason, detail="", keyid=None, statement=None):
        self.ok = reason == OK
        self.reason = reason
        self.detail = detail
        self.keyid = keyid
        self.statement = statement

    def __repr__(self):
        return f"Result(reason={self.reason!r}, detail={self.detail!r}, keyid={self.keyid!r})"


_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?"
                      r"(Z|[+-]\d{2}:\d{2})?$")


def _base64(text):
    """Octets d'un base64 standard strict (RFC 4648, avec bourrage), ou None.
    Alphabet URL, espaces et bourrage manquant sont refusés, comme par le DM."""
    if not isinstance(text, str) or len(text) % 4 or not _BASE64_RE.match(text):
        return None
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError):
        return None


def _as_object(source):
    """Un document JSON reçu en texte ou en octets ; un objet est pris tel quel."""
    if isinstance(source, dict):
        return source
    if isinstance(source, (bytes, bytearray)):
        try:
            source = bytes(source).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(source, str):
        return None
    try:
        parsed = json.loads(source)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_payload(payload, schema):
    """Le payload signé, lu comme un document du schéma attendu, ou None. De
    l'UTF-8 mal formé est refusé, pas réparé : ce qui est lu est ce qui a été signé."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    document = _as_object(text)
    return document if document and document.get("schema") == schema else None


def _parse_date(text):
    """Secondes depuis l'époque d'une date-heure ISO 8601, ou None. Une date
    sans fuseau est en UTC, comme du côté du DM."""
    match = _DATE_RE.match(text) if isinstance(text, str) else None
    if not match:
        return None
    try:
        moment = datetime.datetime(*(int(part) for part in match.group(1, 2, 3, 4, 5, 6)))
    except ValueError:
        return None
    seconds = calendar.timegm(moment.timetuple())
    zone = match.group(7)
    if zone and zone != "Z":
        offset = int(zone[1:3]) * 3600 + int(zone[4:6]) * 60
        seconds -= offset if zone[0] == "+" else -offset
    return seconds


def _import_key(text):
    """(keyid, point) d'une clé publique SPKI en base64, ou None."""
    spki = _base64(text)
    point = public_point(spki) if spki is not None else None
    return (key_id(spki), point) if point else None


def _signed_payload(part, keys):
    """(octets du payload, keyid) si une signature de `part` (une enveloppe ou
    une délégation) est valide pour une des clés `keys` ({keyid: point})."""
    payload = _base64(part.get("payload"))
    if payload is None:
        raise _Refusal(MALFORMED, "payload is not base64")
    signatures = part.get("signatures")
    known = False
    for entry in signatures if isinstance(signatures, list) else ():
        if not isinstance(entry, dict) or entry.get("alg") != ALG:
            continue
        keyid = entry.get("keyid")
        if not isinstance(keyid, str) or keyid not in keys:
            continue
        known = True
        signature = _base64(entry.get("sig"))
        if signature is None:
            raise _Refusal(MALFORMED, "signature is not base64")
        if verify_es256(keys[keyid], signature, payload):
            return payload, keyid
    if known:
        raise _Refusal(BAD_SIGNATURE, "no valid signature by a known key")
    raise _Refusal(UNKNOWN_KEY, "no signature by a known key")


def _load_roots(trust):
    """Racines utilisables de l'ancre : {keyid: point}, au moins une. Une
    racine dont le keyid n'est pas celui de sa clé, ou dont la clé n'est pas
    une clé P-256 valide, est écartée."""
    document = _as_object(trust)
    if not document or document.get("schema") != TRUST_SCHEMA \
            or not isinstance(document.get("roots"), list):
        raise _Refusal(NO_TRUST, "dm-trust.json unreadable")
    roots = {}
    for entry in document["roots"]:
        if not isinstance(entry, dict) or entry.get("alg") != ALG:
            continue
        imported = _import_key(entry.get("public_key"))
        if imported and imported[0] == entry.get("keyid"):
            roots[imported[0]] = imported[1]
    if not roots:
        raise _Refusal(NO_TRUST, "dm-trust.json names no usable root")
    return roots


def _check_delegation(delegation, roots, now):
    """(keyid, point) de la clé déléguée : délégation signée par une racine,
    dans ses dates, nommant la clé qu'elle porte."""
    if not isinstance(delegation, dict):
        raise _Refusal(DELEGATION_INVALID, "delegation unreadable")
    try:
        payload, _root = _signed_payload(delegation, roots)
    except _Refusal as refusal:
        raise _Refusal(DELEGATION_INVALID,
                       f"delegation not signed by a root ({refusal.reason})") from None
    content = _json_payload(payload, DELEGATION_SCHEMA)
    if not content:
        raise _Refusal(DELEGATION_INVALID, "delegation unreadable")
    not_before = _parse_date(content.get("not_before"))
    not_after = _parse_date(content.get("not_after"))
    if not_before is None or not_after is None or not (not_before <= now <= not_after):
        raise _Refusal(DELEGATION_INVALID, "delegation outside its validity period")
    imported = _import_key(content.get("public_key"))
    if not imported:
        raise _Refusal(DELEGATION_INVALID, "delegation: unreadable public key")
    if imported[0] != content.get("keyid"):
        raise _Refusal(DELEGATION_INVALID, "delegation: keyid does not match the key")
    return imported


def _accepted_keys(envelope, roots, now):
    """Clés admises pour l'enveloppe : les racines, plus la clé déléguée quand
    la délégation tient. Une délégation invalide ne fait pas obstacle à une
    signature faite par une racine elle-même."""
    keys = dict(roots)
    if envelope.get("delegation") is None:
        return keys
    try:
        keyid, point = _check_delegation(envelope["delegation"], roots, now)
        keys[keyid] = point
    except _Refusal:
        signed_by_root = any(isinstance(entry, dict) and isinstance(entry.get("keyid"), str)
                             and entry["keyid"] in roots for entry in envelope["signatures"])
        if not signed_by_root:
            raise
    return keys


def _check_statement(statement, data, version, extension_id, slug):
    if statement.get("kind") != "served":
        kind = statement.get("kind")
        raise _Refusal(WRONG_KIND, f'statement of kind "{kind}", "served" expected')
    if not isinstance(data, (bytes, bytearray)):
        raise _Refusal(DIGEST_MISMATCH, "no bytes to check")
    digest = hashlib.sha256(bytes(data)).hexdigest()
    signed = statement.get("sha256")
    if not isinstance(signed, str) or signed.lower() != digest:
        raise _Refusal(DIGEST_MISMATCH,
                       f"signed sha256 {str(signed)[:12]}, downloaded {digest[:12]}")
    if not isinstance(version, str) or not version or statement.get("version") != version:
        signed = statement.get("version")
        raise _Refusal(VERSION_MISMATCH, f"signed version {signed}, target {version}")
    # Lien avec cette extension. L'identifiant, lu par le DM dans le paquet
    # qu'il a signé, est le lien fiable ; le slug est le nom d'une fiche du
    # catalogue et ne sert que pour une déclaration sans identifiant. Un
    # appelant sans identifiant ne saute jamais le contrôle : c'est un refus.
    if "extension_id" in statement:
        if not isinstance(extension_id, str) or not extension_id \
                or statement["extension_id"] != extension_id:
            signed = statement["extension_id"]
            raise _Refusal(EXTENSION_MISMATCH,
                           f'signed for extension "{signed}", installed "{extension_id}"')
    elif not isinstance(slug, str) or not slug or statement.get("slug") != slug:
        signed = statement.get("slug")
        raise _Refusal(SLUG_MISMATCH, f'signed for "{signed}", expected "{slug}"')


def _verify(envelope, data, version, extension_id, slug, trust, now, result):
    roots = _load_roots(trust)
    if envelope is None or envelope == "" or envelope == b"":
        raise _Refusal(NO_ENVELOPE, "no signature envelope")
    envelope = _as_object(envelope)
    if not envelope or envelope.get("schema") != ENVELOPE_SCHEMA:
        raise _Refusal(MALFORMED, f"envelope unreadable: schema {ENVELOPE_SCHEMA} expected")
    if not isinstance(envelope.get("payload"), str) \
            or not isinstance(envelope.get("signatures"), list):
        raise _Refusal(MALFORMED, "envelope unreadable: payload or signatures missing")
    payload, keyid = _signed_payload(envelope, _accepted_keys(envelope, roots, now))
    statement = _json_payload(payload, STATEMENT_SCHEMA)
    if not statement:
        raise _Refusal(MALFORMED, f"statement unreadable: schema {STATEMENT_SCHEMA} expected")
    result.keyid = keyid
    result.statement = statement
    _check_statement(statement, data, version, extension_id, slug)


def verify_package_signature(envelope, data, version, extension_id, slug, trust, now=None):
    """Vérifie l'enveloppe d'un paquet servi par un DM contre les octets
    téléchargés. Ne lève jamais : rend un `Result`.

    envelope      `update.signature` de la directive, objet ou texte JSON.
    data          octets du paquet téléchargé.
    version       version cible annoncée par la directive.
    extension_id  identifiant de l'extension INSTALLÉE ; comparé à
                  l'`extension_id` de la déclaration quand elle en porte un (le
                  slug n'est alors pas comparé).
    slug          nom sous lequel le plugin interroge le DM ; comparé seulement
                  quand la déclaration ne porte pas d'`extension_id`.
    trust         dm-trust.json du paquet INSTALLÉ, objet, texte ou octets.
    now           secondes depuis l'époque (défaut : maintenant) ; borne la
                  délégation.
    """
    result = Result(MALFORMED)
    try:
        _verify(envelope, data, version, extension_id, slug, trust,
                time.time() if now is None else now, result)
        result.ok, result.reason = True, OK
    except _Refusal as refusal:
        result.ok, result.reason, result.detail = False, refusal.reason, refusal.detail
    except Exception as exc:  # jamais d'installation sur une erreur inattendue
        result.ok, result.reason, result.detail = False, MALFORMED, type(exc).__name__
    return result


def read_trust(package_dir):
    """Ancre du paquet installé : le texte de `dm-trust.json` à la racine du
    paquet, `None` si le fichier n'existe pas (paquet antérieur à la signature),
    `""` s'il existe mais ne se lit pas, ce qui vaudra refus (`no_trust`)."""
    path = os.path.join(package_dir or "", TRUST_FILE)
    if not package_dir or not os.path.lexists(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
