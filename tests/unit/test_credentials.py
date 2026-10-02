"""Jetons courts en mémoire du processus, jamais sur disque."""

import time

from src.mirai import credentials


def setup_function():
    credentials.forget_all()


def test_remember_and_recall():
    credentials.remember("x", "v")
    assert credentials.recall("x") == "v"


def test_expired_value_is_not_recalled_but_expiry_is_known():
    credentials.remember("x", "v", time.time() - 10)
    assert credentials.recall("x") == ""
    assert credentials.expires_at("x") > 0


def test_empty_value_forgets():
    credentials.remember("x", "v")
    credentials.remember("x", "")
    assert credentials.recall("x") == ""
    assert credentials.expires_at("x") == 0


def test_dm_tokens_are_remembered_and_revoked():
    credentials.remember_dm_tokens({"llmToken": "t1",
                                    "llmTokenExpiresAt": int(time.time()) + 3600,
                                    "telemetryKey": "k1"})
    assert credentials.recall(credentials.DM_LLM_TOKEN) == "t1"
    assert credentials.recall(credentials.DM_TELEMETRY_KEY) == "k1"
    credentials.remember_dm_tokens({"llmToken": ""})
    assert credentials.recall(credentials.DM_LLM_TOKEN) == ""
    assert credentials.recall(credentials.DM_TELEMETRY_KEY) == "k1"


def test_direct_mode_token_is_remembered():
    credentials.remember_dm_tokens({"llm_api_tokens": "provider-key"})
    assert credentials.recall(credentials.DM_LLM_TOKEN) == "provider-key"
