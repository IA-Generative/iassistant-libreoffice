"""Fixtures partagées pour les tests unitaires.

`MainJob` porte des drapeaux de CLASSE volontairement partagés entre instances
(`_update_in_progress_cls`, `_enrollment_dismissed_cls` — cf. « share
enrollment/update flags across instances »). Sans réinitialisation, l'état
fuit d'un test à l'autre et provoque des échecs dépendants de l'ordre.

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
        from src.mirai.entrypoint import MainJob
    except Exception:
        return
    MainJob._update_in_progress_cls = False
    MainJob._enrollment_dismissed_cls = False
    MainJob._update_launch_blocked_cls = set()
    MainJob._feed_rewrite_last_cls = None
    MainJob._feed_rewrite_last_result_cls = None


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
    credentials.forget_all()


@pytest.fixture(autouse=True)
def _isolate_mainjob_state():
    _reset_mainjob_flags()
    _reset_credentials()
    _cleanup_shared_config_dir()
    yield
    _reset_mainjob_flags()
    _reset_credentials()
    _cleanup_phantom_dirs()
    _cleanup_shared_config_dir()
