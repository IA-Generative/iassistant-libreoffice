"""Routes de dispatch : chaque commande déclarée dans oxt/*.xcu atteint son
handler, et chaque route de la coquille a un émetteur.

Une commande sans route finit en « Action indisponible » ; une route sans
émetteur est du code mort qu'aucun test d'usage ne voit.
"""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.entrypoint import MainJob  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_COMMAND = re.compile(r"service:fr\.gouv\.interieur\.mirai\.do\?([A-Za-z]+)(?:&amp;|&)src=([a-z]+)")

# Routes de la coquille encore présentes sans émetteur ; leur suppression est
# suivie par un ticket. La liste ne peut que raccourcir.
SANS_EMETTEUR = {"proxy_settings", "OpenmiraiWebsite"}

# Le handler que chaque commande doit atteindre : (cible, nom).
HANDLERS = {
    "OpenAssistant": ("module", "open_palette"),
    "TestModel": ("module", "test_model_capabilities"),
    "settings": ("job", "settings_box"),
    "AboutDialog": ("job", "_show_about_dialog"),
    "Documentation": ("job", "_open_url_config"),
}


def _declared_commands():
    commands = set()
    oxt = os.path.join(_REPO_ROOT, "oxt")
    for name in sorted(os.listdir(oxt)):
        if name.endswith(".xcu"):
            with open(os.path.join(oxt, name), encoding="utf-8") as fh:
                commands |= set(_COMMAND.findall(fh.read()))
    return sorted(commands)


def test_the_xcu_files_declare_commands():
    assert _declared_commands()


@pytest.mark.parametrize("action,source", _declared_commands())
def test_every_declared_command_reaches_its_handler(action, source):
    assert action in HANDLERS, f"commande {action!r} sans handler attendu dans ce test"
    job = make_job()
    job._needs_first_enrollment = lambda: False
    job._wait_for_config = lambda _action: None
    job._hydrate_config_cache = lambda: None
    job._send_telemetry = MagicMock()
    job._show_message = MagicMock()
    job._report_unhandled_action = MagicMock()
    job.settings_box = MagicMock(return_value={})
    job._show_about_dialog = MagicMock()
    job._open_url_config = MagicMock()

    with patch("src.mirai.core.entry.open_palette") as open_palette, \
            patch("src.mirai.core.entry.test_model_capabilities") as test_model:
        job.trigger(f"{action}&src={source}")

    kind, name = HANDLERS[action]
    if kind == "module":
        handler = {"open_palette": open_palette, "test_model_capabilities": test_model}[name]
    else:
        handler = getattr(job, name)
    handler.assert_called_once()
    job._report_unhandled_action.assert_not_called()
    job._show_message.assert_not_called()


def test_every_shell_route_has_an_emitter():
    emitted = {action for action, _ in _declared_commands()}
    orphans = sorted(set(MainJob._SHELL_ACTIONS) - emitted - SANS_EMETTEUR)
    assert orphans == [], f"routes sans émetteur : {orphans}"


def test_routes_listed_without_emitter_still_exist():
    stale = sorted(SANS_EMETTEUR - set(MainJob._SHELL_ACTIONS))
    assert stale == [], f"routes supprimées, à retirer de SANS_EMETTEUR : {stale}"
