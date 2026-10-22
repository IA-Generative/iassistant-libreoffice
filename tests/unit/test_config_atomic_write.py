"""`set_config` écrit de façon ATOMIQUE.

Écrire la configuration en place expose tout lecteur concurrent à un JSON
tronqué. Le lecteur repart alors sur les valeurs par défaut et perd les
credentials — c'est une façon d'entrer dans l'« état absorbant » (marqué
enrôlé, sans paire relais) sans que personne ne l'ait demandé.
"""

import contextlib
import json
import os
import threading

from tests.stubs.uno_stubs import install, make_job, read_user_config

install()

from src.mirai import local_config


def _job(tmp_path):
    config_dir = str(tmp_path)
    return make_job(config_dir=config_dir), config_dir


def _leftovers(config_dir):
    return [n for n in os.listdir(os.path.join(config_dir, "mirai")) if n.startswith(".tmp-")]


def _settings_locked(monkeypatch, refusals):
    """Remplacement de settings.json refusé `refusals` fois, comme sous Windows
    quand un antivirus tient le fichier ouvert."""
    real = os.replace
    attempts = []

    def replace(source, target):
        if os.path.basename(target) == local_config.SETTINGS_FILE:
            attempts.append(target)
            if len(attempts) <= refusals:
                raise PermissionError(13, "[WinError 5] Accès refusé")
        return real(source, target)

    monkeypatch.setattr(os, "replace", replace)


def _windows_sharing(monkeypatch):
    """Sémantique Windows : un fichier ouvert en lecture ne peut pas être
    remplacé. Rend la liste des remplacements refusés."""
    real_open, real_replace = open, os.replace
    reading = []
    refused = []

    @contextlib.contextmanager
    def tracked_open(path, *args, **kwargs):
        with real_open(path, *args, **kwargs) as handle:
            reading.append(path)
            try:
                yield handle
            finally:
                reading.remove(path)

    def replace(source, target):
        if target in reading:
            refused.append(target)
            raise PermissionError(13, "[WinError 5] Accès refusé")
        return real_replace(source, target)

    monkeypatch.setattr(local_config, "open", tracked_open, raising=False)
    monkeypatch.setattr(os, "replace", replace)
    return refused


def _settings_held_without_sharing(monkeypatch, refusals):
    """Un programme extérieur tient settings.json sans aucun partage : lecture
    et remplacement refusés `refusals` fois, puis il relâche le fichier."""
    real_open, real_replace = open, os.replace
    attempts = []

    def refused(target):
        if os.path.basename(str(target)) != local_config.SETTINGS_FILE:
            return False
        attempts.append(target)
        return len(attempts) <= refusals

    def tracked_open(path, *args, **kwargs):
        if refused(path):
            raise PermissionError(13, "[WinError 5] Accès refusé")
        return real_open(path, *args, **kwargs)

    def replace(source, target):
        if refused(target):
            raise PermissionError(13, "[WinError 5] Accès refusé")
        return real_replace(source, target)

    monkeypatch.setattr(local_config, "open", tracked_open, raising=False)
    monkeypatch.setattr(os, "replace", replace)


def _settings_on_disk(config_dir):
    with open(os.path.join(config_dir, "mirai", "settings.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_value_is_persisted(tmp_path):
    job, config_dir = _job(tmp_path)
    job.set_config("device_name", "poste-a")

    with open(os.path.join(config_dir, "mirai", "settings.json"), encoding="utf-8") as fh:
        assert json.load(fh)["device_name"] == "poste-a"


def test_no_temporary_file_is_left_behind(tmp_path):
    job, config_dir = _job(tmp_path)
    job.set_config("a", "1")

    leftovers = [n for n in os.listdir(os.path.join(config_dir, "mirai"))
                 if n.startswith(".tmp-")]
    assert leftovers == [], f"fichiers temporaires oubliés : {leftovers}"


def test_concurrent_readers_never_see_a_truncated_file(tmp_path):
    """Le cœur du sujet : jamais de JSON partiel, même sous écritures répétées."""
    job, config_dir = _job(tmp_path)
    path = os.path.join(config_dir, "mirai", "settings.json")
    job.set_config("device_name", "poste-initial")

    corrupted = []
    stop = threading.Event()

    def _reader():
        while not stop.is_set():
            try:
                with open(path, encoding="utf-8") as fh:
                    json.load(fh)
            except FileNotFoundError:
                continue          # fenêtre de remplacement : acceptable
            except json.JSONDecodeError as exc:
                corrupted.append(str(exc))
                return

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    for index in range(200):
        job.set_config("payload", "x" * 500 + str(index))
    stop.set()
    reader.join(timeout=5)

    assert corrupted == [], (
        f"un lecteur a vu un JSON tronqué : {corrupted[:1]}")


def test_existing_keys_survive_a_write(tmp_path):
    job, config_dir = _job(tmp_path)
    job.set_config("device_name", "poste-a")
    job.set_config("llm_tool_mode", "native")

    with open(os.path.join(config_dir, "mirai", "settings.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["device_name"] == "poste-a"
    assert data["llm_tool_mode"] == "native"


def test_setting_survives_a_briefly_locked_settings_file(tmp_path, monkeypatch, fast_clock):
    job, config_dir = _job(tmp_path)
    _settings_locked(monkeypatch, refusals=2)
    job.set_config("plugin_uuid", "u-1")
    assert read_user_config(config_dir)["plugin_uuid"] == "u-1"
    assert _leftovers(config_dir) == []


def test_lasting_lock_keeps_the_previous_settings_and_no_leftover(tmp_path, monkeypatch,
                                                                  fast_clock):
    job, config_dir = _job(tmp_path)
    job.set_config("device_name", "poste-a")
    _settings_locked(monkeypatch, refusals=float("inf"))
    job.set_config("device_name", "poste-b")
    assert read_user_config(config_dir)["device_name"] == "poste-a"
    assert _leftovers(config_dir) == []


def test_reads_in_the_process_never_hold_up_a_settings_write(tmp_path, monkeypatch, fast_clock):
    job, config_dir = _job(tmp_path)
    job.set_config("payload", "initial")
    refused = _windows_sharing(monkeypatch)
    reader = local_config.LocalConfig(config_dir, [])
    stop = threading.Event()

    def _read():
        while not stop.is_set():
            reader.get("payload")

    thread = threading.Thread(target=_read, daemon=True)
    thread.start()
    for index in range(200):
        job.set_config("payload", str(index))
    stop.set()
    thread.join(timeout=5)

    assert refused == []
    assert read_user_config(config_dir)["payload"] == "199"


def test_setting_survives_a_settings_file_briefly_held_without_sharing(tmp_path, monkeypatch,
                                                                       fast_clock):
    job, config_dir = _job(tmp_path)
    job.set_config("plugin_uuid", "u-1")
    _settings_held_without_sharing(monkeypatch, refusals=2)
    job.set_config("assistant_active_tab", "actions")
    data = _settings_on_disk(config_dir)
    assert data["plugin_uuid"] == "u-1"
    assert data["assistant_active_tab"] == "actions"
    assert _leftovers(config_dir) == []


def test_lasting_hold_without_sharing_keeps_the_previous_settings(tmp_path, monkeypatch,
                                                                  fast_clock):
    job, config_dir = _job(tmp_path)
    job.set_config("plugin_uuid", "u-1")
    _settings_held_without_sharing(monkeypatch, refusals=float("inf"))
    job.set_config("assistant_active_tab", "actions")
    data = _settings_on_disk(config_dir)
    assert data["plugin_uuid"] == "u-1"
    assert "assistant_active_tab" not in data
    assert _leftovers(config_dir) == []
