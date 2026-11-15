"""Règle de transition : un paquet installé qui porte `dm-trust.json` n'installe
une mise à jour reçue par directive que si elle est signée ; sans ce fichier,
rien ne change. Un refus n'installe rien et se dit, avec sa raison."""

import base64
import json
import os
import re
import types

import pytest

from src.mirai import local_config
from tests.stubs.uno_stubs import install

install()

from src.mirai import entrypoint
from src.mirai.shell import update_signature

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
with open(os.path.join(ROOT, "tests", "fixtures", "package_signature_vectors.json"),
          encoding="utf-8") as _fh:
    VECTORS = {vector["name"]: vector for vector in json.load(_fh)["vectors"]}
# Signée par la racine elle-même : aucune délégation, donc aucune date en jeu.
SIGNED = VECTORS["valid_root_direct"]
PACKAGE = base64.b64decode(SIGNED["bytes"])
EXTENSION_ID = SIGNED["extension_id"]


class Job:
    """Ce que la vérification demande à la coquille, sans LibreOffice."""

    def __init__(self, package_dir, config_path="/config/mirai-libreoffice/config.json"):
        self.package_dir = package_dir
        self.config_path = config_path
        self.reports = []
        self.events = []
        self.runtime = types.SimpleNamespace(update_launch_blocked=set())

    def _package_root_dir(self):
        return self.package_dir

    def _get_config_from_file(self, key, default):
        return self.config_path if key == "config_path" else default

    def _report_update_status(self, *args):
        self.reports.append(args)

    def _send_telemetry(self, name, attributes):
        self.events.append((name, attributes))


def _directive(**changes):
    directive = {"action": "update", "target_version": SIGNED["version"], "campaign_id": 7,
                 "signature": SIGNED["envelope"]}
    directive.update(changes)
    return directive


@pytest.fixture
def anchored(tmp_path):
    (tmp_path / "dm-trust.json").write_text(json.dumps(SIGNED["trust"]), encoding="utf-8")
    return Job(str(tmp_path))


def _refuses(job, directive, data=PACKAGE):
    return update_signature.refuses(job, directive, data, EXTENSION_ID, "0.3.0")


def test_a_package_without_anchor_asks_for_no_signature(tmp_path):
    job = Job(str(tmp_path))
    assert update_signature.required(job) is False
    assert _refuses(job, _directive(signature=None)) is False
    assert (job.reports, job.events, job.runtime.update_launch_blocked) == ([], [], set())


def test_a_package_whose_folder_is_unknown_asks_for_no_signature():
    job = Job(None)
    assert update_signature.required(job) is False
    assert _refuses(job, _directive(signature=None)) is False


def test_an_anchored_package_accepts_a_signed_update(anchored):
    assert update_signature.required(anchored) is True
    assert _refuses(anchored, _directive()) is False
    assert (anchored.reports, anchored.events) == ([], [])
    assert anchored.runtime.update_launch_blocked == set()


@pytest.mark.parametrize("change, data, reason", [
    ({"signature": None}, PACKAGE, "no_envelope"),
    ({"signature": ""}, PACKAGE, "no_envelope"),
    ({"signature": "{pas du json"}, PACKAGE, "malformed"),
    ({}, PACKAGE + b"x", "digest_mismatch"),
    ({"target_version": "0.4.0"}, PACKAGE, "version_mismatch"),
    ({"signature": VECTORS["unknown_key"]["envelope"]}, PACKAGE, "unknown_key"),
    ({"signature": VECTORS["payload_altered"]["envelope"], "target_version": "9.9.9"},
     PACKAGE, "bad_signature"),
    ({"signature": VECTORS["wrong_kind"]["envelope"]}, PACKAGE, "wrong_kind"),
    ({"signature": VECTORS["extension_other"]["envelope"]}, PACKAGE, "extension_mismatch"),
    ({"signature": VECTORS["delegation_expired"]["envelope"]}, PACKAGE, "delegation_invalid"),
])
def test_an_anchored_package_refuses_and_says_why(anchored, change, data, reason):
    directive = _directive(**change)
    target = directive["target_version"]
    assert _refuses(anchored, directive, data) is True
    assert anchored.reports == [(7, "failed", "0.3.0", "", f"signature: {reason}")]
    assert anchored.events == [("UpdateInstallFailed", {
        "version_after": target, "campaign_id": "7", "route": "directed",
        "signature_reason": reason})]
    assert anchored.runtime.update_launch_blocked == {target}


def test_an_unreadable_anchor_refuses_everything(tmp_path):
    (tmp_path / "dm-trust.json").write_text("{", encoding="utf-8")
    job = Job(str(tmp_path))
    assert update_signature.required(job) is True
    assert _refuses(job, _directive()) is True
    assert job.reports[0][4] == "signature: no_trust"


def test_a_statement_without_extension_id_is_bound_by_the_slug(tmp_path):
    vector = VECTORS["no_extension_id_slug_ok"]
    (tmp_path / "dm-trust.json").write_text(json.dumps(vector["trust"]), encoding="utf-8")
    directive = _directive(signature=vector["envelope"])
    assert _refuses(Job(str(tmp_path)), directive) is False
    other = Job(str(tmp_path), config_path="/config/autre-plugin/config.json?profile=prod")
    assert _refuses(other, directive) is True
    assert other.reports[0][4] == "signature: slug_mismatch"
    unnamed = Job(str(tmp_path), config_path="/config/config.json")
    assert _refuses(unnamed, directive) is True


@pytest.mark.parametrize("path, slug", [
    ("/config/mirai-libreoffice/config.json", "mirai-libreoffice"),
    ("/config/mirai-libreoffice/config.json?profile=prod", "mirai-libreoffice"),
    ("config/mirai-libreoffice/config.json", "mirai-libreoffice"),
    ("/config/config.json", ""), ("/autre/mirai-libreoffice/config.json", ""), ("", ""),
    (None, ""),
])
def test_the_slug_is_the_name_the_plugin_asks_the_dm_under(path, slug):
    assert local_config.dm_slug(path) == slug


def test_the_extension_compares_the_identifier_its_package_declares():
    with open(os.path.join(ROOT, "oxt", "description.xml"), encoding="utf-8") as fh:
        declared = re.search(r'<identifier\s+value="([^"]+)"', fh.read()).group(1)
    assert entrypoint._EXTENSION_IDENTIFIER == declared == EXTENSION_ID
