"""Dispatch des actions : rien ne doit être avalé en silence.

Une action de la coquille aboutit avec ou sans document, et son échec s'affiche ;
une action que rien ne traite laisse une trace. Un clic sans effet ni trace ne
se diagnostique pas.
"""

from unittest.mock import MagicMock, patch

from src.mirai.i18n import t
from tests.stubs.uno_stubs import make_job


class _Recorder:
    """Job instrumenté : retient messages affichés et actions déclenchées."""

    def __init__(self, job):
        self.job = job
        self.messages = []
        self.opened_urls = []
        self.settings_opened = 0
        self.about_opened = 0
        job._show_message = lambda title, body: self.messages.append((title, body))
        job._send_telemetry = lambda *a, **k: None
        job._show_about_dialog = self._about
        job.settings_box = self._settings
        job._open_url_config = self.opened_urls.append

    def _about(self):
        self.about_opened += 1

    def _settings(self, _title):
        self.settings_opened += 1
        return {}


def _job():
    job = make_job()
    return job, _Recorder(job)


def test_documentation_works_without_any_document():
    """Déclarée dans les menus Writer ET Calc, elle n'était branchée nulle part
    côté Calc — et pas du tout sans document."""
    job, rec = _job()
    assert job._handle_shell_action("Documentation") is True
    assert rec.opened_urls == ["doc_url"]


def test_website_works_without_any_document():
    job, rec = _job()
    assert job._handle_shell_action("OpenmiraiWebsite") is True
    assert rec.opened_urls == ["portal_url"]


def test_documentation_without_any_browser_tells_the_user():
    """Ni Python ni LibreOffice n'ouvrent l'URL : un message, pas un clic muet."""
    job = make_job()
    messages = []
    job._show_message = lambda title, body: messages.append(body)
    job._send_telemetry = lambda *a, **k: None
    job.get_config = lambda key, default="": "https://doc.example" if key == "doc_url" else default
    job.ctx.getServiceManager().createInstanceWithContext.side_effect = RuntimeError("no handler")

    with patch("webbrowser.open", return_value=False):
        assert job._handle_shell_action("Documentation") is True
    assert messages and t("msg.no_browser") in messages[0]


def test_settings_and_about_work_without_any_document():
    job, rec = _job()
    assert job._handle_shell_action("settings") is True
    assert job._handle_shell_action("AboutDialog") is True
    assert rec.settings_opened == 1
    assert rec.about_opened == 1


def test_shell_action_failure_is_visible():
    """Une action de coquille qui échoue doit produire un message, pas un vide."""
    job, rec = _job()
    job.settings_box = MagicMock(side_effect=RuntimeError("dialogue indisponible"))

    assert job._handle_shell_action("settings") is True
    assert rec.messages, "l'échec doit être annoncé à l'utilisateur"
    assert "dialogue indisponible" in rec.messages[0][1]


def test_unknown_action_is_not_a_shell_action():
    job, _rec = _job()
    assert job._handle_shell_action("ExtendSelection") is False


# ── Santé technique : les pannes muettes deviennent des spans ───────────

def _telemetry_recorder(job):
    seen = []
    job._send_telemetry = (
        lambda span, attrs=None: seen.append((span, dict(attrs or {}))))
    return seen


def test_unhandled_action_is_telemetered_once_per_action():
    """Le clic sans effet laissait un log local ; le parc, lui, ne voyait
    rien. Une émission par nom d'action et par session — pas une par clic."""

    job = make_job()
    seen = _telemetry_recorder(job)

    job._report_unhandled_action("ActionFantome", object())
    job._report_unhandled_action("ActionFantome", object())
    job._report_unhandled_action("AutreAction", object())

    spans = [s for s, _ in seen]
    assert spans == ["ActionUnhandled", "ActionUnhandled"]
    assert seen[0][1]["action"] == "ActionFantome"
    assert seen[1][1]["action"] == "AutreAction"


def test_the_new_spans_have_a_condensed_action_name():
    """`plugin.action` est posé par `_send_telemetry` depuis cette table :
    sans entrée, le tableau de bord affiche le nom brut du span."""
    from src.mirai.entrypoint import MainJob

    assert MainJob._ACTION_NAMES["ActionUnhandled"] == "dispatch.unhandled"
    assert MainJob._ACTION_NAMES["ConfigWaitAtTrigger"] == "config.wait"


def test_new_shell_spans_pass_the_identity_filter():
    """Avant login, seuls les spans « techniques » sortent : les deux
    nouveaux doivent en être, sinon ils seraient jetés en silence."""
    from src.mirai.entrypoint import MainJob

    for span in ("ActionUnhandled", "ConfigWaitAtTrigger", "ExtensionLoaded"):
        assert span in MainJob._TECHNICAL_EVENTS, span


def test_config_wait_emits_nothing_when_config_is_ready():
    job = make_job()
    seen = _telemetry_recorder(job)
    job._fetching_config = False
    job.config_cache = {"config": {}}

    assert job._wait_for_config("OpenAssistant") == 0
    assert seen == []


def test_config_wait_reports_duration_and_outcome(monkeypatch):
    job = make_job()
    seen = _telemetry_recorder(job)
    job._fetching_config = True
    job.config_cache = {}

    def _sleep(_seconds):
        job._fetching_config = False
        job.config_cache = {"config": {}}

    monkeypatch.setattr("time.sleep", _sleep)
    job._wait_for_config("OpenAssistant")

    spans = dict(seen)
    assert "ConfigWaitAtTrigger" in spans
    attrs = spans["ConfigWaitAtTrigger"]
    assert attrs["config.available"] is True
    assert isinstance(attrs["config.wait_ms"], int)
    assert attrs["action"] == "OpenAssistant"
