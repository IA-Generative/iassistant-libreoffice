"""Isolation du coffre pour les tests d'intégration."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_credentials():
    # Jamais le vrai trousseau macOS ni le Gestionnaire d'identification Windows.
    from src.mirai import credentials
    credentials.use_store(credentials.MemoryStore())
    credentials.forget_all()
    from src.mirai import local_config, log_setup
    local_config.unfreeze_for_tests()
    credentials.unfreeze_for_tests()
    log_setup.unfreeze_for_tests()
