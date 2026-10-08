"""
Per-URL insecure SSL (`-k`) for bootstrap failover.

A bootstrap URL whose host is declared in `bootstrap_insecure_urls` skips cert
verification (e.g. an internal OCP route behind a private CA), while the public
DMs (Scaleway, DGX) stay verified. Covers the prod profile that mixes
verified + unverified bootstrap endpoints.

Run with:
    pytest tests/unit/test_bootstrap_insecure_ssl.py -v --tb=short
"""
import ssl
from unittest.mock import MagicMock

# Stubs must be installed before importing entrypoint
from tests.stubs.uno_stubs import install, make_job

install()


OCP = "https://bootstrap.apps.ocp.internal.example"
DGX = "https://gpu.internal.example/bootstrap"
SCW = "https://bootstrap.fake-domain.name"


def _job_with_config(values):
    """make_job() with `_get_config_from_file` backed by a dict."""
    job = make_job()
    job._get_config_from_file = MagicMock(
        side_effect=lambda k, d=None, **kw: values.get(k, d)
    )
    return job


def test_insecure_match_base_and_full_url():
    job = _job_with_config({"bootstrap_insecure_urls": [OCP]})
    assert job._is_insecure_bootstrap_url(OCP) is True
    # The active URL is a base; config fetch passes base + path — both must match.
    assert job._is_insecure_bootstrap_url(OCP + "/config/x?profile=prod") is True


def test_verified_hosts_not_matched():
    job = _job_with_config({"bootstrap_insecure_urls": [OCP]})
    assert job._is_insecure_bootstrap_url(DGX) is False
    assert job._is_insecure_bootstrap_url(SCW) is False


def test_no_list_means_never_insecure():
    job = _job_with_config({})
    assert job._is_insecure_bootstrap_url(OCP) is False


def test_bare_host_entry_tolerated():
    # Entry written without a scheme still matches by host.
    job = _job_with_config(
        {"bootstrap_insecure_urls": ["bootstrap.apps.ocp.internal.example"]}
    )
    assert job._is_insecure_bootstrap_url(OCP) is True


def test_context_insecure_for_ocp():
    job = _job_with_config(
        {"proxy_allow_insecure_ssl": False, "bootstrap_insecure_urls": [OCP]}
    )
    ctx = job.get_ssl_context(OCP)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_context_verified_for_public_url():
    job = _job_with_config(
        {"proxy_allow_insecure_ssl": False, "bootstrap_insecure_urls": [OCP]}
    )
    ctx = job.get_ssl_context(SCW)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_context_defaults_to_active_bootstrap_url():
    # No explicit target → falls back to the resolved (failover-winner) URL,
    # so enroll/telemetry/update inherit the per-URL decision.
    job = _job_with_config(
        {"proxy_allow_insecure_ssl": False, "bootstrap_insecure_urls": [OCP]}
    )
    job._active_bootstrap_url = MagicMock(return_value=OCP)
    ctx = job.get_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE


def test_global_flag_forces_insecure_everywhere():
    job = _job_with_config({"proxy_allow_insecure_ssl": True})
    ctx = job.get_ssl_context(SCW)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
