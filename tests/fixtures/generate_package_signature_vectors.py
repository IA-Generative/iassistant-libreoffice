#!/usr/bin/env python3
"""Vecteurs d'interopérabilité de la vérification de signature des paquets.

Écrit package_signature_vectors.json, lu par
tests/unit/test_package_signature_verify.py. Ce script ne partage rien avec
src/mirai/package_signature.py : il produit les enveloppes de l'ADR-0006 du
Device Management avec la bibliothèque `cryptography` (ECDSA P-256 / SHA-256,
signature brute r||s), comme le fait le DM. Le module du plugin, écrit en
Python pur, doit accepter et refuser exactement ce qui est annoncé ici.

    pip install -r scripts/requirements-signing.txt
    python3 tests/fixtures/generate_package_signature_vectors.py

CLÉS DE TEST. Les paires sont tirées au hasard à chaque exécution et leurs clés
privées ne quittent pas la mémoire : le fichier produit ne contient que des
clés publiques et des signatures. Aucune de ces clés ne protège quoi que ce
soit. Relancer le script remplace tous les vecteurs.
"""
import base64
import copy
import datetime
import hashlib
import json
import os

import cryptography
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "package_signature_vectors.json")

NOW = "2026-11-15T12:00:00Z"
SLUG = "mirai-libreoffice"
EXTENSION_ID = "fr.gouv.interieur.mirai"
VERSION = "0.4.0-rc.12"
PACKAGE = b"PK\x03\x04 paquet de test servi par un DM - pas une archive \xc3\xa9\x00\xff"
ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
FIELD = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
VALID_FROM, VALID_TO = "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"
PAST_FROM, PAST_TO = "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"


def b64(data):
    return base64.b64encode(data).decode("ascii")


def new_key(curve=None):
    return ec.generate_private_key(curve or ec.SECP256R1())


def spki(key, point_format=serialization.PublicFormat.SubjectPublicKeyInfo):
    return key.public_key().public_bytes(serialization.Encoding.DER, point_format)


def keyid_of(spki_bytes):
    return hashlib.sha256(spki_bytes).hexdigest()[:16]


def keyid(key):
    return keyid_of(spki(key))


def rs(key, payload):
    return utils.decode_dss_signature(key.sign(payload, ec.ECDSA(hashes.SHA256())))


def raw(r, s):
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def sign_raw(key, payload):
    return raw(*rs(key, payload))


def sign_der(key, payload):
    return key.sign(payload, ec.ECDSA(hashes.SHA256()))


def sign_low_s(key, payload):
    r, s = rs(key, payload)
    return raw(r, min(s, ORDER - s))


def sign_high_s(key, payload):
    r, s = rs(key, payload)
    return raw(r, max(s, ORDER - s))


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signed(payload, key, signer=sign_raw, kid=None):
    return {"payload": b64(payload),
            "signatures": [{"keyid": kid or keyid(key), "alg": "ES256",
                            "sig": b64(signer(key, payload))}]}


def statement(**changes):
    doc = {
        "schema": "dm-package-statement/1",
        "kind": "served",
        "slug": SLUG,
        "extension_id": EXTENSION_ID,
        "version": VERSION,
        "file": f"{SLUG}-{VERSION}.oxt",
        "sha256": hashlib.sha256(PACKAGE).hexdigest(),
        "size": len(PACKAGE),
        "dm": "https://dm.example.invalid",
        "signed_at": "2026-11-15T09:00:00Z",
        "upstream": {"sha256": "0" * 64, "verified": True, "ref": "refs/heads/develop",
                     "keyid": "0" * 16},
        # Du texte hors ASCII : le payload est de l'UTF-8, le module doit le décoder.
        "changed_files": ["dm-trust.json", "config.default.json", "description.xml",
                          "notes/résumé — été.txt"],
    }
    doc.update(changes)
    # None retire le champ : une déclaration sans extension_id (DM antérieur).
    return {k: v for k, v in doc.items() if v is not None}


def envelope(doc, key, delegation=None, **kw):
    env = {"schema": "dm-package-signature/1"}
    env.update(signed(dumps(doc), key, **kw))
    if delegation is not None:
        env["delegation"] = delegation
    return env


def delegation(root, dm_key, not_before=VALID_FROM, not_after=VALID_TO, **changes):
    content = {
        "schema": "dm-signing-delegation/1",
        "dm": "https://dm.example.invalid",
        "keyid": keyid(dm_key),
        "public_key": b64(spki(dm_key)),
        "not_before": not_before,
        "not_after": not_after,
    }
    content.update(changes)
    return signed(dumps(content), root)


def root_entry(spki_bytes):
    return {"keyid": keyid_of(spki_bytes), "alg": "ES256", "public_key": b64(spki_bytes)}


def trust(*roots):
    return {"schema": "dm-trust/1", "roots": [root_entry(spki(k)) for k in roots]}


def with_signature(env, sig):
    out = copy.deepcopy(env)
    out["signatures"][0]["sig"] = b64(sig)
    return out


def moved_point(key, x=None, y=None):
    """SPKI de `key` dont le point est remplacé : même en-tête, autres coordonnées."""
    der = spki(key)
    head, px, py = der[:-64], der[-64:-32], der[-32:]
    if x is not None:
        px = x.to_bytes(32, "big")
    if y is not None:
        py = y.to_bytes(32, "big")
    return head + px + py


def main():
    root, other_root, dm, stranger = new_key(), new_key(), new_key(), new_key()
    p384 = new_key(ec.SECP384R1())
    anchor = trust(root)
    good = envelope(statement(), dm, delegation(root, dm))
    direct = envelope(statement(), root)
    vectors = []

    def add(name, about, expect, env=good, **over):
        case = {"name": name, "about": about, "envelope": env, "bytes": b64(PACKAGE),
                "version": VERSION, "slug": SLUG, "extension_id": EXTENSION_ID,
                "trust": anchor, "now": NOW, "expect": {"ok": expect == "ok", "reason": expect}}
        case.update(over)
        vectors.append(case)

    # --- Acceptés -------------------------------------------------------------
    add("valid_root_direct", "enveloppe signée directement par la racine, sans délégation", "ok",
        env=direct)
    add("valid_delegated", "enveloppe signée par la clé du DM, délégation valide de la racine", "ok")
    add("valid_simple_mode", "mode simple : l'ancre est la clé du DM lui-même", "ok",
        env=envelope(statement(), dm), trust=trust(dm))
    add("valid_second_root", "ancre à deux racines, signature par la seconde", "ok",
        env=envelope(statement(), other_root), trust=trust(root, other_root))
    add("valid_root_direct_despite_expired_delegation",
        "délégation expirée jointe, mais l'enveloppe est signée par la racine elle-même", "ok",
        env=envelope(statement(), root, delegation(root, dm, PAST_FROM, PAST_TO)))
    add("valid_envelope_as_text", "l'enveloppe et l'ancre arrivent en texte JSON", "ok",
        env=json.dumps(good), trust=json.dumps(anchor))
    add("valid_low_s", "signature dont s est dans la moitié basse de l'ordre", "ok",
        env=envelope(statement(), root, signer=sign_low_s))
    add("valid_high_s", "signature dont s est dans la moitié haute : ECDSA l'accepte aussi", "ok",
        env=envelope(statement(), root, signer=sign_high_s))

    # --- Signature ------------------------------------------------------------
    tampered = dict(good)
    tampered["payload"] = b64(dumps(statement(version="9.9.9")))
    add("payload_altered", "déclaration réécrite après signature", "bad_signature",
        env=tampered, version="9.9.9")

    flipped = bytearray(base64.b64decode(good["signatures"][0]["sig"]))
    flipped[10] ^= 0x01
    add("signature_altered", "un bit de la signature inversé", "bad_signature",
        env=with_signature(good, bytes(flipped)))
    add("signature_der_encoded", "signature au format DER au lieu de r||s brut", "bad_signature",
        env=envelope(statement(), root, signer=sign_der))
    add("signature_truncated", "signature de 63 octets", "bad_signature",
        env=with_signature(direct, base64.b64decode(direct["signatures"][0]["sig"])[:63]))
    add("unknown_key", "enveloppe signée par une clé absente de l'ancre, sans délégation",
        "unknown_key", env=envelope(statement(), stranger))
    add("keyid_borrowed", "clé inconnue qui se présente sous le keyid de la racine",
        "bad_signature", env=envelope(statement(), stranger, kid=keyid(root)))
    wrong_alg = copy.deepcopy(direct)
    wrong_alg["signatures"][0]["alg"] = "EdDSA"
    add("wrong_alg", "signature annoncée sous un autre algorithme : ignorée", "unknown_key",
        env=wrong_alg)

    # r ou s hors de [1, n-1] : refusés avant tout calcul sur la courbe.
    r, s = rs(root, base64.b64decode(direct["payload"]))
    top = (1 << 256) - 1
    for name, about, pair in (
            ("signature_r_zero", "r = 0", (0, s)),
            ("signature_s_zero", "s = 0", (r, 0)),
            ("signature_r_equals_order", "r = n (congru à 0)", (ORDER, s)),
            ("signature_s_equals_order", "s = n (congru à 0)", (r, ORDER)),
            ("signature_r_above_order", "r = 2^256 - 1", (top, s)),
            ("signature_s_above_order", "s = 2^256 - 1", (r, top)),
            ("signature_all_zero", "r = s = 0", (0, 0))):
        add(name, f"signature hors bornes : {about}", "bad_signature",
            env=with_signature(direct, raw(*pair)))

    # --- Clé publique ---------------------------------------------------------
    numbers = root.public_key().public_numbers()
    for name, about, der in (
            ("public_key_off_curve", "racine dont le point n'est pas sur la courbe (Y + 1)",
             moved_point(root, y=(numbers.y + 1) % FIELD)),
            ("public_key_zero_point", "racine dont le point est (0, 0)", moved_point(root, x=0, y=0)),
            ("public_key_coordinate_above_field", "racine dont l'abscisse dépasse le corps (X = p)",
             moved_point(root, x=FIELD)),
            ("public_key_compressed", "racine en point compressé : seul 0x04||X||Y est lu",
             spki(root, serialization.PublicFormat.SubjectPublicKeyInfo)[:26]
             + root.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.CompressedPoint)),
            ("public_key_trailing_bytes", "racine suivie d'octets en trop", spki(root) + b"\x00"),
            ("public_key_p384", "racine sur la courbe P-384", spki(p384))):
        bad = {"schema": "dm-trust/1", "roots": [root_entry(der)]}
        add(name, f"{about} : écartée, l'ancre n'a plus de racine", "no_trust", env=direct, trust=bad)
    off_curve = b64(moved_point(dm, y=(dm.public_key().public_numbers().y + 1) % FIELD))
    add("delegation_public_key_off_curve", "délégation pour un point hors de la courbe",
        "delegation_invalid",
        env=envelope(statement(), dm, delegation(
            root, dm, public_key=off_curve, keyid=keyid_of(base64.b64decode(off_curve)))))

    # --- Délégation -----------------------------------------------------------
    add("delegation_expired", "délégation dont la fenêtre est passée", "delegation_invalid",
        env=envelope(statement(), dm, delegation(root, dm, PAST_FROM, PAST_TO)))
    add("delegation_not_yet_valid", "délégation dont la fenêtre n'a pas commencé",
        "delegation_invalid",
        env=envelope(statement(), dm, delegation(root, dm, "2027-01-01T00:00:00Z",
                                                 "2028-01-01T00:00:00Z")))
    add("delegation_by_unknown_root", "délégation signée par une racine absente de l'ancre",
        "delegation_invalid", env=envelope(statement(), dm, delegation(other_root, dm)))
    add("delegation_keyid_mismatch", "délégation dont le keyid n'est pas celui de la clé portée",
        "delegation_invalid",
        env=envelope(statement(), dm, delegation(root, dm, keyid=keyid(stranger))))
    add("delegation_for_another_key", "délégation valide, mais pour une autre clé que la signataire",
        "unknown_key", env=envelope(statement(), stranger, delegation(root, dm)))
    add("delegation_bad_dates", "délégation aux dates illisibles", "delegation_invalid",
        env=envelope(statement(), dm, delegation(root, dm, not_after="demain")))
    add("delegation_p384_key", "délégation pour une clé P-384 : seule P-256 est acceptée",
        "delegation_invalid",
        env=envelope(statement(), dm, delegation(root, dm, public_key=b64(spki(p384)))))
    add("delegation_wrong_schema", "délégation d'un autre schéma, correctement signée",
        "delegation_invalid",
        env=envelope(statement(), dm, delegation(root, dm, schema="dm-signing-delegation/2")))
    add("delegation_with_offset_dates", "délégation aux dates à décalage horaire, encore valide",
        "ok", env=envelope(statement(), dm, delegation(
            root, dm, "2026-11-15T13:30:00+02:00", "2026-11-15T11:30:00-01:00")))
    add("delegation_ended_an_hour_ago", "délégation close une heure plus tôt, décalage compris",
        "delegation_invalid", env=envelope(statement(), dm, delegation(
            root, dm, "2026-01-01T00:00:00Z", "2026-11-15T12:00:00+01:00")))

    # --- Déclaration ----------------------------------------------------------
    add("wrong_kind", "enveloppe de build (CI) présentée comme paquet servi", "wrong_kind",
        env=envelope(statement(kind="build"), dm, delegation(root, dm)))
    add("wrong_sha256", "les octets téléchargés ne sont pas ceux qui ont été signés",
        "digest_mismatch", bytes=b64(PACKAGE + b"x"))
    add("wrong_version", "la directive annonce une autre version que celle signée",
        "version_mismatch", version="0.4.0")
    add("native_version_is_not_the_target", "l'écriture numérique de description.xml n'est pas "
        "le numéro signé", "version_mismatch", version="0.3.99999.2.12")
    # Lien avec le plugin : l'identifiant d'extension quand la déclaration le porte, sinon le slug.
    add("extension_other", "paquet signé pour une autre extension", "extension_mismatch",
        env=envelope(statement(extension_id="org.example.autre"), root))
    add("extension_id_slug_differs", "extension_id présent et juste : le slug n'est pas comparé",
        "ok", env=direct, slug="autre-nom-au-catalogue")
    add("extension_id_caller_has_none",
        "la déclaration porte un extension_id, l'appelant n'en donne pas", "extension_mismatch",
        env=direct, extension_id=None)
    add("extension_id_empty_in_statement",
        "extension_id présent mais vide : refus, pas de repli sur le slug", "extension_mismatch",
        env=envelope(statement(extension_id=""), root))
    add("no_extension_id_slug_ok", "déclaration sans extension_id : repli sur le slug, qui correspond",
        "ok", env=envelope(statement(extension_id=None), root))
    add("wrong_slug", "déclaration sans extension_id, signée pour un autre plugin", "slug_mismatch",
        env=envelope(statement(extension_id=None), root), slug="mirai-matisse")
    add("statement_wrong_schema", "déclaration d'un autre schéma, correctement signée", "malformed",
        env=envelope(statement(schema="dm-package-statement/2"), root))

    # --- Enveloppe et ancre ---------------------------------------------------
    add("no_envelope", "la directive ne porte pas d'enveloppe", "no_envelope", env=None)
    add("envelope_wrong_schema", "enveloppe d'un autre schéma", "malformed",
        env=dict(good, schema="dm-package-signature/2"))
    add("envelope_not_json", "enveloppe illisible", "malformed", env="{pas du json")
    add("payload_not_base64", "payload qui n'est pas du base64", "malformed",
        env=dict(good, payload="@@@@"))
    add("payload_urlsafe_base64", "payload en base64 « URL » : alphabet refusé", "malformed",
        env=dict(direct, payload=direct["payload"].replace("+", "-").replace("/", "_")
                 if set("+/") & set(direct["payload"]) else "-_-_"))
    add("trust_empty", "ancre sans racine", "no_trust", trust={"schema": "dm-trust/1", "roots": []})
    add("trust_wrong_schema", "ancre d'un autre schéma", "no_trust",
        trust=dict(anchor, schema="dm-trust/2"))
    add("trust_unreadable", "ancre illisible", "no_trust", trust="")
    bad_root = copy.deepcopy(anchor)
    bad_root["roots"][0]["keyid"] = keyid(stranger)
    add("trust_root_keyid_mismatch", "racine dont le keyid n'est pas celui de sa clé : écartée",
        "no_trust", trust=bad_root)

    doc = {
        "_readme": "Vecteurs de test produits par generate_package_signature_vectors.py "
                   f"(cryptography {cryptography.__version__}). CLES DE TEST tirees au hasard, "
                   "cles privees jamais ecrites. Ne pas modifier a la main : relancer le script.",
        "generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vectors": vectors,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{len(vectors)} vecteurs écrits dans {OUT}")


if __name__ == "__main__":
    main()
