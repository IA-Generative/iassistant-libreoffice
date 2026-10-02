"""Journal de l'extension : <UserConfig>/mirai/mirai.log, rotation bornée.

Le profil LibreOffice est itinérant sur les postes cibles : le journal part sur
le serveur de fichiers à chaque fermeture de session, d'où la taille bornée.
"""

import logging
import logging.handlers
import os
import threading

LOG_FILE = "mirai.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 1

_handler = None
_lock = threading.Lock()
_frozen = False


def freeze():
    global _frozen
    _frozen = True


def unfreeze_for_tests():
    global _frozen
    _frozen = False


def install(data_dir):
    """Branche le journal sur `data_dir` (une seule fois par processus)."""
    global _handler
    if not data_dir or _frozen:
        return
    with _lock:
        if _handler is not None:
            return
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(data_dir, LOG_FILE), maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT, encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        _handler = handler


def path():
    with _lock:
        return _handler.baseFilename if _handler is not None else ""


def uninstall():
    global _handler
    with _lock:
        if _handler is None:
            return
        logging.getLogger().removeHandler(_handler)
        _handler.close()
        _handler = None
