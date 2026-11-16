"""Le paquet neutre que la CI publie : `02-build-oxt.sh --neutral --version …`.

Aucune valeur d'organisation : la liste de DM est l'adresse d'exemple que le
plugin sait écarter, le flux de mise à jour en dérive, le modèle de
configuration ne garde que des repères. Le numéro demandé est inscrit dans le
paquet sans toucher aux fichiers suivis."""

import importlib.util
import json
import os
import shutil
import subprocess
import zipfile

import pytest

from src.mirai import local_config, package_version

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_SCRIPT = os.path.join(ROOT, "scripts", "02-build-oxt.sh")
VERSION = "0.4.0-rc.12"

_spec = importlib.util.spec_from_file_location(
    "neutral_package", os.path.join(ROOT, "scripts", "neutral_package.py"))
neutral_package = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neutral_package)

pytestmark = pytest.mark.skipif(shutil.which("zip") is None,
                                reason="binaire 'zip' indisponible — build .oxt impossible")


def _build(out, *args, env=None):
    return subprocess.run(["bash", BUILD_SCRIPT, "--output", str(out), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=240,
                          env={**os.environ, **(env or {})})


@pytest.fixture(scope="module")
def neutral_oxt(tmp_path_factory):
    out = tmp_path_factory.mktemp("neutral") / f"mirai-libreoffice-{VERSION}.oxt"
    res = _build(out, "--neutral", "--version", VERSION)
    assert res.returncode == 0, res.stdout + res.stderr
    with zipfile.ZipFile(out) as archive:
        return {name: archive.read(name).decode("utf-8")
                for name in ("config.default.json", "description.xml", "dm-manifest.json",
                             "dm-config.json", "registration/license.txt")}


def test_the_dm_list_is_the_example_address_the_plugin_discards(neutral_oxt):
    config = json.loads(neutral_oxt["config.default.json"])
    assert config["bootstrap_urls"] == [local_config.EXAMPLE_DM_URL]
    assert "bootstrap_url" not in config
    assert config["enabled"] is True
    assert local_config.dm_urls(config["bootstrap_urls"]) == []


def test_the_native_update_feed_derives_from_the_example_address(neutral_oxt):
    assert ('<src xlink:href="https://dm.example.invalid/catalog/mirai-libreoffice/update.xml"/>'
            in neutral_oxt["description.xml"])
    assert neutral_oxt["description.xml"].count("<src ") == 1


def test_the_config_template_keeps_no_real_address(neutral_oxt):
    template = json.loads(neutral_oxt["dm-config.json"])
    assert template["default"]["portal_url"] == "${{PORTAL_URL}}"
    assert template["default"]["doc_url"] == "${{DOC_URL}}"
    assert template["prod"]["llm_base_urls"] == "${{LLM_BASE_URL}}"
    assert template["local"]["llm_base_urls"].startswith("http://localhost")
    assert neutral_package.real_hosts(neutral_oxt["dm-config.json"]) == []


def test_the_version_is_written_in_both_forms(neutral_oxt):
    native = package_version.native_version(VERSION)
    assert f'<version value="{native}"/>' in neutral_oxt["description.xml"]
    manifest = json.loads(neutral_oxt["dm-manifest.json"])
    assert manifest["version"] == VERSION
    assert manifest["changelog"][0]["version"] == VERSION
    assert VERSION in neutral_oxt["registration/license.txt"]


def test_a_neutral_build_refuses_a_real_configuration(tmp_path):
    res = _build(tmp_path / "x.oxt", "--neutral", "--config",
                 os.path.join(ROOT, "config", "profiles", "config.default.dev.json"))
    assert res.returncode != 0
    assert not (tmp_path / "x.oxt").exists()


def test_a_neutral_build_refuses_a_forced_feed_address(tmp_path):
    res = _build(tmp_path / "x.oxt", "--neutral",
                 env={"MIRAI_UPDATE_FEED_URL": "https://dm.example.com/update.xml"})
    assert res.returncode != 0
    assert "NON NEUTRE" in res.stderr
    assert not (tmp_path / "x.oxt").exists()


def test_an_unknown_version_form_is_refused(tmp_path):
    res = _build(tmp_path / "x.oxt", "--neutral", "--version", "0.4.0-beta.1")
    assert res.returncode != 0
    assert not (tmp_path / "x.oxt").exists()


def _tracked_description():
    with open(os.path.join(ROOT, "oxt", "description.xml"), encoding="utf-8") as fh:
        return fh.read()


def test_a_real_local_build_can_carry_a_version_too(tmp_path):
    """Le numéro n'existe que dans le paquet : le fichier suivi ne change pas."""
    out = tmp_path / "dev.oxt"
    before = _tracked_description()
    res = _build(out, "--version", "0.4.0-dev.3", "--config",
                 os.path.join(ROOT, "config", "profiles", "config.default.dev.json"))
    assert res.returncode == 0, res.stdout + res.stderr
    assert _tracked_description() == before
    with zipfile.ZipFile(out) as archive:
        assert '<version value="0.3.99999.1.3"/>' in archive.read("description.xml").decode()
        assert json.loads(archive.read("config.default.json"))["bootstrap_urls"] == [
            "http://localhost:8089"]
        assert "mirai.interieur.gouv.fr" in archive.read("dm-config.json").decode()


def _package(tmp_path, config=None, dm_config=None, feed="https://dm.example.invalid/u.xml"):
    (tmp_path / "config.default.json").write_text(json.dumps(
        config or {"enabled": True, "bootstrap_urls": ["https://dm.example.invalid"]}))
    (tmp_path / "dm-config.json").write_text(json.dumps(dm_config or {"default": {}}))
    (tmp_path / "description.xml").write_text(
        f'<description><update-information><src xlink:href="{feed}"/></update-information>'
        "</description>")
    return str(tmp_path)


def test_check_accepts_a_neutral_package(tmp_path):
    assert neutral_package.check(_package(tmp_path)) == []


@pytest.mark.parametrize("change, word", [
    ({"config": {"bootstrap_urls": ["https://dm.example.com"]}}, "liste de DM"),
    ({"config": {"bootstrap_urls": []}}, "liste de DM"),
    ({"config": {"bootstrap_urls": ["https://dm.example.invalid"],
                 "bootstrap_url": "https://dm.example.com"}}, "liste de DM"),
    ({"config": {"bootstrap_urls": ["https://dm.example.invalid"],
                 "llm_base_urls": "x"}}, "hors transport"),
    ({"config": {"bootstrap_urls": ["https://dm.example.invalid"],
                 "bootstrap_insecure_urls": ["dm.example.com"]}}, "hors transport"),
    ({"dm_config": {"prod": {"links": [{"url": "https://portail.example.com/x"}]}}},
     "portail.example.com"),
    ({"feed": "https://dm.example.com/u.xml"}, "flux de mise à jour"),
])
def test_check_names_what_is_not_neutral(tmp_path, change, word):
    problems = neutral_package.check(_package(tmp_path, **change))
    assert len(problems) == 1 and word in problems[0]


def test_placeholders_are_named_after_the_key():
    assert neutral_package.placeholder("portal_url") == "${{PORTAL_URL}}"
    assert neutral_package.placeholder("keycloakIssuerUrl") == "${{KEYCLOAK_ISSUER_URL}}"
