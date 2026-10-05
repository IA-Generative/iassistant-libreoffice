"""Dégradation propre + anti-boucle quand l'install auto de la MAJ est bloquée
par la politique du poste (WinError 5 — AppLocker / Defender ASR).

Run:  pytest tests/unit/test_update_blocked.py -v
"""
import threading
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.entrypoint import MainJob


def test_schedule_update_skips_blocked_target():
    """Un target déjà marqué « bloqué » ne relance ni download ni prompt."""
    job = make_job()
    job._perform_update = MagicMock()
    MainJob._update_launch_blocked_cls.add("0.0.1.0.14")

    job._schedule_update({"action": "update", "target_version": "0.0.1.0.14"})

    job._perform_update.assert_not_called()
    assert MainJob._update_in_progress_cls is False


def test_schedule_update_runs_for_unblocked_target():
    """Un target non bloqué démarre bien le worker."""
    job = make_job()
    done = threading.Event()
    job._perform_update = lambda directive: done.set()

    job._schedule_update({"action": "update", "target_version": "0.0.2"})

    assert done.wait(2), "le worker d'update aurait dû tourner"


def test_notify_update_blocked_never_raises():
    """Le message d'échec ne doit jamais faire planter le worker (UNO best-effort)."""
    job = make_job()
    # Ne lève pas, même si le toolkit/MSG_BUTTONS n'est pas pleinement stubbé.
    assert job._notify_update_blocked("0.0.1.0.14", r"C:\x\pending_update\mirai_update.bat") is None


def test_report_update_status_sends_relay_headers():
    """/update/status exige des relay-creds (DM VULN-007) : le POST doit porter
    X-Relay-Client / X-Relay-Key, sinon 401 (bug observé sur log MI)."""
    job = make_job()
    job._active_bootstrap_url = MagicMock(return_value="https://dm")
    job._ensure_plugin_uuid = MagicMock(return_value="uuid-1")
    job._get_config_from_file = MagicMock(return_value="")   # pas d'access_token
    job.get_ssl_context = MagicMock(return_value=None)
    job._relay_headers = MagicMock(return_value={"X-Relay-Client": "rc_x", "X-Relay-Key": "k"})

    captured = {}

    def _fake_urlopen(req, **kw):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        resp = MagicMock()
        resp.read.return_value = b"{}"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    job._urlopen = _fake_urlopen
    job._report_update_status(1, "installed", "0.0.1.0.13", "0.0.1.0.14")

    assert "x-relay-client" in captured["headers"]
    assert "x-relay-key" in captured["headers"]


# ── Option B : install in-process (fallback géré par l'appelant) ─────

def test_in_process_install_guard_rejects_bad_path():
    """Chemin vide / inexistant → False sans toucher UNO ni le restart."""
    job = make_job()
    job._terminate_on_main_thread = MagicMock()  # sécurité (ne pas SIGTERM le test)
    assert job._install_and_restart_in_process("") is False
    assert job._install_and_restart_in_process("/nope/does-not-exist.oxt") is False
    job._terminate_on_main_thread.assert_not_called()


def test_in_process_install_degrades_gracefully(tmp_path):
    """Fichier réel mais service de déploiement absent → False, aucun restart
    (l'appelant retombera sur le script puis le message manuel)."""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".oxt")
    os.close(fd)
    try:
        job = make_job()
        job._terminate_on_main_thread = MagicMock()  # sécurité (ne pas SIGTERM le test)
        job._run_install_on_main_thread = MagicMock(return_value=False)  # main thread KO
        job._install_oxt_inprocess = MagicMock(return_value=False)  # aucune API dispo
        assert job._install_and_restart_in_process(path) is False
        job._terminate_on_main_thread.assert_not_called()
    finally:
        os.remove(path)


# ── Install in-process : PackageManagerFactory via getValueByName (sans import,
#    thread-safe côté worker) ; l'import `from com.sun.star…` échoue hors thread
#    principal ("No module named 'com'") — d'où le pré-bind + ce chemin. ─────────

def test_install_oxt_inprocess_uses_package_manager_factory():
    """Chemin principal : thePackageManagerFactory (getValueByName, pas d'import)
    → getPackageManager('user') → removePackage PUIS addPackage (remove-avant-add,
    évite le doublon 'Insert duplicate implementation name …')."""
    job = make_job()
    factory = MagicMock(name="thePackageManagerFactory")
    job.ctx.getValueByName.return_value = factory
    pkg = factory.getPackageManager.return_value

    assert job._install_oxt_inprocess("file:///x.oxt", (), None, None) is True
    job.ctx.getValueByName.assert_called_with(
        "/singletons/com.sun.star.deployment.thePackageManagerFactory"
    )
    factory.getPackageManager.assert_called_with("user")
    pkg.removePackage.assert_called_once()
    pkg.addPackage.assert_called_once()
    names = [c[0] for c in pkg.mock_calls]
    assert names.index("removePackage") < names.index("addPackage"), \
        "remove doit précéder add"


def test_install_and_restart_closes_after_success():
    """Install in-process OK → _close_after_inprocess_update() est appelé (fermeture,
    PAS de re-exec), retourne True."""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".oxt")
    os.close(fd)
    try:
        job = make_job()
        job._run_install_on_main_thread = MagicMock(return_value=False)  # force le legacy
        job._install_oxt_inprocess = MagicMock(return_value=True)
        job._close_after_inprocess_update = MagicMock()  # évite le vrai terminate/SIGTERM
        assert job._install_and_restart_in_process(path) is True
        job._close_after_inprocess_update.assert_called_once()
    finally:
        os.remove(path)


def test_install_oxt_inprocess_false_when_no_api():
    """Ni factory ni singleton ExtensionManager → False (repli script/manuel)."""
    import src.mirai.entrypoint as ep

    job = make_job()
    job.ctx.getValueByName.return_value = None
    prev = ep._EXT_MGR_SINGLETON
    ep._EXT_MGR_SINGLETON = None
    try:
        assert job._install_oxt_inprocess("file:///x.oxt", (), None, None) is False
    finally:
        ep._EXT_MGR_SINGLETON = prev


# ── Bouton « Ouvrir le dossier » : ouverture native, sans cmd.exe ────

def test_open_folder_native_uses_shell_execute():
    """Ouvre le dossier via UNO SystemShellExecute (ShellExecute) — jamais un
    subprocess/cmd.exe. On vérifie l'URL file:// et le flag NO_SYSTEM_ERROR_MESSAGE."""
    import tempfile

    d = tempfile.mkdtemp()
    shell = MagicMock()
    job = make_job()
    job.ctx.getServiceManager.return_value.createInstanceWithContext.return_value = shell

    assert job._open_folder_native(d) is True
    shell.execute.assert_called_once()
    args = shell.execute.call_args.args
    assert args[0].startswith("file://")          # URL du dossier
    assert d in args[0]
    assert args[2] == 1                            # NO_SYSTEM_ERROR_MESSAGE


def test_open_folder_native_rejects_bad_path():
    """Chemin vide / inexistant → False, sans instancier le service shell."""
    job = make_job()
    assert job._open_folder_native("") is False
    assert job._open_folder_native("/nope/does-not-exist-xyz") is False


def test_notify_update_blocked_opens_folder_on_yes():
    """Quand le .oxt téléchargé existe et que l'utilisateur clique « Oui » (result==2),
    la boîte déclenche l'ouverture native du dossier."""
    import os
    import tempfile

    d = tempfile.mkdtemp()
    open(os.path.join(d, "mirai_update.oxt"), "w").close()
    script = os.path.join(d, "mirai_update.bat")

    job = make_job()
    job._open_folder_native = MagicMock()
    # createMessageBox(...).execute() → 2 (MessageBoxResults.YES)
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    toolkit.createMessageBox.return_value.execute.return_value = 2

    job._notify_update_blocked("0.0.1.0.16", script)

    job._open_folder_native.assert_called_once_with(d)


def test_notify_update_blocked_no_open_on_no():
    """« Non » (result==3) : aucune ouverture de dossier."""
    import os
    import tempfile

    d = tempfile.mkdtemp()
    open(os.path.join(d, "mirai_update.oxt"), "w").close()
    script = os.path.join(d, "mirai_update.bat")

    job = make_job()
    job._open_folder_native = MagicMock()
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    toolkit.createMessageBox.return_value.execute.return_value = 3

    job._notify_update_blocked("0.0.1.0.16", script)

    job._open_folder_native.assert_not_called()


def test_notify_update_blocked_uses_querybox_when_folder_offered():
    """Régression : avec un dossier connu, la boîte doit être un QUERYBOX (type 4)
    pour afficher Oui/Non. Un INFOBOX (type 1) n'affiche qu'OK → le bouton « Oui »
    ne s'affichait pas (bug observé en test réel sur la 0.0.1.0.18)."""
    import os
    import tempfile

    d = tempfile.mkdtemp()
    open(os.path.join(d, "mirai_update.oxt"), "w").close()
    script = os.path.join(d, "mirai_update.bat")

    job = make_job()
    job._open_folder_native = MagicMock()
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    toolkit.createMessageBox.return_value.execute.return_value = 3

    job._notify_update_blocked("0.0.1.0.18", script)

    args = toolkit.createMessageBox.call_args.args
    assert args[1] == 4, "doit être un QUERYBOX (4) pour afficher Oui/Non"


def test_notify_update_blocked_uses_infobox_without_folder():
    """Sans dossier connu : INFOBOX (type 1) + OK seul (rien à ouvrir)."""
    import tempfile

    job = make_job(config_dir=tempfile.mkdtemp())  # pas de sous-dossier pending_update
    toolkit = job.ctx.getServiceManager.return_value.createInstance.return_value
    toolkit.createMessageBox.return_value.execute.return_value = 1

    job._notify_update_blocked("0.0.1.0.18", r"C:\nope\pending_update\mirai_update.bat")

    args = toolkit.createMessageBox.call_args.args
    assert args[1] == 1, "doit rester un INFOBOX (1) quand il n'y a pas de dossier à ouvrir"
