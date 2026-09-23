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


def _cleanup_phantom_dirs():
    # Dossiers nommés d'après un repr de MagicMock (chemin de config mocké).
    for path in glob.glob(os.path.join(_REPO_ROOT, "*MagicMock*")):
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_config_cache():
    # `config_cache.json` (cache disque du config_data enrichi) est écrit dans
    # le UserConfig PARTAGÉ des tests (/tmp/test_libreoffice_config). Sans purge,
    # il fuit d'un test à l'autre (p. ex. un `update` directive persisté puis
    # relu ailleurs). On le retire avant ET après chaque test.
    for base in ("/tmp/test_libreoffice_config",):
        try:
            os.remove(os.path.join(base, "config_cache.json"))
        except OSError:
            pass


_LOCALE_ENV_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")


def _pin_locale(monkeypatch):
    # Les libellés de l'IHM sont résolus à la construction du widget, et
    # `MainJob.__init__` re-résout la langue à chaque instanciation. Sans
    # verrou, le rendu suivrait la locale du poste (LC_ALL, LANG, …) et les
    # assertions françaises échoueraient sur un environnement pt_BR ou en_US.
    # On neutralise donc l'environnement pour retomber sur le français, puis on
    # force la langue du module (les tests qui veulent une autre langue la
    # posent eux-mêmes après ce fixture).
    for name in _LOCALE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    try:
        from src.mirai import i18n
    except Exception:
        return
    i18n.set_locale(i18n.DEFAULT_LOCALE)


@pytest.fixture(autouse=True)
def _isolate_mainjob_state(monkeypatch):
    _reset_mainjob_flags()
    _pin_locale(monkeypatch)
    _cleanup_config_cache()
    yield
    _pin_locale(monkeypatch)
    _reset_mainjob_flags()
    _cleanup_phantom_dirs()
    _cleanup_config_cache()
