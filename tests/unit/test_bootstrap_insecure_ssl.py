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
SCW = "https://bootstrap.fake-domain.name"


def _job_with_config(values):
    """make_job() with `_get_config_from_file` backed by a dict."""
    job = make_job()
    job._get_config_from_file = MagicMock(
        side_effect=lambda k, d=None, **kw: values.get(k, d)
    )
    return job


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
