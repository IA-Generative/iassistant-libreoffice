"""Le journal ne reçoit ni jeton ni texte du document."""

import io
import json
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.calc_prompt_function import call_llm
from src.mirai.core import sse_pump
from src.mirai.entrypoint import _send_telemetry_trace_impl
from src.mirai.menu_actions.calc import _generate_formula, _transform_to_column
from tests.stubs.fake_shell import FakeShell
from tests.unit.core.test_palette_build import _build, palette_module  # noqa: F401
from tests.unit.test_calc_menu_actions import _make_cell, _make_job, _make_sheet
from tests.unit.test_calc_prompt_function import _base_config

SECRET = "SECRET-llmToken-0123456789"
DOCUMENT = "Texte confidentiel du document"
# Corps d'erreur d'un serveur qui renvoie ce qu'il a reçu.
ECHO = json.dumps({"detail": f"{SECRET} {DOCUMENT}"})


def _response(body):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(status, body=ECHO):
    return urllib.error.HTTPError("https://serveur.example", status, "err", {},
                                  io.BytesIO(body.encode("utf-8")))


def _entrypoint_log(action):
    lines = []
    with patch("src.mirai.entrypoint.log_to_file", side_effect=lines.append):
        action()
    return [str(line) for line in lines]


def _leaks(lines):
    return [line for line in lines if SECRET in line or DOCUMENT in line]


def test_dm_response_is_not_logged(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._failover_ordered_urls = lambda: ["https://dm.example"]
    payload = json.dumps({"config": {
        "llmToken": SECRET, "llm_api_tokens": SECRET, "embdToken": SECRET,
        "telemetryKey": SECRET, "llm_base_urls": "https://dm.example/llm/v1",
    }}).encode("utf-8")
    job._urlopen = MagicMock(return_value=_response(payload))
    lines = []
    with patch("src.mirai.entrypoint.log_to_file", side_effect=lines.append):
        job._fetch_config(force=True)
    assert lines
    assert [line for line in lines if SECRET in str(line)] == []


def test_llm_request_body_is_not_logged(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    values = {"llm_base_urls": "https://llm.example/v1", "llm_api_tokens": SECRET,
              "llm_default_models": "model-a"}
    job.get_config = MagicMock(side_effect=lambda key, default=None: values.get(key, default))
    lines = []
    with patch("src.mirai.entrypoint.log_to_file", side_effect=lines.append):
        job.make_api_request(DOCUMENT, system_prompt="Consigne", max_tokens=100)
    leaked = [line for line in lines if DOCUMENT in str(line) or SECRET in str(line)]
    assert leaked == []


def test_journal_detail_shows_in_actions_tab_but_only_size_is_logged(palette_module):  # noqa: F811
    palette = _build(palette_module)
    lines = []
    palette.shell.log = lines.append

    palette.journal_line("↳ Titre conservé", detail=f"« {DOCUMENT} »")

    assert DOCUMENT in palette._models["journal"].Text
    assert [line for line in lines if DOCUMENT in str(line)] == []
    assert any("Titre conservé" in line and "car.)" in line for line in lines)


def test_calc_transform_does_not_log_cell_text():
    sheet, _ = _make_sheet({(0, 0): DOCUMENT})
    job = _make_job(["résultat"])
    _transform_to_column(job, sheet, range(0, 1), range(0, 1), "traduis")
    logged = " ".join(str(call) for call in job._log.call_args_list)
    assert "[transform]" in logged
    assert DOCUMENT not in logged


def test_calc_formula_does_not_log_the_generated_formula():
    formula = "=SUM(A1:A9)+42424242"
    job = _make_job([formula])
    _generate_formula(job, _make_cell(), "somme")
    logged = " ".join(str(call) for call in job._log.call_args_list)
    assert "[formula]" in logged
    assert "42424242" not in logged


def test_dm_error_body_is_not_logged(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._failover_ordered_urls = lambda: ["https://dm.example"]
    job._urlopen = MagicMock(side_effect=_http_error(500))
    lines = _entrypoint_log(lambda: job._fetch_config(force=True))
    assert any("HTTP 500" in line for line in lines)
    assert _leaks(lines) == []


def test_enroll_error_body_is_not_logged(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._device_management_enabled = lambda: True
    job._fetch_config = MagicMock(return_value={"config": {"enroll": "https://dm.example/enroll"}})
    job._sync_keycloak_from_config = MagicMock()
    job._ensure_access_token = MagicMock(return_value="tok")
    job._token_email = MagicMock(return_value="a@b.c")
    job._keycloak_config = MagicMock(return_value={})
    job._keycloak_endpoint = MagicMock(return_value="")
    job._ensure_extension_uuid = MagicMock(return_value="uuid")
    job._relay_credentials_valid = lambda: False
    job._urlopen = MagicMock(side_effect=_http_error(409))
    lines = _entrypoint_log(job._ensure_device_management_state)
    assert any("enroll failed" in line for line in lines)
    assert _leaks(lines) == []


def test_llm_error_body_is_not_logged(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._send_telemetry = lambda name, attrs=None: None
    job._show_message = lambda title, msg: None
    job._show_thinking = lambda: None
    job._update_thinking_dots = lambda: None
    job._close_thinking = lambda: None
    job.get_ssl_context = lambda *a, **k: None
    job._urlopen = MagicMock(side_effect=_http_error(500))
    request = urllib.request.Request("https://dm.example/llm/v1/chat/completions")
    lines = _entrypoint_log(lambda: job.stream_request(request, "chat", lambda chunk: None))
    assert any("HTTP 500" in line for line in lines)
    assert _leaks(lines) == []


def test_palette_stream_error_body_is_not_logged():
    shell = FakeShell(responses=[_http_error(500)])
    sse_pump.run_stream(shell, object(), lambda event: None)
    assert any("HTTP 500" in line for line in shell.logs)
    assert _leaks(shell.logs) == []


def test_telemetry_error_body_is_not_logged(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._secure_send_telemetry_payload = MagicMock(return_value=False)
    job._urlopen = MagicMock(side_effect=_http_error(400))
    lines = _entrypoint_log(lambda: _send_telemetry_trace_impl(job, "Span"))
    assert any("Status: 400" in line for line in lines)
    assert _leaks(lines) == []


def test_prompt_error_body_reaches_the_cell_but_not_the_log():
    with patch("src.mirai.calc_prompt_function._urlopen", side_effect=_http_error(502)), \
            patch("src.mirai.calc_prompt_function._log") as log:
        cell = call_llm("msg", "", "", 10, _base_config(), None)
    logged = [str(call) for call in log.call_args_list]
    assert any("502" in line for line in logged)
    assert _leaks(logged) == []
    assert cell.startswith("#PROMPT_ERROR: HTTP 502") and DOCUMENT in cell


def test_prompt_unexpected_reply_is_not_logged():
    reply = _response(json.dumps({"output": DOCUMENT}).encode("utf-8"))
    with patch("src.mirai.calc_prompt_function._urlopen", return_value=reply), \
            patch("src.mirai.calc_prompt_function._log") as log:
        cell = call_llm("msg", "", "", 10, _base_config(), None)
    logged = [str(call) for call in log.call_args_list]
    assert cell == "#PROMPT_ERROR: unexpected response structure"
    assert any("output" in line for line in logged)
    assert _leaks(logged) == []
