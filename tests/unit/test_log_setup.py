"""Le journal vit dans le dossier de l'extension, avec rotation."""

import logging
import logging.handlers

from src.mirai import log_setup


def teardown_function():
    log_setup.uninstall()


def test_install_writes_into_the_data_dir(tmp_path):
    log_setup.install(str(tmp_path / "mirai"))
    logging.getLogger().info("ligne de test")
    assert log_setup.path() == str(tmp_path / "mirai" / "mirai.log")
    assert "ligne de test" in (tmp_path / "mirai" / "mirai.log").read_text(encoding="utf-8")


def test_install_is_idempotent_and_rotates(tmp_path):
    log_setup.install(str(tmp_path / "a"))
    log_setup.install(str(tmp_path / "b"))
    handlers = [h for h in logging.getLogger().handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == log_setup.MAX_BYTES
    assert handlers[0].backupCount == log_setup.BACKUP_COUNT


def test_uninstall_detaches_the_handler(tmp_path):
    log_setup.install(str(tmp_path / "mirai"))
    log_setup.uninstall()
    assert log_setup.path() == ""
