"""
Inject fake UNO modules so that src.mirai.entrypoint can be imported
and tested outside of LibreOffice.

Usage — call install() before importing anything from src.mirai.entrypoint:

    from tests.stubs.uno_stubs import install
    install()
    from src.mirai.entrypoint import MainJob
"""
import base64
import json
import os
import sys
import time
from unittest.mock import MagicMock


# Each UNO interface used as a base class must be a *distinct* Python class;
# reusing `object` for all of them causes "duplicate base class" errors.
class _UnoBase:          pass
class _XJobExecutor:     pass
class _XJob:             pass
class _XActionListener:  pass
class _XItemListener:    pass
class _XMouseListener:   pass
class _XKeyListener:     pass
class _XWindowListener:  pass
class _XTopWindowListener: pass
class _XSelectionChangeListener: pass
class _XCommandEnvironment: pass
class _XInteractionHandler: pass
class _XCallback:        pass


def install():
    """Patch sys.modules with UNO stubs. Idempotent."""
    if sys.modules.get("uno") and getattr(sys.modules["uno"], "_STUB", False):
        return

    uno = MagicMock()
    uno._STUB = True
    uno.fileUrlToSystemPath = lambda url: url.replace("file://", "")
    uno.systemPathToFileUrl = lambda path: "file://" + path
    uno.createUnoStruct = MagicMock(return_value=MagicMock())

    unohelper = MagicMock()
    unohelper.Base = _UnoBase          # real class, not object

    officehelper = MagicMock()

    com_sun_star_task = MagicMock()
    com_sun_star_task.XJobExecutor = _XJobExecutor
    com_sun_star_task.XJob = _XJob
    com_sun_star_task.XInteractionHandler = _XInteractionHandler

    com_sun_star_ucb = MagicMock()
    com_sun_star_ucb.XCommandEnvironment = _XCommandEnvironment

    com_sun_star_awt = MagicMock()
    com_sun_star_awt.MessageBoxButtons = MagicMock()
    com_sun_star_awt.MessageBoxType = MagicMock()
    com_sun_star_awt.XActionListener  = _XActionListener
    com_sun_star_awt.XItemListener    = _XItemListener
    com_sun_star_awt.XMouseListener   = _XMouseListener
    com_sun_star_awt.XKeyListener     = _XKeyListener
    com_sun_star_awt.XWindowListener  = _XWindowListener
    com_sun_star_awt.XTopWindowListener = _XTopWindowListener
    com_sun_star_awt.XCallback         = _XCallback

    com_sun_star_awt_msgtype = MagicMock()
    com_sun_star_awt_msgtype.MESSAGEBOX = 0

    com_sun_star_beans = MagicMock()
    com_sun_star_beans.PropertyValue = MagicMock

    com_sun_star_container = MagicMock()

    com_sun_star_view = MagicMock()
    com_sun_star_view.XSelectionChangeListener = _XSelectionChangeListener

    com_sun_star_frame = MagicMock()

    com_sun_star_ui = MagicMock()

    sys.modules.update({
        "uno": uno,
        "unohelper": unohelper,
        "officehelper": officehelper,
        "com": MagicMock(),
        "com.sun": MagicMock(),
        "com.sun.star": MagicMock(),
        "com.sun.star.task": com_sun_star_task,
        "com.sun.star.ucb": com_sun_star_ucb,
        "com.sun.star.awt": com_sun_star_awt,
        "com.sun.star.awt.MessageBoxType": com_sun_star_awt_msgtype,
        "com.sun.star.beans": com_sun_star_beans,
        "com.sun.star.container": com_sun_star_container,
        "com.sun.star.view": com_sun_star_view,
        "com.sun.star.frame": com_sun_star_frame,
        "com.sun.star.ui": com_sun_star_ui,
        "com.sun.star.lang": MagicMock(),
        "com.sun.star.deployment": MagicMock(),
    })


_STARTUP_LAUNCHERS = (
    "_schedule_config_refresh",
    "_warmup_secure_flow_async",
    "_send_telemetry",
    "_ensure_device_management_state_async",
    "_schedule_update_reconciliation",
    "_schedule_native_feed_check",
    "_schedule_feed_rewrite",
    "_schedule_enrollment_check",
)


def make_job(config_dir=None):
    """
    Instantiate MainJob with a fully mocked UNO context.

    Args:
        config_dir: directory used as UserConfig (defaults to
                    /tmp/test_libreoffice_config, shared between tests and
                    purged by tests/unit/conftest.py).
                    Pass a real tempfile.mkdtemp() path for tests that write files.
    Returns:
        A MainJob instance ready for unit testing.
    """
    install()

    from src.mirai.entrypoint import MainJob

    path_settings = MagicMock()
    path_settings.UserConfig = config_dir or "/tmp/test_libreoffice_config"

    service_manager = MagicMock()
    service_manager.createInstanceWithContext.return_value = path_settings

    ctx = MagicMock()
    ctx.ServiceManager = service_manager
    ctx.getServiceManager.return_value = service_manager

    # Les tâches de fond de démarrage sont désarmées AVANT la construction :
    # `MainJob.__init__` les lance en threads, et ces threads réécrivent
    # mirai/settings.json (et l'instantané DM mirai/dm_snapshot.json). Les
    # neutraliser après coup laisse la course ouverte : selon la
    # charge, l'un d'eux écrase la valeur que le test vient d'écrire, et l'échec
    # se déplace d'un test à l'autre. On patche donc la CLASSE le temps de
    # l'instanciation, puis on la restaure pour ne rien laisser fuir.
    originals = {name: getattr(MainJob, name) for name in _STARTUP_LAUNCHERS}
    for name in originals:
        setattr(MainJob, name, lambda self, *a, **k: None)
    try:
        job = MainJob(ctx)
    finally:
        for name, method in originals.items():
            setattr(MainJob, name, method)

    deadline = time.time() + 2.0
    while getattr(job, "_fetching_config", False) and time.time() < deadline:
        time.sleep(0.01)
    job._fetching_config = False
    job._schedule_config_refresh = MagicMock()
    return job


def seed_user_config(config_dir, data):
    """Remplace les réglages utilisateur par `data`, comme les aurait laissés
    set_config : secrets dans le coffre (mémoire en test), le reste dans
    <config_dir>/mirai/settings.json."""
    from src.mirai import credentials, local_config
    plain = {key: value for key, value in data.items()
             if key not in credentials.STORED_KEYS and key not in credentials.MEMORY_KEYS}
    path = os.path.join(local_config.data_dir(config_dir), local_config.SETTINGS_FILE)
    local_config.write_json_atomic(path, plain)
    scope = local_config.LocalConfig(config_dir, []).transport_scope()
    for key in credentials.STORED_KEYS:
        credentials.set_secret(key, data.get(key, ""), scope)
    for key in credentials.MEMORY_KEYS:
        credentials.remember(key, data.get(key, ""))
    return path


def read_user_config(config_dir):
    from src.mirai import local_config
    return local_config.read_json(
        os.path.join(local_config.data_dir(config_dir), local_config.SETTINGS_FILE))


def make_jwt(payload):
    """JWT non signé : le code testé ne lit que la charge utile."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"
