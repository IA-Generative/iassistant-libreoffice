"""Jetons courts en mémoire du processus, jamais sur disque."""

import subprocess
import time
from unittest.mock import MagicMock, patch

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


def _fresh_store():
    store = credentials.MemoryStore()
    credentials.use_store(store)
    return store


def test_secret_roundtrip_and_empty_deletes():
    _fresh_store()
    assert credentials.set_secret("proxy_password", "pw", "scope-a")
    assert credentials.get_secret("proxy_password", "scope-b") == "pw"   # non lié
    credentials.set_secret("proxy_password", "", "scope-a")
    assert credentials.get_secret("proxy_password", "scope-a") == ""


def test_scoped_secret_is_invisible_and_dropped_in_another_environment():
    store = _fresh_store()
    credentials.set_secret("relay_client_key", "rk", "scope-a")
    assert credentials.get_secret("relay_client_key", "scope-a") == "rk"
    assert credentials.get_secret("relay_client_key", "scope-b") == ""
    assert store.get("relay_client_key") is None


def test_wipe_deletes_every_stored_key_and_memory():
    store = _fresh_store()
    for name in credentials.STORED_KEYS:
        credentials.set_secret(name, "v", "s")
    credentials.remember("access_token", "at")
    credentials.wipe()
    assert all(store.get(name) is None for name in credentials.STORED_KEYS)
    assert credentials.recall("access_token") == ""


def test_unreadable_entry_counts_as_absent():
    store = _fresh_store()
    store.set("refresh_token", "pas du json")
    assert credentials.get_secret("refresh_token", "s") == ""


def test_keychain_secret_never_on_the_command_line():
    calls = []

    def _run(args, **kwargs):
        calls.append((args, kwargs.get("input")))
        result = MagicMock(returncode=0)
        if args[:2] == ["security", "find-generic-password"]:
            result.stdout = "c2VjcmV0"           # base64("secret")
        return result

    with patch.object(subprocess, "run", side_effect=_run):
        assert credentials.MacKeychainStore().set("refresh_token", "secret")
    for args, _stdin in calls:
        assert "secret" not in " ".join(args)
        assert "c2VjcmV0" not in " ".join(args)
    assert any(stdin and "c2VjcmV0" in stdin for _args, stdin in calls)


def test_windows_store_falls_back_to_local_machine():
    store = credentials.WindowsCredentialStore.__new__(credentials.WindowsCredentialStore)
    attempts = []
    store._write = lambda name, raw, persist: attempts.append(persist) or persist == 2
    assert store.set("refresh_token", "v")
    assert attempts == [3, 2]


def test_default_store_is_memory_on_linux():
    with patch.object(credentials.sys, "platform", "linux"):
        assert isinstance(credentials.default_store(), credentials.MemoryStore)


def _capture_log(monkeypatch):
    logged = []
    monkeypatch.setattr(credentials, "_log", credentials._log)
    credentials.set_log(logged.append)
    return logged


def test_os_store_construction_failure_is_logged(monkeypatch):
    logged = _capture_log(monkeypatch)
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    monkeypatch.setattr(credentials, "WindowsCredentialStore",
                        MagicMock(side_effect=OSError("Advapi32 introuvable")))
    assert isinstance(credentials.default_store(), credentials.MemoryStore)
    assert len(logged) == 1
    assert "OSError" in logged[0] and "mémoire" in logged[0]


def test_store_failure_is_logged_without_the_value(monkeypatch):
    logged = _capture_log(monkeypatch)

    class _Broken(credentials.MemoryStore):
        def set(self, name, value):
            raise RuntimeError(value)

    credentials.use_store(_Broken())
    assert credentials.set_secret("refresh_token", "SECRET-rt", "s") is False
    assert len(logged) == 1
    assert "set" in logged[0] and "RuntimeError" in logged[0]
    assert "SECRET" not in logged[0]


def test_keychain_calls_are_bounded_in_time():
    timeouts = []

    def _run(args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return MagicMock(returncode=1, stdout="")

    store = credentials.MacKeychainStore()
    with patch.object(subprocess, "run", side_effect=_run):
        store.get("refresh_token")
        store.set("refresh_token", "v")
        store.delete("refresh_token")
    assert len(timeouts) == 4
    assert set(timeouts) == {10}
    credentials.use_store(store)
    with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("security", 10)):
        assert credentials.get_secret("refresh_token", "s") == ""
