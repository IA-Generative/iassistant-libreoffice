"""Signature d'origine d'un paquet publié : `scripts/package_signature.py sign`
écrit une enveloppe que `verify` accepte, et refuse dès que le fichier, le
numéro attendu ou la clé change.

La paire de clés est tirée pour le test, dans un dossier temporaire : aucune
clé n'est lue dans le dépôt. Sans la bibliothèque `cryptography` (outillage de
la CI, jamais embarquée dans l'OXT), le module est ignoré."""

import base64
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "package_signature.py")
SLUG, VERSION = "mirai-libreoffice", "0.4.0-rc.12"
GITHUB = {
    "GITHUB_REPOSITORY": "org/depot",
    "GITHUB_REF": "refs/heads/develop",
    "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
    "GITHUB_WORKFLOW": "Pre-release",
    "GITHUB_WORKFLOW_REF": "org/depot/.github/workflows/prerelease.yml@refs/heads/develop",
    "GITHUB_RUN_ID": "4242",
}


def _run(*args, env=None):
    clean = {k: v for k, v in os.environ.items()
             if k != "PACKAGE_SIGNING_KEY" and not k.startswith("GITHUB_")}
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                          env={**clean, **(env or {})})


def _pair(curve=None):
    key = ec.generate_private_key(curve or ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public


@pytest.fixture
def signed(tmp_path):
    private, public = _pair()
    package = tmp_path / f"{SLUG}-{VERSION}.oxt"
    package.write_bytes(b"paquet de test, pas une archive")
    key_file = tmp_path / "editeur.pub.pem"
    key_file.write_bytes(public)
    res = _run("sign", str(package), "--slug", SLUG, "--version", VERSION,
               env={**GITHUB, "PACKAGE_SIGNING_KEY": private})
    assert res.returncode == 0, res.stderr
    assert "PRIVATE KEY" not in res.stdout + res.stderr
    return package, key_file


def _statement(package):
    envelope = json.loads((package.parent / (package.name + ".sig.json")).read_text())
    return envelope, json.loads(base64.b64decode(envelope["payload"]))


def test_the_envelope_states_what_was_built_and_where(signed):
    package, _ = signed
    envelope, statement = _statement(package)
    assert envelope["schema"] == "dm-package-signature/1"
    assert [s["alg"] for s in envelope["signatures"]] == ["ES256"]
    assert len(base64.b64decode(envelope["signatures"][0]["sig"])) == 64
    assert len(envelope["signatures"][0]["keyid"]) == 16
    assert statement["schema"] == "dm-package-statement/1"
    assert (statement["kind"], statement["slug"], statement["version"]) == ("build", SLUG, VERSION)
    assert statement["file"] == package.name
    assert statement["size"] == package.stat().st_size
    assert statement["source"] == {
        "repository": "org/depot", "ref": "refs/heads/develop",
        "commit": GITHUB["GITHUB_SHA"], "run_id": "4242",
        "workflow": GITHUB["GITHUB_WORKFLOW_REF"],
    }


def test_a_signed_package_is_accepted(signed):
    package, key_file = signed
    res = _run("verify", str(package), "--public-key", str(key_file),
               "--slug", SLUG, "--version", VERSION)
    assert res.returncode == 0, res.stderr
    assert "VALIDE" in res.stdout and "refs/heads/develop" in res.stdout


def _refusal(package, key_file, *extra):
    res = _run("verify", str(package), "--public-key", str(key_file), *extra)
    assert res.returncode == 1
    return res.stderr


def test_a_changed_file_is_refused(signed):
    package, key_file = signed
    package.write_bytes(package.read_bytes() + b"x")
    assert "[digest_mismatch]" in _refusal(package, key_file)


def test_another_version_or_slug_is_refused(signed):
    package, key_file = signed
    assert "[version_mismatch]" in _refusal(package, key_file, "--version", "0.4.0")
    assert "[slug_mismatch]" in _refusal(package, key_file, "--slug", "autre")


def test_another_key_is_refused(signed, tmp_path):
    package, _ = signed
    other = tmp_path / "autre.pub.pem"
    other.write_bytes(_pair()[1])
    assert "[unknown_key]" in _refusal(package, other)


def test_a_rewritten_statement_is_refused(signed):
    package, key_file = signed
    envelope, statement = _statement(package)
    statement["version"] = "9.9.9"
    envelope["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()
    (package.parent / (package.name + ".sig.json")).write_text(json.dumps(envelope))
    assert "[bad_signature]" in _refusal(package, key_file)


def test_a_build_envelope_is_not_a_served_one(signed):
    package, key_file = signed
    assert "[wrong_kind]" in _refusal(package, key_file, "--kind", "served")


def test_without_a_key_nothing_is_signed(tmp_path):
    package = tmp_path / "p.oxt"
    package.write_bytes(b"x")
    res = _run("sign", str(package), "--slug", SLUG, "--version", VERSION)
    assert res.returncode == 2
    assert not (tmp_path / "p.oxt.sig.json").exists()


def test_a_key_on_another_curve_is_refused_without_being_shown(tmp_path):
    package = tmp_path / "p.oxt"
    package.write_bytes(b"x")
    private, _ = _pair(ec.SECP384R1())
    res = _run("sign", str(package), "--slug", SLUG, "--version", VERSION,
               env={"PACKAGE_SIGNING_KEY": private})
    assert res.returncode == 2
    assert not (tmp_path / "p.oxt.sig.json").exists()
    body = "".join(private.splitlines()[1:-1])
    assert body[:24] not in res.stdout + res.stderr
