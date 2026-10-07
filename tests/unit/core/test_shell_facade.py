"""Façade : équivalence build_chat_request avec le vrai MainJob + clamps."""

import json
import tempfile

from tests.stubs.uno_stubs import install, make_job

install()

from src.mirai.core.shell_facade import (  # install() doit précéder l'import
    MainJobShell,
    clamp_max_tokens,
)


def _job_with_config(**config):
    job = make_job(config_dir=tempfile.mkdtemp())
    for key, value in config.items():
        job.set_config(key, value)
    return job


def test_clamp_table():
    assert clamp_max_tokens("llama-3.3-70b-instruct", 10000) == 4096
    assert clamp_max_tokens("DeepSeek-R1-Distill-Llama-70B", 20000) == 8196
    assert clamp_max_tokens("mistral-small", 10000) == 10000
    assert clamp_max_tokens("", 10000) == 10000


def test_build_chat_request_equivalent_to_mainjob():
    job = _job_with_config(llm_base_urls="https://relay.example/api",
                           llm_api_tokens="tok-123",
                           llm_default_models="mistral-small")
    shell = MainJobShell(job)
    messages = [{"role": "user", "content": "bonjour"}]

    reference = job.make_chat_request(messages, 2000)
    built = shell.build_chat_request(messages, 2000)

    assert built.full_url == reference.full_url
    ref_body = json.loads(reference.data.decode("utf-8"))
    new_body = json.loads(built.data.decode("utf-8"))
    assert new_body == ref_body
    # en-têtes (dont Authorization) préservés
    assert dict(built.header_items()) == dict(reference.header_items())


def test_build_chat_request_applies_model_clamp():
    job = _job_with_config(llm_base_urls="https://relay.example/api",
                           llm_default_models="llama-3.3-70b-instruct")
    shell = MainJobShell(job)
    built = shell.build_chat_request([{"role": "user", "content": "x"}],
                                     max_tokens=10000)
    body = json.loads(built.data.decode("utf-8"))
    assert body["max_tokens"] == 4096
    assert body["max_completion_tokens"] == 4096


def test_build_chat_request_merges_extra_body():
    job = _job_with_config(llm_base_urls="https://relay.example/api")
    shell = MainJobShell(job)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    built = shell.build_chat_request([{"role": "user", "content": "x"}],
                                     extra_body={"tools": tools,
                                                 "tool_choice": "auto"})
    body = json.loads(built.data.decode("utf-8"))
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["stream"] is True


def test_report_llm_error_delegates_to_shell_pipeline():
    job = _job_with_config()
    sent = []
    job._send_llm_relay_error = lambda *args, **kwargs: sent.append((args, kwargs))
    shell = MainJobShell(job)
    code, retry_after = shell.report_llm_error(
        429, '{"error": {"code": "quota"}, "retry_after": 30}', None)
    assert code == "quota" and retry_after == 30
    assert sent and sent[0][0][0] == 429


def test_telemetry_passthrough():
    job = _job_with_config()
    seen = []
    job._send_telemetry = lambda span, attrs=None: seen.append((span, attrs))
    shell = MainJobShell(job)
    shell.telemetry("AssistantRun", {"plugin.action": "assistant.run"})
    assert seen == [("AssistantRun", {"plugin.action": "assistant.run"})]
