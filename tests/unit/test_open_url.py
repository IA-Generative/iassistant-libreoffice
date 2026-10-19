"""Ouverture d'une URL : Python d'abord, LibreOffice ensuite, jamais l'URL
dans le journal."""

import logging
from unittest.mock import MagicMock, patch

from tests.stubs.uno_stubs import install

install()

from src.mirai.shell.open_url import open_url

URL = "https://sso.example.fr/auth?state=STATE_SECRET&code_challenge=CHALLENGE_SECRET"
URIS_ONLY_WITHOUT_SYSTEM_ERROR_BOX = 3


def _ctx(shell):
    ctx = MagicMock()
    ctx.getServiceManager().createInstanceWithContext.return_value = shell
    return ctx


def test_python_browser_takes_the_url_and_libreoffice_is_not_asked():
    shell = MagicMock()
    with patch("webbrowser.open", return_value=True):
        assert open_url(_ctx(shell), URL) is True
    shell.execute.assert_not_called()


def test_libreoffice_opens_the_url_when_python_finds_no_browser():
    shell = MagicMock()
    with patch("webbrowser.open", return_value=False):
        assert open_url(_ctx(shell), URL) is True
    shell.execute.assert_called_once_with(URL, "", URIS_ONLY_WITHOUT_SYSTEM_ERROR_BOX)


def test_libreoffice_opens_the_url_when_python_raises():
    shell = MagicMock()
    with patch("webbrowser.open", side_effect=OSError("no browser")):
        assert open_url(_ctx(shell), URL) is True
    shell.execute.assert_called_once()


def test_failure_of_both_is_reported_without_the_url_in_the_log(caplog):
    shell = MagicMock()
    shell.execute.side_effect = RuntimeError(
        f"XSystemShellExecute.execute URIS_ONLY with non-absolute URI reference {URL}")
    with patch("webbrowser.open", return_value=False), caplog.at_level(logging.INFO):
        assert open_url(_ctx(shell), URL) is False
    assert [line for line in caplog.messages
            if "STATE_SECRET" in line or "CHALLENGE_SECRET" in line] == []
    assert any("RuntimeError" in line for line in caplog.messages)
