"""Isolation du dossier personnel pour les tests d'intégration."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_legacy_home(monkeypatch, tmp_path_factory):
    # La migration supprime ~/log.txt s'il vient de l'extension : jamais le
    # vrai dossier personnel du développeur pendant les tests.
    from src.mirai import local_config
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(local_config, "legacy_home_dir", lambda: str(home))
