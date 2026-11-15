#!/usr/bin/env python3
"""Signature d'origine d'un paquet publié (ADR-0006 du Device Management).

    package_signature.py sign <paquet> --slug <slug> --version <version>
        Écrit <paquet>.sig.json. La clé privée (EC P-256, PEM, sans mot de
        passe) est lue dans la variable d'environnement PACKAGE_SIGNING_KEY :
        jamais sur la ligne de commande, jamais dans un fichier du dépôt. Elle
        n'est ni affichée ni écrite.

    package_signature.py verify <paquet> --public-key <fichier PEM>
                                [--slug <slug>] [--version <version>]
        Vérifie <paquet>.sig.json contre une ou plusieurs clés publiques.

Format. Une enveloppe `dm-package-signature/1` :

    {"schema": "dm-package-signature/1",
     "payload": "<base64 de la déclaration>",
     "signatures": [{"keyid": "...", "alg": "ES256", "sig": "<base64 r||s>"}]}

La déclaration (`dm-package-statement/1`, `kind: "build"`) porte le slug, la
version, le nom, l'empreinte SHA-256 et la taille du fichier, sa date et son
origine (`source` : dépôt, référence, commit, workflow, run), lue dans
l'environnement de GitHub Actions. La signature ES256 (ECDSA P-256, SHA-256)
est brute, r||s sur 64 octets, et porte sur les octets du payload décodé :
aucune canonisation JSON. `keyid` = 16 premiers caractères hexadécimaux du
SHA-256 de la clé publique (SPKI DER).

Demande la bibliothèque `cryptography` (scripts/requirements-signing.txt).

Codes de sortie. sign : 0 enveloppe écrite, 2 clé absente ou illisible.
verify : 0 valide, 1 refusée (le code du refus est affiché), 2 usage.
"""
import argparse
import base64
import binascii
import datetime
import hashlib
import json
import os
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

ENVELOPE_SCHEMA = "dm-package-signature/1"
STATEMENT_SCHEMA = "dm-package-statement/1"
ALG = "ES256"
KEY_ENV = "PACKAGE_SIGNING_KEY"
_PEM_PUBLIC = b"-----BEGIN PUBLIC KEY-----"
_SOURCE_ENV = (("repository", "GITHUB_REPOSITORY"), ("ref", "GITHUB_REF"),
               ("commit", "GITHUB_SHA"), ("run_id", "GITHUB_RUN_ID"))


class Refused(ValueError):
    """Enveloppe refusée ; `code` est stable (affiché et testé)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _p256(key):
    if not isinstance(getattr(key, "curve", None), ec.SECP256R1):
        raise ValueError("seule la courbe P-256 (ES256) est acceptée")
    return key


def _spki(public_key):
    return public_key.public_bytes(serialization.Encoding.DER,
                                   serialization.PublicFormat.SubjectPublicKeyInfo)


def key_id(public_key):
    return hashlib.sha256(_spki(public_key)).hexdigest()[:16]


def public_keys(pem):
    """Clés publiques P-256 d'un texte PEM -> {keyid: clé}."""
    keys = {}
    for block in pem.split(_PEM_PUBLIC)[1:]:
        key = _p256(serialization.load_pem_public_key(_PEM_PUBLIC + block))
        keys[key_id(key)] = key
    return keys


def source_from_env(env):
    source = {name: env.get(variable, "") for name, variable in _SOURCE_ENV}
    # GITHUB_WORKFLOW_REF nomme le fichier du workflow et sa référence ;
    # GITHUB_WORKFLOW n'est qu'un libellé, que tout workflow peut reprendre.
    source["workflow"] = env.get("GITHUB_WORKFLOW_REF") or env.get("GITHUB_WORKFLOW", "")
    return source


def build_statement(path, slug, version, source, built_at=None):
    with open(path, "rb") as fh:
        data = fh.read()
    built_at = built_at or datetime.datetime.now(datetime.UTC)
    return {
        "schema": STATEMENT_SCHEMA,
        "kind": "build",
        "slug": slug,
        "version": version,
        "file": os.path.basename(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "source": source,
        "built_at": built_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def make_envelope(statement, private_key):
    """Signe la déclaration, sérialisée une fois : ces octets font foi."""
    payload = json.dumps(statement, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    r, s = utils.decode_dss_signature(private_key.sign(payload, ec.ECDSA(hashes.SHA256())))
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return {
        "schema": ENVELOPE_SCHEMA,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [{"keyid": key_id(private_key.public_key()), "alg": ALG,
                        "sig": base64.b64encode(signature).decode("ascii")}],
    }


def _decode(text, what):
    try:
        return base64.b64decode(str(text), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Refused("malformed", f"{what} n'est pas du base64") from exc


def _signature_holds(key, signature, payload):
    if len(signature) != 64:
        return False
    der = utils.encode_dss_signature(int.from_bytes(signature[:32], "big"),
                                     int.from_bytes(signature[32:], "big"))
    try:
        key.verify(der, payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def _signer(signatures, payload, keys):
    """keyid de la première signature valide par une clé connue."""
    known = False
    for entry in signatures:
        if not isinstance(entry, dict) or entry.get("alg") != ALG:
            continue
        key = keys.get(str(entry.get("keyid") or ""))
        if key is None:
            continue
        known = True
        if _signature_holds(key, _decode(entry.get("sig"), "la signature"), payload):
            return str(entry["keyid"])
    if known:
        raise Refused("bad_signature", "signature invalide")
    raise Refused("unknown_key", "aucune signature par une clé connue")


def signed_statement(envelope, keys):
    """(déclaration, keyid du signataire) si une signature de l'enveloppe est
    valide pour une des clés `keys`."""
    if not isinstance(envelope, dict) or envelope.get("schema") != ENVELOPE_SCHEMA:
        raise Refused("malformed", f"schéma {ENVELOPE_SCHEMA} attendu")
    signatures = envelope.get("signatures")
    if not isinstance(envelope.get("payload"), str) or not isinstance(signatures, list):
        raise Refused("malformed", "payload ou signatures absents")
    payload = _decode(envelope["payload"], "le payload")
    signer = _signer(signatures, payload, keys)
    try:
        statement = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused("malformed", "déclaration illisible") from exc
    if not isinstance(statement, dict) or statement.get("schema") != STATEMENT_SCHEMA:
        raise Refused("malformed", f"schéma {STATEMENT_SCHEMA} attendu")
    return statement, signer


def verify_package(envelope, data, keys, kind="build", slug="", version=""):
    """Vérifie l'enveloppe contre les octets `data`. Lève Refused."""
    statement, signer = signed_statement(envelope, keys)
    if statement.get("kind") != kind:
        raise Refused("wrong_kind", f"enveloppe « {statement.get('kind')} », « {kind} » attendu")
    if str(statement.get("sha256") or "").lower() != hashlib.sha256(data).hexdigest():
        raise Refused("digest_mismatch", "le fichier n'est pas celui qui a été signé")
    if slug and statement.get("slug") != slug:
        raise Refused("slug_mismatch", f"signé pour « {statement.get('slug')} », pas « {slug} »")
    if version and statement.get("version") != version:
        raise Refused("version_mismatch",
                      f"signé en version {statement.get('version')}, pas {version}")
    return statement, signer


def _sign(args):
    pem = os.environ.get(KEY_ENV, "")
    if not pem.strip():
        print(f"{KEY_ENV} est vide : rien n'est signé.", file=sys.stderr)
        return 2
    try:
        private_key = _p256(serialization.load_pem_private_key(pem.encode("utf-8"),
                                                               password=None))
    except (ValueError, TypeError) as exc:
        # Seule la nature de l'erreur est affichée : jamais son texte.
        print(f"{KEY_ENV} illisible (clé EC P-256 en PEM, sans mot de passe, attendue) : "
              f"{type(exc).__name__}", file=sys.stderr)
        return 2
    statement = build_statement(args.file, args.slug, args.version, source_from_env(os.environ))
    envelope = make_envelope(statement, private_key)
    out = args.out or args.file + ".sig.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Enveloppe écrite : {out} (keyid {envelope['signatures'][0]['keyid']}, "
          f"sha256 {statement['sha256']})")
    return 0


def _verify(args):
    try:
        with open(args.public_key, "rb") as fh:
            keys = public_keys(fh.read())
        if not keys:
            raise ValueError(f"aucune clé publique dans {args.public_key}")
        with open(args.file, "rb") as fh:
            data = fh.read()
        with open(args.sig or args.file + ".sig.json", encoding="utf-8-sig") as fh:
            text = fh.read()
    except (OSError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    try:
        try:
            envelope = json.loads(text)
        except ValueError as exc:
            raise Refused("malformed", "enveloppe illisible : JSON attendu") from exc
        statement, signer = verify_package(envelope, data, keys, kind=args.kind,
                                           slug=args.slug, version=args.version)
    except Refused as exc:
        print(f"REFUSÉ [{exc.code}] : {exc}", file=sys.stderr)
        return 1
    print(f"VALIDE : {statement.get('slug')} {statement.get('version')} "
          f"({statement.get('kind')}), signé par {signer}")
    source = statement.get("source") if isinstance(statement.get("source"), dict) else {}
    for name in ("repository", "ref", "commit", "workflow", "run_id"):
        if source.get(name):
            print(f"  {name:<10} {source[name]}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Signature d'origine d'un paquet (ADR-0006).")
    commands = parser.add_subparsers(dest="command", required=True)
    sign = commands.add_parser("sign", help="écrit <paquet>.sig.json")
    sign.add_argument("file")
    sign.add_argument("--slug", required=True, help="nom du plugin au catalogue du DM")
    sign.add_argument("--version", required=True, help="version portée par le paquet")
    sign.add_argument("--out", help="enveloppe à écrire (défaut : <paquet>.sig.json)")
    sign.set_defaults(run=_sign)
    verify = commands.add_parser("verify", help="vérifie <paquet>.sig.json")
    verify.add_argument("file")
    verify.add_argument("--public-key", required=True, dest="public_key",
                        help="fichier PEM : une ou plusieurs clés publiques P-256")
    verify.add_argument("--sig", help="enveloppe (défaut : <paquet>.sig.json)")
    verify.add_argument("--kind", default="build", choices=("build", "served"))
    verify.add_argument("--slug", default="", help="slug attendu (non comparé si absent)")
    verify.add_argument("--version", default="", help="version attendue (non comparée si absente)")
    verify.set_defaults(run=_verify)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
