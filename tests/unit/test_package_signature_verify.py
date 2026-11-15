"""Vérification de la signature d'un paquet servi par un Device Management.

Le module du plugin est en Python pur ; il est confronté ici à des vecteurs
écrits par une implémentation indépendante, la bibliothèque `cryptography`
(tests/fixtures/generate_package_signature_vectors.py), comme le fait le DM.
Les vecteurs sont committés : ces tests n'ont besoin d'aucune bibliothèque."""

import ast
import base64
import calendar
import datetime
import hashlib
import json
import os

import pytest

from src.mirai import package_signature as ps

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
with open(os.path.join(ROOT, "tests", "fixtures", "package_signature_vectors.json"),
          encoding="utf-8") as _fh:
    VECTORS = json.load(_fh)["vectors"]
BY_NAME = {vector["name"]: vector for vector in VECTORS}


def _epoch(text):
    return calendar.timegm(datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").timetuple())


def _verify(vector, **over):
    args = {"envelope": vector["envelope"], "data": base64.b64decode(vector["bytes"]),
            "version": vector["version"], "extension_id": vector["extension_id"],
            "slug": vector["slug"], "trust": vector["trust"], "now": _epoch(vector["now"])}
    args.update(over)
    return ps.verify_package_signature(**args)


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_the_module_agrees_with_the_independent_implementation(vector):
    result = _verify(vector)
    assert (result.ok, result.reason) == (vector["expect"]["ok"], vector["expect"]["reason"]), \
        f"{vector['about']} : {result.detail}"


def test_the_vectors_cover_every_case_the_plugin_must_tell_apart():
    required = {
        "valid_root_direct", "valid_delegated", "payload_altered", "wrong_version",
        "wrong_sha256", "delegation_expired", "unknown_key", "extension_other",
        "signature_r_zero", "signature_s_zero", "signature_r_equals_order",
        "signature_s_equals_order", "public_key_off_curve",
    }
    assert required <= set(BY_NAME)
    assert {v["expect"]["reason"] for v in VECTORS} == {
        ps.OK, ps.NO_ENVELOPE, ps.MALFORMED, ps.UNKNOWN_KEY, ps.BAD_SIGNATURE,
        ps.DELEGATION_INVALID, ps.WRONG_KIND, ps.DIGEST_MISMATCH, ps.VERSION_MISMATCH,
        ps.EXTENSION_MISMATCH, ps.SLUG_MISMATCH, ps.NO_TRUST}


def test_an_accepted_envelope_names_its_signer_and_statement():
    result = _verify(BY_NAME["valid_delegated"])
    assert result.ok and result.reason == "ok"
    assert result.keyid == BY_NAME["valid_delegated"]["envelope"]["signatures"][0]["keyid"]
    assert result.statement["kind"] == "served"
    assert "notes/résumé — été.txt" in result.statement["changed_files"]


def test_a_delegation_is_only_valid_inside_its_dates():
    vector = BY_NAME["valid_delegated"]
    assert _verify(vector, now=_epoch("2026-01-01T00:00:00Z")).ok
    assert _verify(vector, now=_epoch("2027-01-01T00:00:00Z")).ok
    assert _verify(vector, now=_epoch("2025-12-31T23:59:59Z")).reason == "delegation_invalid"
    assert _verify(vector, now=_epoch("2027-01-01T00:00:01Z")).reason == "delegation_invalid"


def test_the_anchor_is_read_before_the_envelope():
    """Sans ancre utilisable rien n'est vérifiable, enveloppe présente ou non."""
    vector = BY_NAME["no_envelope"]
    assert _verify(vector, trust=None).reason == "no_trust"
    assert _verify(vector, trust={"schema": "dm-trust/1"}).reason == "no_trust"


@pytest.mark.parametrize("over", [
    {"envelope": 42}, {"envelope": []}, {"envelope": {"schema": "dm-package-signature/1"}},
    {"envelope": {"schema": "dm-package-signature/1", "payload": "", "signatures": [None, 3]}},
    {"data": None}, {"data": "texte"}, {"version": None}, {"version": 12}, {"trust": 3.5},
    {"trust": b"\xff\xfe"}, {"extension_id": 7}, {"now": 0},
])
def test_no_input_makes_the_verification_raise_or_accept(over):
    result = _verify(BY_NAME["valid_delegated"], **over)
    assert result.ok is False and result.reason != "ok"


# ─── Arithmétique de la courbe et lecture de la clé ─────────────────────────

# RFC 6979, annexe A.2.5 : clé P-256 et signatures SHA-256 de « sample » et « test ».
RFC6979_KEY = (0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6,
               0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299)
RFC6979 = [
    (b"sample", "EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716",
     "F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8"),
    (b"test", "F1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367",
     "019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083"),
]


@pytest.mark.parametrize("message, r, s", RFC6979)
def test_published_signatures_verify(message, r, s):
    signature = bytes.fromhex(r + s)
    assert ps.verify_es256(RFC6979_KEY, signature, message)
    assert not ps.verify_es256(RFC6979_KEY, signature, message + b".")
    assert not ps.verify_es256((RFC6979_KEY[0], ps._P - RFC6979_KEY[1]), signature, message)


def test_the_generator_has_the_order_of_the_curve():
    generator = (ps._GX, ps._GY, 1)
    assert ps._on_curve(ps._GX, ps._GY)
    assert ps._affine_x(ps._multiply(ps._N, generator)) is None
    assert ps._affine_x(ps._multiply(ps._N - 1, generator)) == ps._GX
    assert ps._affine_x(ps._multiply(ps._N + 1, generator)) == ps._GX
    assert ps._affine_x(ps._add(ps._multiply(2, generator), ps._multiply(3, generator))) \
        == ps._affine_x(ps._multiply(5, generator))


def test_r_and_s_are_bounded_before_any_curve_computation():
    r, s = (int(part, 16) for part in RFC6979[0][1:])

    def holds(r_value, s_value):
        return ps.verify_es256(RFC6979_KEY, r_value.to_bytes(32, "big")
                               + s_value.to_bytes(32, "big"), b"sample")
    assert holds(r, s)
    assert holds(r, ps._N - s), "s et n - s signent le même message"
    for bad_r, bad_s in ((0, s), (r, 0), (ps._N, s), (r, ps._N), (2**256 - 1, s), (0, 0)):
        assert not holds(bad_r, bad_s)
    assert not ps.verify_es256(RFC6979_KEY, bytes(63), b"sample")
    assert not ps.verify_es256(RFC6979_KEY, bytes(65), b"sample")
    assert not ps.verify_es256(RFC6979_KEY, "pas des octets", b"sample")


def _spki(x, y, lead=0x04):
    return ps._SPKI_PREFIX + bytes([lead]) + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def test_only_an_uncompressed_p256_point_on_the_curve_is_a_public_key():
    x, y = RFC6979_KEY
    assert ps.public_point(_spki(x, y)) == (x, y)
    assert ps.public_point(bytearray(_spki(x, y))) == (x, y)
    assert ps.public_point(_spki(x, (y + 1) % ps._P)) is None, "hors de la courbe"
    assert ps.public_point(_spki(0, 0)) is None
    assert ps.public_point(_spki(ps._P, y)) is None, "abscisse hors du corps"
    assert ps.public_point(_spki(x, y, lead=0x02)) is None, "point compressé"
    assert ps.public_point(_spki(x, y, lead=0x00)) is None
    assert ps.public_point(_spki(x, y) + b"\x00") is None
    assert ps.public_point(_spki(x, y)[:-1]) is None
    assert ps.public_point(b"\x30" + _spki(x, y)[1:-1] + b"\x00") is None
    other_curve = bytearray(_spki(x, y))
    other_curve[22] ^= 0x01
    assert ps.public_point(bytes(other_curve)) is None, "autre identifiant de courbe"
    assert ps.public_point("texte") is None and ps.public_point(None) is None


def test_the_keyid_is_the_start_of_the_sha256_of_the_key():
    spki = _spki(*RFC6979_KEY)
    assert ps.key_id(spki) == hashlib.sha256(spki).hexdigest()[:16]
    root = BY_NAME["valid_root_direct"]["trust"]["roots"][0]
    assert ps.key_id(base64.b64decode(root["public_key"])) == root["keyid"]


@pytest.mark.parametrize("text, seconds", [
    ("2026-11-15T12:00:00Z", 1794744000), ("2026-11-15T12:00:00", 1794744000),
    ("2026-11-15T13:00:00+01:00", 1794744000), ("2026-11-15T07:30:00-04:30", 1794744000),
    ("2026-11-15T12:00:00.250Z", 1794744000),
])
def test_dates_are_read_with_their_offset(text, seconds):
    assert ps._parse_date(text) == seconds


@pytest.mark.parametrize("text", ["demain", "2026-11-15", "2026-13-01T00:00:00Z",
                                  "2026-02-30T00:00:00Z", "2026-11-15 12:00:00Z", "", None, 12])
def test_unreadable_dates_are_refused(text):
    assert ps._parse_date(text) is None


@pytest.mark.parametrize("text", ["QUJD", "", "QQ==", "QUI="])
def test_strict_base64_is_decoded(text):
    assert ps._base64(text) == base64.b64decode(text)


@pytest.mark.parametrize("text", ["QUJ", "QU JD", "QUJD\n", "QUJ-", "QUJ_", "Q===", "=QUJ", None, b"QUJD"])
def test_anything_else_is_not_base64(text):
    assert ps._base64(text) is None


# ─── Ancre du paquet installé ───────────────────────────────────────────────

def test_a_package_without_anchor_has_no_trust_file(tmp_path):
    assert ps.read_trust(str(tmp_path)) is None
    assert ps.read_trust(None) is None
    assert ps.read_trust("") is None


def test_the_anchor_is_the_text_of_dm_trust_json(tmp_path):
    (tmp_path / "dm-trust.json").write_text('{"schema": "dm-trust/1"}', encoding="utf-8")
    assert ps.read_trust(str(tmp_path)) == '{"schema": "dm-trust/1"}'


def test_an_anchor_that_cannot_be_read_still_counts_as_present(tmp_path):
    """Présente mais illisible : la signature reste exigée, et sera refusée."""
    (tmp_path / "dm-trust.json").mkdir()
    assert ps.read_trust(str(tmp_path)) == ""
    other = tmp_path / "autre"
    other.mkdir()
    (other / "dm-trust.json").write_bytes(b"\xff\xfe\x00")
    assert ps.read_trust(str(other)) == ""
    assert ps.verify_package_signature(None, b"", "1.0.0", "id", "slug", "").reason == "no_trust"


# ─── Le module tourne dans le Python de LibreOffice ─────────────────────────

_STANDARD_LIBRARY = {"base64", "binascii", "calendar", "datetime", "hashlib", "json", "os",
                     "re", "time"}


def _source():
    with open(ps.__file__, encoding="utf-8") as fh:
        return fh.read()


def test_the_module_needs_only_the_standard_library():
    imported = set()
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or ".").split(".")[0])
    assert imported <= _STANDARD_LIBRARY


def test_the_module_is_written_for_old_pythons_too():
    """LibreOffice 25.8 embarque Python 3.11, les versions plus anciennes un
    Python plus ancien, et sous Linux c'est celui du système."""
    ast.parse(_source(), feature_version=(3, 6))


def test_the_module_agrees_with_cryptography_on_fresh_signatures():
    """Contrôle croisé sur des signatures tirées pendant le test, quand la
    bibliothèque de l'outillage est installée."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    for _ in range(8):
        key = ec.generate_private_key(ec.SECP256R1())
        spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
        numbers = key.public_key().public_numbers()
        point = ps.public_point(spki)
        assert point == (numbers.x, numbers.y)
        message = os.urandom(64)
        r, s = utils.decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        assert ps.verify_es256(point, signature, message)
        assert not ps.verify_es256(point, signature, message + b"x")
        flipped = bytearray(signature)
        flipped[os.urandom(1)[0] % 64] ^= 0x10
        assert not ps.verify_es256(point, bytes(flipped), message)


def test_the_vector_generator_shares_nothing_with_the_module():
    path = os.path.join(ROOT, "tests", "fixtures", "generate_package_signature_vectors.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module or "." for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith(("src", "mirai", ".")) for name in imported)
