"""Fixtures partagées pour les tests unitaires.

`MainJob` porte des drapeaux de CLASSE (`*_cls`) volontairement partagés entre
instances. Sans réinitialisation, l'état fuit d'un test à l'autre et provoque
des échecs dépendants de l'ordre.

Ce conftest réinitialise ces drapeaux avant ET après chaque test, et nettoie
en filet de sécurité d'éventuels dossiers-fantômes laissés par des chemins de
config mockés.
"""
import glob
import os
import shutil

import pytest

# Racine du repo (deux niveaux au-dessus de tests/unit/).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _reset_mainjob_flags():
    try:
        from tests.stubs.uno_stubs import install
        install()
        from src.mirai import log_setup
        from src.mirai.entrypoint import MainJob
    except Exception:
        return
    log_setup.uninstall()
    MainJob._update_in_progress_cls = False
    MainJob._storage_ready_cls = False
    MainJob._enrollment_dismissed_cls = False
    MainJob._update_launch_blocked_cls = set()
    MainJob._feed_rewrite_last_cls = None
    MainJob._feed_rewrite_last_result_cls = None
    MainJob._uninstall_listener_cls = None
    MainJob._self_update_in_flight_cls = False
    MainJob._wiped_cls = False
    from src.mirai import credentials, local_config
    local_config.unfreeze_for_tests()
    credentials.unfreeze_for_tests()
    log_setup.unfreeze_for_tests()


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


@pytest.fixture(autouse=True)
def _isolate_mainjob_state():
    _reset_mainjob_flags()
    _reset_credentials()
    _pin_locale()
    _cleanup_shared_config_dir()
    yield
    _pin_locale()
    _reset_mainjob_flags()
    _reset_credentials()
    _cleanup_phantom_dirs()
    _cleanup_shared_config_dir()
