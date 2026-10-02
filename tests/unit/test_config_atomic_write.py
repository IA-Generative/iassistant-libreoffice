"""`set_config` écrit de façon ATOMIQUE.

Écrire la configuration en place expose tout lecteur concurrent à un JSON
tronqué. Le lecteur repart alors sur les valeurs par défaut et perd les
credentials — c'est une façon d'entrer dans l'« état absorbant » (marqué
enrôlé, sans paire relais) sans que personne ne l'ait demandé. Le symptôme
observé en test était plus discret : un échec qui se déplaçait d'un test à
l'autre selon la charge de la machine.
"""

import json
import os
import tempfile
import threading

from tests.stubs.uno_stubs import install, make_job

install()


def _job():
    config_dir = tempfile.mkdtemp()
    return make_job(config_dir=config_dir), config_dir


def test_value_is_persisted():
    job, config_dir = _job()
    job.set_config("relay_client_id", "rc_abc")

    with open(os.path.join(config_dir, "mirai", "settings.json"), encoding="utf-8") as fh:
        assert json.load(fh)["relay_client_id"] == "rc_abc"


def test_no_temporary_file_is_left_behind():
    job, config_dir = _job()
    job.set_config("a", "1")

    leftovers = [n for n in os.listdir(os.path.join(config_dir, "mirai"))
                 if n.startswith(".tmp-")]
    assert leftovers == [], f"fichiers temporaires oubliés : {leftovers}"


def test_concurrent_readers_never_see_a_truncated_file():
    """Le cœur du sujet : jamais de JSON partiel, même sous écritures répétées."""
    job, config_dir = _job()
    path = os.path.join(config_dir, "mirai", "settings.json")
    job.set_config("relay_client_id", "rc_initial")

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


def test_existing_keys_survive_a_write():
    job, config_dir = _job()
    job.set_config("relay_client_id", "rc_abc")
    job.set_config("relay_client_key", "key_xyz")

    with open(os.path.join(config_dir, "mirai", "settings.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["relay_client_id"] == "rc_abc"
    assert data["relay_client_key"] == "key_xyz"
