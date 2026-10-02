"""Perf du fetch de config : ordre de failover (DM gagnant en premier),
timeout configurable, et cache-first (persist/hydrate hors réseau).

Run:  pytest tests/unit/test_config_fetch_perf.py -v
"""
import json
import os
import tempfile
import time
from unittest.mock import MagicMock

from src.mirai import local_config
from tests.stubs.uno_stubs import install, make_job

install()


def _cfg(job, values):
    job._get_config_from_file = MagicMock(side_effect=lambda k, d=None, **kw: values.get(k, d))


# ── Fix 1 : _failover_ordered_urls (essaie le DM gagnant en premier) ──

def test_failover_prefers_in_memory_resolved_url():
    job = make_job()
    _cfg(job, {"bootstrap_urls": ["https://a", "https://b", "https://c"]})
    job._resolved_bootstrap_url = "https://b"
    assert job._failover_ordered_urls() == ["https://b", "https://a", "https://c"]


def test_failover_prefers_persisted_last_url():
    job = make_job()
    _cfg(job, {"bootstrap_urls": ["https://a", "https://b"], "last_bootstrap_url": "https://b"})
    job._resolved_bootstrap_url = ""
    assert job._failover_ordered_urls() == ["https://b", "https://a"]


def test_failover_unchanged_without_hint():
    job = make_job()
    _cfg(job, {"bootstrap_urls": ["https://a", "https://b"]})
    job._resolved_bootstrap_url = ""
    assert job._failover_ordered_urls() == ["https://a", "https://b"]


def test_failover_trailing_slash_tolerant():
    job = make_job()
    _cfg(job, {"bootstrap_urls": ["https://a/", "https://b"]})
    job._resolved_bootstrap_url = "https://a"   # sans slash final
    assert job._failover_ordered_urls() == ["https://a/", "https://b"]


def test_failover_single_url_unchanged():
    job = make_job()
    _cfg(job, {"bootstrap_urls": ["https://only"], "last_bootstrap_url": "https://only"})
    assert job._failover_ordered_urls() == ["https://only"]


# ── Fix 3 : persist + hydrate du cache config ────────────────────────

def test_config_cache_persist_and_hydrate_roundtrip():
    d = tempfile.mkdtemp()
    job = make_job(config_dir=d)
    data = {"meta": {"schema_version": 2}, "config": {"keycloakRealm": "mirai"}}
    job._persist_config_cache(data)
    assert os.path.isfile(os.path.join(d, "mirai", "dm_snapshot.json"))
    # instance fraîche, cache vide → hydrate le recharge
    job2 = make_job(config_dir=d)
    job2.config_cache = None
    job2._hydrate_config_cache()
    assert job2.config_cache == data


def test_config_cache_hydrate_skips_when_stale():
    d = tempfile.mkdtemp()
    job = make_job(config_dir=d)
    local_config.write_json_atomic(
        os.path.join(d, "mirai", "dm_snapshot.json"),
        {"ts": time.time() - (job.config_ttl + 100), "config_data": {"config": {}}})
    job.config_cache = None
    job._hydrate_config_cache()
    assert job.config_cache is None


def test_config_cache_hydrate_noop_when_already_cached():
    d = tempfile.mkdtemp()
    job = make_job(config_dir=d)
    job._persist_config_cache({"config": {"a": 1}})
    job.config_cache = {"config": {"already": "loaded"}}
    job._hydrate_config_cache()   # ne doit pas écraser
    assert job.config_cache == {"config": {"already": "loaded"}}


# ── Fix 2 : timeout de fetch configurable ────────────────────────────

def _mini_response(obj):
    resp = MagicMock()
    resp.read.return_value = json.dumps(obj).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_config_fetch_uses_configurable_timeout():
    job = make_job()
    _cfg(job, {
        "bootstrap_url": "https://dm",
        "config_path": "/c.json",
        "enabled": True,
        "proxy_enabled": False,
        "config_fetch_timeout_seconds": 3,
    })
    for attr in ("_relay_headers",):
        setattr(job, attr, MagicMock(return_value={}))
    job._get_extension_version = MagicMock(return_value="1.0.0")
    job._get_lo_version = MagicMock(return_value="24.8")
    job._ensure_plugin_uuid = MagicMock(return_value="u")
    job._persist_bootstrap_config = MagicMock()
    job._persist_config_cache = MagicMock()
    job.set_config = MagicMock()
    job._urlopen = MagicMock(return_value=_mini_response({"config": {}}))

    job._fetch_config(force=True)

    _, kwargs = job._urlopen.call_args
    assert kwargs.get("timeout") == 3


def test_config_fetch_timeout_defaults_to_4():
    job = make_job()
    _cfg(job, {
        "bootstrap_url": "https://dm",
        "config_path": "/c.json",
        "enabled": True,
        "proxy_enabled": False,
    })
    job._relay_headers = MagicMock(return_value={})
    job._get_extension_version = MagicMock(return_value="1.0.0")
    job._get_lo_version = MagicMock(return_value="24.8")
    job._ensure_plugin_uuid = MagicMock(return_value="u")
    job._persist_bootstrap_config = MagicMock()
    job._persist_config_cache = MagicMock()
    job.set_config = MagicMock()
    job._urlopen = MagicMock(return_value=_mini_response({"config": {}}))

    job._fetch_config(force=True)

    _, kwargs = job._urlopen.call_args
    assert kwargs.get("timeout") == 4
