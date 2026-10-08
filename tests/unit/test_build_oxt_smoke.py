"""Smoke test du BUILD .oxt (plugin LibreOffice).

Exécute réellement ``scripts/02-build-oxt.sh`` avec le profil suivi par git
``config/profiles/config.default.dev.json`` (même entrée sur tous les postes) vers
une sortie temporaire, puis valide que l'archive produite est un paquet LibreOffice
cohérent et installable :

  - c'est un ZIP valide ;
  - tous les membres requis sont présents (manifeste UNO, sources, config, DM) ;
  - aucun artefact de build indésirable n'a fuité (.pyc, __pycache__, .DS_Store, .git) ;
  - la ``config.default.json`` embarquée reste *transport-only* (anti-fuite SSO/LLM).

On valide la SORTIE du build (le paquet), pas la logique métier. Le test se
``skip`` proprement si ``zip`` est indisponible (build impossible).
"""
import json
import os
import shutil
import subprocess
import zipfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_SCRIPT = os.path.join(ROOT, "scripts", "02-build-oxt.sh")

# Membres attendus à la racine de l'archive .oxt
REQUIRED_MEMBERS = [
    "META-INF/manifest.xml",      # manifeste UNO (requis par LibreOffice)
    "description.xml",            # métadonnées extension (id + version)
    "main.py",                    # point d'entrée
    "src/mirai/entrypoint.py",    # logique principale
    "src/mirai/security_flow.py", # coffre + flux sécurisé
    "config.default.json",        # config embarquée (transport-only)
    "dm-manifest.json",           # métadonnées Device Management
    "dm-config.json",             # template config DM
    "assets/logo.png",            # logo catalogue DM
]

FORBIDDEN_SUFFIXES = (".pyc", ".DS_Store")
# Clés autorisées dans la config embarquée d'un profil ONLINE (cf. 02-build-oxt.sh).
TRANSPORT_ONLY_KEYS = {
    "configVersion", "enabled", "bootstrap_urls", "bootstrap_url",
    "bootstrap_insecure_urls", "config_path", "_note", "_description",
}


@pytest.fixture(scope="module")
def oxt_path(tmp_path_factory):
    if shutil.which("zip") is None:
        pytest.skip("binaire 'zip' indisponible — build .oxt impossible")
    if not os.path.isfile(BUILD_SCRIPT):
        pytest.skip(f"script de build introuvable: {BUILD_SCRIPT}")

    out = tmp_path_factory.mktemp("oxt") / "mirai-smoke.oxt"
    res = subprocess.run(
        ["bash", BUILD_SCRIPT,
         "--config", os.path.join(ROOT, "config", "profiles", "config.default.dev.json"),
         "--output", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    if res.returncode != 0:
        pytest.fail(
            f"02-build-oxt.sh a échoué (code {res.returncode})\n"
            f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        )
    assert out.exists(), "l'archive .oxt n'a pas été produite"
    return str(out)


def _names(oxt):
    with zipfile.ZipFile(oxt) as z:
        return z.namelist()


def test_oxt_is_a_valid_zip(oxt_path):
    with zipfile.ZipFile(oxt_path) as z:
        assert z.testzip() is None, "archive .oxt corrompue"


@pytest.mark.parametrize("member", REQUIRED_MEMBERS)
def test_required_member_present(oxt_path, member):
    assert member in _names(oxt_path), f"membre requis absent de l'archive: {member}"


def test_no_leaked_build_artifacts(oxt_path):
    bad = [
        n for n in _names(oxt_path)
        if n.endswith(FORBIDDEN_SUFFIXES)
        or "__pycache__" in n
        or n.startswith(".git")
    ]
    assert not bad, f"artefacts de build indésirables dans l'archive: {bad}"


def test_embedded_config_is_transport_only(oxt_path):
    with zipfile.ZipFile(oxt_path) as z:
        cfg = json.loads(z.read("config.default.json"))
    if cfg.get("enabled") is False:
        return  # profil offline: baker une config locale est légitime
    leaked = sorted(k for k in cfg if k not in TRANSPORT_ONLY_KEYS)
    assert not leaked, (
        "config.default.json embarquée fuite des clés non-transport "
        f"(SSO/keycloak/LLM ?): {leaked}"
    )


def test_description_has_native_update_feed(oxt_path):
    """MAJ native LibreOffice : un profil online doit embarquer le
    bloc <update-information> pointant sur le feed du DM (un <src> par
    bootstrap_url — failover natif)."""
    with zipfile.ZipFile(oxt_path) as z:
        desc = z.read("description.xml").decode("utf-8", "replace")
        cfg = json.loads(z.read("config.default.json"))
    if cfg.get("enabled") is False:
        assert "<update-information>" not in desc
        return
    bases = cfg.get("bootstrap_urls") or ([cfg["bootstrap_url"]] if cfg.get("bootstrap_url") else [])
    if not bases:
        pytest.skip("profil sans bootstrap_urls — pas de feed natif attendu")
    assert "<update-information>" in desc
    assert "/catalog/mirai-libreoffice/update.xml" in desc
    assert desc.count("<src xlink:href=") >= len(bases)


def test_description_version_matches_dm_manifest(oxt_path):
    """La version injectée dans dm-manifest.json doit suivre description.xml."""
    import re
    with zipfile.ZipFile(oxt_path) as z:
        desc = z.read("description.xml").decode("utf-8", "replace")
        dm = json.loads(z.read("dm-manifest.json"))
    m = re.search(r'<version value="([^"]+)"', desc)
    assert m, "version absente de description.xml"
    assert dm.get("version") == m.group(1), (
        f"dm-manifest.json version={dm.get('version')} != description.xml {m.group(1)}"
    )


def test_license_shows_the_description_version(oxt_path):
    """La licence affichée à l'installation porte la version de description.xml."""
    import re
    with zipfile.ZipFile(oxt_path) as z:
        desc = z.read("description.xml").decode("utf-8", "replace")
        licenses = [z.read(name).decode("utf-8") for name in
                    ("registration/license.txt", "docs/license.txt")]
    version = re.search(r'<version value="([^"]+)"', desc).group(1)
    for text in licenses:
        assert f"version {version}" in text
        assert "@VERSION@" not in text
