"""Fixtures partagées pour les tests unitaires.

L'état du processus (`shell/runtime.py`) est partagé par toutes les instances
de `MainJob`. Sans réinitialisation, il fuit d'un test à l'autre et provoque
des échecs dépendants de l'ordre.

Ce conftest le réinitialise avant ET après chaque test, et nettoie en filet de
sécurité d'éventuels dossiers-fantômes laissés par des chemins de config
mockés.
"""
import glob
import os
import shutil
import time

import pytest

from tests.stubs.uno_stubs import install

# Avant toute collecte : un test lancé seul importe la coquille ou la palette
# sans qu'un autre module ait posé les stubs UNO.
install()

# Racine du repo (deux niveaux au-dessus de tests/unit/).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _reset_process_state():
    from src.mirai.shell import runtime
    runtime.reset_for_tests()


def _cleanup_phantom_dirs():
    # Dossiers nommés d'après un repr de MagicMock (chemin de config mocké).
    for path in glob.glob(os.path.join(_REPO_ROOT, "*MagicMock*")):
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_shared_config_dir():
    # make_job() sans config_dir partage /tmp/test_libreoffice_config : ses
    # fichiers (réglages, instantané DM, ancien cache) fuiraient d'un test à
    # l'autre. On les retire avant ET après chaque test.
    base = "/tmp/test_libreoffice_config"
    shutil.rmtree(os.path.join(base, "mirai"), ignore_errors=True)
    for name in ("config_cache.json", "config.json"):
        try:
            os.remove(os.path.join(base, name))
        except OSError:
            pass


def _reset_credentials():
    try:
        from src.mirai import credentials
    except Exception:
        return
    credentials.use_store(credentials.MemoryStore())
    credentials.forget_all()


def _pin_locale():
    # Chaque test part du français, la langue du LibreOffice que simule
    # `make_job` par défaut. Un test qui en veut une autre la pose lui-même.
    try:
        from src.mirai import i18n
    except Exception:
        return
    i18n.set_locale(i18n.DEFAULT_LOCALE)


class _FastClock:
    """Module `time` vu par la coquille : `sleep` rend la main tout de suite et
    avance d'autant `time()`, pour que les boucles à échéance se terminent."""

    def __init__(self):
        self._elapsed = 0.0

    def sleep(self, seconds):
        self._elapsed += seconds
        time.sleep(0)

    def time(self):
        return time.time() + self._elapsed

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def fast_clock(monkeypatch):
    """Les attentes de la coquille et des fichiers locaux ne coûtent plus de
    temps réel."""
    from src.mirai import entrypoint, local_config
    clock = _FastClock()
    monkeypatch.setattr(entrypoint, "time", clock)
    monkeypatch.setattr(local_config, "time", clock)
    return clock


@pytest.fixture
def thread_guard():
    """Enveloppe des faux objets UNO dans un ThreadGuard partagé ; le test
    échoue si l'un d'eux a été touché hors du thread propriétaire."""
    from tests.stubs.thread_guard import ThreadGuard
    violations = []

    def guard(target, owner, path="doc"):
        return ThreadGuard(target, owner, violations, path)

    yield guard
    assert violations == [], f"UNO touché hors du thread principal : {violations}"


@pytest.fixture(autouse=True)
def _isolate_mainjob_state():
    _reset_process_state()
    _reset_credentials()
    _pin_locale()
    _cleanup_shared_config_dir()
    yield
    _pin_locale()
    _reset_process_state()
    _reset_credentials()
    _cleanup_phantom_dirs()
    _cleanup_shared_config_dir()
