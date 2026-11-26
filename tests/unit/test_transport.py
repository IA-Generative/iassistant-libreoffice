"""Transport de la coquille : proxy, vérification des certificats, en-têtes de la
paire relais et délai, tels qu'une requête les porte en quittant l'extension."""
import base64
import os
import shutil
import ssl
import urllib.request

import pytest

from src.mirai.core.shell_facade import MainJobShell
from tests.stubs.uno_stubs import make_job, seed_user_config

DM = "https://dm.example"
OTHER_DM = "https://autre-dm.example"
INTERNAL = "https://dm.interne.example"
PROXY = "http://proxy.example:3128"
SYSTEM_PROXY = {"https": "http://systeme.example:8080"}

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUNDLED_CHAIN = os.path.join(
    _REPO_ROOT, "src", "mirai", "CAbundle", "scaleway-bootstrap-ca-chain.pem")
TEST_CA = os.path.join(_REPO_ROOT, "tests", "fixtures", "tls", "test-ca.pem")


def _job(tmp_path, **settings):
    seed_user_config(str(tmp_path), settings)
    return make_job(config_dir=str(tmp_path))


def _shell(tmp_path, **settings):
    return MainJobShell(_job(tmp_path, **settings))


def _get(shell, network, url=DM + "/ressource", timeout=None):
    """La requête GET vers `url`, telle que le réseau la reçoit."""
    network.on("GET", url)
    shell.urlopen(urllib.request.Request(url), timeout=timeout)
    return network.sent[-1]


def _both_schemes(proxy):
    return {"http": proxy, "https": proxy}


def _authenticated_proxy(tmp_path):
    return _shell(tmp_path, proxy_enabled=True, proxy_url=PROXY,
                  proxy_username="alice", proxy_password="mot-de-passe")


def test_without_a_proxy_the_request_goes_direct(tmp_path, network):
    sent = _get(_shell(tmp_path), network)
    assert sent.proxies == []
    assert sent.header("Proxy-Authorization") is None


def test_an_enabled_proxy_serves_http_and_https(tmp_path, network):
    """L'adresse saisie est ramenée à schéma, hôte et port."""
    shell = _shell(tmp_path, proxy_enabled=True, proxy_url="proxy.example:3128/chemin")
    assert _get(shell, network).proxies == [_both_schemes(PROXY)]


def test_proxy_credentials_travel_in_a_basic_header(tmp_path, network):
    """Les identifiants partent d'emblée, et seul un défi Basic du proxy
    recevrait une réponse."""
    sent = _get(_authenticated_proxy(tmp_path), network)
    expected = base64.b64encode(b"alice:mot-de-passe").decode("ascii")
    assert sent.header("Proxy-Authorization") == f"Basic {expected}"
    assert sent.proxy_challenges_answered == {"Basic"}


def test_proxy_credentials_add_a_second_proxy_table(tmp_path, network):
    """L'adresse nue du proxy reste en tête ; celle qui porte les identifiants
    vient après elle."""
    sent = _get(_authenticated_proxy(tmp_path), network)
    assert sent.proxies == [
        _both_schemes(PROXY),
        _both_schemes("http://alice:mot-de-passe@proxy.example:3128")]


def test_a_username_without_password_is_not_sent(tmp_path, network):
    shell = _shell(tmp_path, proxy_enabled=True, proxy_url=PROXY, proxy_username="alice")
    sent = _get(shell, network)
    assert sent.proxies == [_both_schemes(PROXY)]
    assert sent.header("Proxy-Authorization") is None
    assert sent.proxy_challenges_answered == set()


def test_a_disabled_proxy_is_ignored_even_when_fully_configured(tmp_path, network):
    shell = _shell(tmp_path, proxy_enabled=False, proxy_url=PROXY,
                   proxy_username="alice", proxy_password="mot-de-passe")
    sent = _get(shell, network)
    assert sent.proxies == []
    assert sent.header("Proxy-Authorization") is None


def test_an_enabled_proxy_without_address_goes_direct(tmp_path, network):
    shell = _shell(tmp_path, proxy_enabled=True, proxy_url="")
    assert _get(shell, network).proxies == []


def test_without_an_extension_proxy_the_system_proxy_applies(tmp_path, network):
    network.system_proxies = SYSTEM_PROXY
    assert _get(_shell(tmp_path), network).proxies == [SYSTEM_PROXY]


def test_the_extension_proxy_replaces_the_system_proxy(tmp_path, network):
    network.system_proxies = SYSTEM_PROXY
    shell = _shell(tmp_path, proxy_enabled=True, proxy_url=PROXY)
    assert _get(shell, network).proxies == [_both_schemes(PROXY)]


def test_the_direct_attempt_at_the_device_management_uses_the_system_proxy(tmp_path, network):
    """La récupération de la configuration essaie d'abord sans le proxy de
    l'extension : celui du système s'applique quand même."""
    network.system_proxies = SYSTEM_PROXY
    network.on("GET", "/config/config.json", {"config": {"llmToken": "jeton-du-dm"}})
    shell = _shell(tmp_path, enabled=True, bootstrap_urls=[DM],
                   proxy_enabled=True, proxy_url=PROXY)
    shell.recover_llm_auth()
    [fetch] = network.requests("GET", "/config/config.json")
    assert fetch.proxies == [SYSTEM_PROXY]


def test_certificates_are_verified_by_default(tmp_path, network):
    sent = _get(_shell(tmp_path), network)
    assert sent.verifies_certificates
    assert sent.tls.check_hostname is True


def test_a_host_declared_insecure_is_not_verified(tmp_path, network):
    """Seul l'hôte déclaré perd la vérification : les autres la gardent."""
    shell = _shell(tmp_path, bootstrap_insecure_urls=[INTERNAL])
    insecure = _get(shell, network, INTERNAL + "/ressource")
    assert not insecure.verifies_certificates
    assert insecure.tls.check_hostname is False
    assert _get(shell, network, DM + "/ressource").verifies_certificates


def test_the_global_switch_turns_verification_off_for_every_host(tmp_path, network):
    sent = _get(_shell(tmp_path, proxy_allow_insecure_ssl=True), network)
    assert not sent.verifies_certificates
    assert sent.tls.check_hostname is False


@pytest.mark.parametrize("device_management, verified", [(INTERNAL, False), (DM, True)])
def test_without_a_target_the_decision_follows_the_device_management_in_use(
        tmp_path, device_management, verified):
    """Enrôlement, télémétrie et mise à jour héritent de la décision prise pour
    le Device Management, quel que soit l'hôte qu'ils appellent."""
    job = _job(tmp_path, bootstrap_urls=[device_management],
               bootstrap_insecure_urls=[INTERNAL])
    assert (job.get_ssl_context().verify_mode == ssl.CERT_REQUIRED) is verified


def _serials(context):
    return {certificate["serialNumber"] for certificate in context.get_ca_certs()}


def _added_to_system_trust(context):
    return _serials(context) - _serials(ssl.create_default_context())


def _certificates_of(path):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=path)
    return _added_to_system_trust(context)


def test_the_bundled_chain_is_trusted_on_top_of_the_system(tmp_path):
    context = _job(tmp_path).get_ssl_context(DM)
    assert _added_to_system_trust(context) == _certificates_of(BUNDLED_CHAIN)


def test_a_configured_bundle_replaces_the_bundled_chain(tmp_path):
    context = _job(tmp_path, ca_bundle_path=TEST_CA).get_ssl_context(DM)
    assert _added_to_system_trust(context) == _certificates_of(TEST_CA)


def test_a_bundle_named_without_folder_is_found_in_the_profile(tmp_path):
    shutil.copy(TEST_CA, tmp_path / "autorite.pem")
    context = _job(tmp_path, ca_bundle_path="autorite.pem").get_ssl_context(DM)
    assert _added_to_system_trust(context) == _certificates_of(TEST_CA)


def test_a_missing_configured_bundle_falls_back_to_the_bundled_chain(tmp_path):
    job = _job(tmp_path, ca_bundle_path=str(tmp_path / "absent.pem"))
    assert _added_to_system_trust(job.get_ssl_context(DM)) == _certificates_of(BUNDLED_CHAIN)


def _enrolled(tmp_path, issuer=DM, **settings):
    """Un poste enrôlé auprès de `issuer`, dont le Device Management est DM."""
    return _shell(tmp_path, bootstrap_urls=[DM], relay_client_id="poste-1",
                  relay_client_key="cle-relais", relay_dm_base=issuer, **settings)


def _relay_pair(sent):
    return sent.header("X-Relay-Client"), sent.header("X-Relay-Key")


def test_a_relay_request_carries_the_device_pair(tmp_path, network):
    sent = _get(_enrolled(tmp_path), network, DM + "/relay-assistant/telemetry")
    assert _relay_pair(sent) == ("poste-1", "cle-relais")


def test_an_llm_request_never_carries_the_device_pair(tmp_path, network):
    sent = _get(_enrolled(tmp_path), network, DM + "/llm/v1/chat/completions")
    assert _relay_pair(sent) == (None, None)


def test_a_pair_issued_by_another_device_management_is_not_sent(tmp_path, network):
    shell = _enrolled(tmp_path, issuer=OTHER_DM)
    sent = _get(shell, network, DM + "/relay-assistant/telemetry")
    assert _relay_pair(sent) == (None, None)


def test_an_expired_pair_is_still_sent(tmp_path, network):
    """Le serveur reste seul juge de la validité de la paire."""
    shell = _enrolled(tmp_path, relay_key_expires_at=1)
    sent = _get(shell, network, DM + "/relay-assistant/telemetry")
    assert _relay_pair(sent) == ("poste-1", "cle-relais")


def test_the_pair_follows_the_relay_path_whatever_the_host(tmp_path, network):
    """L'émetteur de la paire est comparé au Device Management en usage, pas à
    l'hôte appelé."""
    sent = _get(_enrolled(tmp_path), network, OTHER_DM + "/relay-assistant/telemetry")
    assert _relay_pair(sent) == ("poste-1", "cle-relais")


def test_a_device_without_pair_adds_nothing(tmp_path, network):
    shell = _shell(tmp_path, bootstrap_urls=[DM])
    sent = _get(shell, network, DM + "/relay-assistant/telemetry")
    assert _relay_pair(sent) == (None, None)


def test_the_timeout_asked_for_is_the_one_applied(tmp_path, network):
    assert _get(_shell(tmp_path), network, timeout=12).timeout == 12


def test_without_a_timeout_none_is_imposed(tmp_path, network):
    assert _get(_shell(tmp_path), network).timeout is None


@pytest.mark.parametrize("configured, applied", [
    (None, 45), (120, 120), ("90", 90), (2, 5), ("illisible", 45)])
def test_the_llm_timeout_comes_from_the_settings_with_a_floor(tmp_path, configured, applied):
    settings = {} if configured is None else {"llm_request_timeout_seconds": configured}
    assert _shell(tmp_path, **settings).request_timeout() == applied
