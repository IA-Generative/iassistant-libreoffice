"""Le cache de la réponse DM ne garde aucun secret sur disque, sans casser la
reprise du jeton par une nouvelle instance de MainJob."""

import os
import time

from tests.stubs.uno_stubs import install, make_job

install()


def _all_text(root):
    chunks = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as handle:
                chunks.append(handle.read())
    return "\n".join(chunks)


def test_cache_on_disk_has_no_secret(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._persist_config_cache({"config": {"llmToken": "SECRET-1", "embdToken": "SECRET-2",
                                          "telemetryKey": "SECRET-3",
                                          "llm_base_urls": "https://x"}})
    assert "SECRET" not in _all_text(str(tmp_path))


def test_new_instance_still_resolves_the_token(tmp_path):
    expires = int(time.time()) + 3600
    dm = {"config": {"llmToken": "live-token-123", "llm_api_tokens": "live-token-123",
                     "llmTokenExpiresAt": expires}}
    first = make_job(config_dir=str(tmp_path))
    first._persist_bootstrap_config(dm)          # ce que fait _fetch_config
    first._persist_config_cache(dm)
    second = make_job(config_dir=str(tmp_path))
    second.config_cache = None
    second._hydrate_config_cache()
    assert second.get_config("llm_api_tokens", "") == "live-token-123"


def test_hydrated_cache_does_not_blank_the_telemetry_key(tmp_path):
    first = make_job(config_dir=str(tmp_path))
    dm = {"config": {"telemetryKey": "tk-live-123", "llmToken": "tok-live-123"}}
    first._persist_bootstrap_config(dm)          # ce que fait _fetch_config
    first._persist_config_cache(dm)
    second = make_job(config_dir=str(tmp_path))
    second.config_cache = None
    second._hydrate_config_cache()
    assert second.get_config("telemetryKey", None) == "tk-live-123"
