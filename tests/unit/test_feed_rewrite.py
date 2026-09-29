"""Réécriture de l'adresse du feed natif (device-management#40).

Le DM ne sert sur l'adresse nue du feed que la « version générale ». Le plugin
écrit `?version=<cible>` dans le description.xml de son installation, que
LibreOffice relit à chaque vérification : chaque poste ne voit que sa cible.

Run:  pytest tests/unit/test_feed_rewrite.py -v
"""
import os
import shutil
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai import entrypoint, feed_rewrite  # noqa: E402
from src.mirai.entrypoint import MainJob  # noqa: E402
from tests.unit.test_update_features import (  # noqa: E402
    _enriched_v2,
    _json_response,
    _make_update_directive,
)

SRC_TAG = "{http://openoffice.org/extensions/description/2006}src"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
BASE_1 = "https://dm-1.example/catalog/mirai-libreoffice/update.xml"
BASE_2 = "https://dm-2.example/bootstrap/catalog/mirai-libreoffice/update.xml"

DESCRIPTION = f"""<?xml version="1.0" encoding="UTF-8"?>
<description xmlns="http://openoffice.org/extensions/description/2006"
             xmlns:d="http://openoffice.org/extensions/description/2006"
             xmlns:xlink="http://www.w3.org/1999/xlink">
  <identifier value="fr.gouv.interieur.mirai"/>
  <version value="0.0.1.0.31"/>
  <update-information>
    <src xlink:href="{BASE_1}"/>
    <src xlink:href="{BASE_2}"/>
  </update-information>
</description>
"""

_TMPDIRS = []


@pytest.fixture(autouse=True, scope="module")
def _purge_tmpdirs():
    yield
    for path in _TMPDIRS:
        shutil.rmtree(path, ignore_errors=True)


def _hrefs(text):
    return [src.get(XLINK_HREF) for src in ET.fromstring(text.encode()).iter(SRC_TAG)]


# ── Module pur ───────────────────────────────────────────────────────────

def test_every_src_gets_the_version_in_order():
    out = feed_rewrite.rewrite_feed_version(DESCRIPTION, "0.0.1.0.32")
    assert _hrefs(out) == [BASE_1 + "?version=0.0.1.0.32", BASE_2 + "?version=0.0.1.0.32"]
    ET.fromstring(out.encode())


def test_the_rest_of_the_file_is_byte_identical():
    """Le build relit <version value=…> par motif : rien d'autre ne bouge."""
    out = feed_rewrite.rewrite_feed_version(DESCRIPTION, "0.0.1.0.32")
    before, after = DESCRIPTION.split("<update-information>"), out.split("<update-information>")
    assert before[0] == after[0]
    assert DESCRIPTION.split("</update-information>")[1] == out.split("</update-information>")[1]


def test_rewriting_twice_replaces_the_version_instead_of_adding_one():
    once = feed_rewrite.rewrite_feed_version(DESCRIPTION, "1.0")
    twice = feed_rewrite.rewrite_feed_version(once, "2.0")
    assert _hrefs(twice) == [BASE_1 + "?version=2.0", BASE_2 + "?version=2.0"]
    assert feed_rewrite.rewrite_feed_version(twice, "2.0") == twice


def test_other_query_parameters_and_override_urls_survive():
    text = DESCRIPTION.replace(BASE_1, "https://custom.example/feed.xml?channel=pilot&amp;x=1")
    out = feed_rewrite.rewrite_feed_version(text, "1.0")
    assert _hrefs(out)[0] == "https://custom.example/feed.xml?channel=pilot&x=1&version=1.0"
    ET.fromstring(out.encode())


def test_version_is_url_encoded():
    out = feed_rewrite.rewrite_feed_version(DESCRIPTION, "1.0+build 7")
    assert _hrefs(out)[0] == BASE_1 + "?version=1.0%2Bbuild+7"


def test_no_update_information_block_means_absent():
    """Profil offline : le build n'injecte aucun bloc, on n'en crée jamais."""
    text = DESCRIPTION.split("<update-information>")[0] + "</description>\n"
    assert feed_rewrite.rewrite_feed_version(text, "1.0") is None


def _write(text):
    folder = tempfile.mkdtemp()
    _TMPDIRS.append(folder)
    path = os.path.join(folder, "description.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return folder, path


def test_file_is_written_atomically_and_idempotently():
    folder, path = _write(DESCRIPTION)
    assert feed_rewrite.rewrite_description_file(path, "1.0")[0] == feed_rewrite.WRITTEN
    mtime = os.stat(path).st_mtime_ns
    assert feed_rewrite.rewrite_description_file(path, "1.0")[0] == feed_rewrite.UNCHANGED
    assert os.stat(path).st_mtime_ns == mtime
    assert os.listdir(folder) == ["description.xml"], "aucun fichier temporaire ne doit rester"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="droits POSIX non applicables")
def test_unwritable_installation_is_reported_not_raised():
    """Installation en couche partagée : repli sur la route dirigée."""
    folder, path = _write(DESCRIPTION)
    os.chmod(path, stat.S_IRUSR)
    os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert feed_rewrite.rewrite_description_file(path, "1.0")[0] == feed_rewrite.UNWRITABLE
    finally:
        os.chmod(folder, stat.S_IRWXU)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == DESCRIPTION


def test_broken_xml_is_an_error_and_the_file_is_untouched():
    broken = DESCRIPTION.replace("</description>", "")
    _folder, path = _write(broken)
    assert feed_rewrite.rewrite_description_file(path, "1.0")[0] == feed_rewrite.ERROR
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == broken


def test_missing_file_is_an_error():
    assert feed_rewrite.rewrite_description_file("/nonexistent/description.xml", "1")[0] == \
        feed_rewrite.ERROR


# ── Branchement dans le plugin ───────────────────────────────────────────

@pytest.fixture
def installed(monkeypatch):
    """Faux paquet installé : <pkg>/description.xml et <pkg>/python/src/mirai/."""
    root = tempfile.mkdtemp()
    _TMPDIRS.append(root)
    pkg = os.path.join(root, "lu42.tmp_", "mirai-libreoffice.oxt")
    os.makedirs(os.path.join(pkg, "python", "src", "mirai"))
    path = os.path.join(pkg, "description.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(DESCRIPTION)
    monkeypatch.setattr(entrypoint, "__file__",
                        os.path.join(pkg, "python", "src", "mirai", "entrypoint.py"))
    return path


def _job():
    job = make_job(config_dir=tempfile.mkdtemp())
    job._get_extension_version = MagicMock(return_value="0.0.1.0.31")
    job._send_telemetry = MagicMock()
    return job


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_update_directive_points_the_feed_at_its_target(installed):
    job = _job()
    job._rewrite_feed_for_directive({"action": "update", "target_version": "0.0.1.0.32"})
    assert _hrefs(_read(installed))[0] == BASE_1 + "?version=0.0.1.0.32"
    attrs = job._send_telemetry.call_args.args[1]
    assert job._send_telemetry.call_args.args[0] == "FeedRewrite"
    assert attrs["feed.result"] == "written" and attrs["feed.target"] == "0.0.1.0.32"


@pytest.mark.parametrize("directive", [None, {}, {"action": "rollback", "target_version": "0.0.1.0.20"},
                                       {"action": "update", "target_version": ""}])
def test_without_update_target_the_feed_points_at_the_installed_version(installed, directive):
    """Sans cible — et pour un rollback, que LibreOffice ne proposerait jamais —
    l'adresse vise la version installée : aucune offre native."""
    job = _job()
    job._rewrite_feed_for_directive(directive)
    assert _hrefs(_read(installed))[0] == BASE_1 + "?version=0.0.1.0.31"


def test_telemetry_is_sent_once_per_change_not_per_config_fetch(installed):
    job = _job()
    for _ in range(3):
        job._rewrite_feed_for_directive(None)
    assert job._send_telemetry.call_count == 1


def test_a_persistent_result_is_logged_once(installed, monkeypatch):
    with open(installed, "w", encoding="utf-8") as fh:
        fh.write(DESCRIPTION.split("<update-information>")[0] + "</description>\n")
    logged = MagicMock()
    monkeypatch.setattr(entrypoint, "log_to_file", logged)
    job = _job()
    for _ in range(3):
        job._rewrite_feed_for_directive(None)
    assert sum("_rewrite_feed_url: absent" in c.args[0] for c in logged.call_args_list) == 1


@pytest.mark.parametrize("target", ["0.0.1.0.31", "0.0.1.0.32"])
def test_fetch_config_rewrites_before_deciding_anything(installed, target):
    """La réécriture a lieu même quand le poste est déjà à la cible (la branche
    « déjà à la version cible » ne planifie rien), et avant de planifier la mise
    à jour : _native_feed_offers relit description.xml."""
    job = _job()
    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: {
        "bootstrap_url": "http://localhost:9999", "config_path": "/config/lo/config.json",
        "enabled": True, "proxy_enabled": False,
    }.get(k, d))
    job._relay_headers = MagicMock(return_value={})
    job._get_lo_version = MagicMock(return_value="24.8.0")
    job._ensure_plugin_uuid = MagicMock(return_value="test-uuid")
    job._persist_bootstrap_config = MagicMock()
    seen = []
    job._schedule_update = MagicMock(side_effect=lambda d: seen.append(_hrefs(_read(installed))[0]))
    directive = _make_update_directive()
    directive["target_version"] = target
    job._urlopen = MagicMock(return_value=_json_response(_enriched_v2(features={}, update=directive)))
    job._fetch_config(force=True)
    assert _hrefs(_read(installed))[0] == BASE_1 + f"?version={target}"
    assert seen == ([] if target == "0.0.1.0.31" else [BASE_1 + "?version=0.0.1.0.32"])


def test_no_package_root_is_harmless(monkeypatch):
    root = tempfile.mkdtemp()
    _TMPDIRS.append(root)
    monkeypatch.setattr(entrypoint, "__file__", os.path.join(root, "a", "b", "entrypoint.py"))
    job = _job()
    assert job._rewrite_feed_url("1.0") == feed_rewrite.ERROR
    job._send_telemetry.assert_not_called()


def test_diagnostic_feed_urls_carry_the_same_version(installed):
    job = _job()
    job._failover_ordered_urls = MagicMock(return_value=["https://dm-1.example/"])
    job._rewrite_feed_for_directive({"action": "update", "target_version": "0.0.1.0.32"})
    assert job._update_feed_urls() == [BASE_1 + "?version=0.0.1.0.32"]


def test_feed_rewrite_is_registered_as_technical_telemetry():
    assert MainJob._ACTION_NAMES["FeedRewrite"] == "update"
    assert "FeedRewrite" in MainJob._TECHNICAL_EVENTS


# ── Corrections de la revue qualité ──────────────────────────────────────

def test_file_mode_and_crlf_are_preserved():
    folder, path = _write(DESCRIPTION)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(DESCRIPTION.replace("\n", "\r\n"))
    os.chmod(path, 0o644)
    assert feed_rewrite.rewrite_description_file(path, "1.0")[0] == feed_rewrite.WRITTEN
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    with open(path, "rb") as fh:
        raw = fh.read()
    assert b"\r\n" in raw and b"\n\n" not in raw.replace(b"\r\n", b"")


def test_startup_rewrite_does_not_overwrite_a_directive_target(installed):
    """Le fetch de /config peut aboutir avant le Timer de démarrage (2 s) :
    la cible posée par la directive ne doit pas être écrasée."""
    job = _job()
    MainJob._feed_rewrite_started_cls = False
    job._rewrite_feed_for_directive({"action": "update", "target_version": "0.0.1.0.32"})
    fired = []

    class _Timer:
        def __init__(self, delay, fn):
            fired.append(fn)
            self.daemon = True

        def start(self):
            pass

    import threading as _threading
    original = _threading.Timer
    _threading.Timer = _Timer
    try:
        job._schedule_feed_rewrite()
    finally:
        _threading.Timer = original
        MainJob._feed_rewrite_started_cls = False
    fired[0]()
    assert _hrefs(_read(installed))[0] == BASE_1 + "?version=0.0.1.0.32"


def test_startup_rewrite_does_not_overwrite_a_directive_written_during_its_version_lookup(
        installed, monkeypatch):
    job = _job()
    fired = []
    monkeypatch.setattr(entrypoint.threading, "Timer", lambda delay, fn: fired.append(fn) or MagicMock())
    monkeypatch.setattr(MainJob, "_feed_rewrite_started_cls", False)
    job._schedule_feed_rewrite()

    def _version_lookup_while_config_lands():
        _job()._rewrite_feed_for_directive({"action": "update", "target_version": "0.0.1.0.32"})
        return "0.0.1.0.31"
    job._get_extension_version = _version_lookup_while_config_lands
    fired[0]()
    assert _hrefs(_read(installed))[0] == BASE_1 + "?version=0.0.1.0.32"


def test_diagnostic_uses_the_bare_address_until_a_rewrite_succeeded():
    job = _job()
    job._failover_ordered_urls = MagicMock(return_value=["https://dm-1.example/"])
    MainJob._feed_rewrite_last_cls = None
    assert job._update_feed_urls() == [BASE_1]
