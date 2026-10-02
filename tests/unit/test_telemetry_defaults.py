"""Aucune clé de télémétrie embarquée dans le code public."""

from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.entrypoint import _send_telemetry_trace_impl  # noqa: E402


def test_trace_without_any_configured_key_carries_no_authorization(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._secure_send_telemetry_payload = MagicMock(return_value=False)
    job._urlopen = MagicMock()

    _send_telemetry_trace_impl(job, "Span")

    job._urlopen.assert_called_once()
    request = job._urlopen.call_args[0][0]
    assert request.get_header("Authorization") is None
