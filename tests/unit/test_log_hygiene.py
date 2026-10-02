"""Le journal ne reçoit ni jeton ni texte du document."""

import json
from unittest.mock import MagicMock, patch

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.menu_actions.calc import _generate_formula, _transform_to_column  # noqa: E402
from tests.unit.core.test_palette_build import _build, palette_module  # noqa: E402,F401
from tests.unit.test_calc_menu_actions import _make_cell, _make_job, _make_sheet  # noqa: E402

SECRET = "SECRET-llmToken-0123456789"
DOCUMENT = "Texte confidentiel du document"


def _response(body):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


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
