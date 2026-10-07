"""Dispatch des actions : rien ne doit être avalé en silence.

Ces tests verrouillent les quatre défauts relevés en qualification de `master`
(R-04 à R-07). Ils ont un point commun : dans chaque cas l'utilisateur cliquait
et il ne se passait *rien du tout* — pas de message, pas de trace. Un clic sans
effet n'est pas diagnosticable ; c'est ce que ces tests interdisent désormais.
"""

from unittest.mock import MagicMock

from src.mirai.menu_actions.calc import handle_calc_action
from src.mirai.menu_actions.writer import handle_writer_action
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


class _WriterModel:
    """Document Writer dont la sélection est vide (le cas qui avalait tout)."""

    def __init__(self, empty=True):
        self.Text = MagicMock()
        controller = MagicMock()
        selection = MagicMock()
        selection.getCount.return_value = 0 if empty else 1
        selection.getByIndex.return_value = MagicMock(
            getString=MagicMock(return_value="du texte"))
        controller.getSelection.return_value = selection
        self.CurrentController = controller


# ── R-04 : entrées de menu Calc mortes ──────────────────────────────────

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


# ── R-05 : sélection vide n'avale plus tout ─────────────────────────────

def test_empty_selection_tells_the_user():
    job, rec = _job()

    assert handle_writer_action(job, "ExtendSelection", _WriterModel()) is True
    assert rec.messages, "sélection vide : l'utilisateur doit être prévenu"
    assert "curseur" in rec.messages[0][1].lower()


def test_writer_handler_ignores_shell_actions():
    """Elles sont traitées en amont : les laisser ici les ré-exposerait au
    « return True » sur sélection vide qui les faisait disparaître."""
    job, _rec = _job()
    for action in ("settings", "AboutDialog", "Documentation", "OpenmiraiWebsite"):
        assert handle_writer_action(job, action, _WriterModel()) is False, action


def test_writer_handler_still_refuses_non_writer_documents():
    job, _rec = _job()
    assert handle_writer_action(job, "ExtendSelection", object()) is False


# ── R-07 : les pannes Calc ne sont plus muettes ─────────────────────────

def test_calc_failure_is_reported():
    """Tout le corps de handle_calc_action était sous `except Exception: pass`."""
    job, rec = _job()

    model = MagicMock()
    model.Sheets = MagicMock()
    model.CurrentController.ActiveSheet = MagicMock()
    # La résolution de la plage explose → auparavant : silence total.
    model.CurrentController.Selection.getRangeAddress.side_effect = RuntimeError(
        "plage illisible")

    assert handle_calc_action(job, "AnalyzeRange", model) is True
    assert rec.messages, "une panne Calc doit être annoncée"
    assert "plage illisible" in rec.messages[0][1]


def test_calc_handler_still_refuses_non_calc_documents():
    job, _rec = _job()
    assert handle_calc_action(job, "AnalyzeRange", object()) is False


# ── Santé technique : les pannes muettes deviennent des spans ───────────

def _telemetry_recorder(job):
    seen = []
    job._send_telemetry = (
        lambda span, attrs=None: seen.append((span, dict(attrs or {}))))
    return seen


def test_unhandled_action_is_telemetered_once_per_action():
    """Le clic sans effet laissait un log local ; le parc, lui, ne voyait
    rien. Une émission par nom d'action et par session — pas une par clic."""
    from src.mirai.entrypoint import MainJob

    job = make_job()
    MainJob._unhandled_reported_cls = set()
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
