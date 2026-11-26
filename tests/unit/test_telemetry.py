"""Télémétrie de la coquille : la requête envoyée au collecteur, ce qui la retient
et les spans que la coquille émet d'elle-même."""
import json
import logging
import re
import threading
import time
import urllib.error
import webbrowser

import pytest

from src.mirai import local_config
from src.mirai.core.shell_facade import MainJobShell
from src.mirai.entrypoint import get_user_agent
from tests.stubs.helpers import otlp_values, spans_sent
from tests.stubs.uno_stubs import make_job, read_user_config, seed_user_config

COLLECTOR = "https://collecteur.example/v1/traces"
DM = "https://dm.example"
LLM = "https://ia.example/v1"


def _job(tmp_path, **settings):
    seed_user_config(str(tmp_path), {"telemetryEndpoint": COLLECTOR, **settings})
    return make_job(config_dir=str(tmp_path))


def _device(tmp_path):
    return read_user_config(str(tmp_path))["extensionUUID"]


def _posts(network):
    network.settle()
    return network.requests("POST", COLLECTOR)


def _emit(tmp_path, network, attributes=None, name="Exemple", **settings):
    """La requête qui porte un span émis par le moteur."""
    network.on("POST", COLLECTOR)
    MainJobShell(_job(tmp_path, **settings)).telemetry(name, attributes)
    [post] = _posts(network)
    return post


def _emit_with_device_management(tmp_path, network, **served):
    """La même requête, sur un poste qui vient de recevoir `served` du Device
    Management."""
    network.on("GET", "/config/config.json", {"config": {"llmToken": "jeton-du-dm", **served}})
    network.on("POST", COLLECTOR)
    shell = MainJobShell(_job(tmp_path, enabled=True, bootstrap_urls=[DM]))
    shell.recover_llm_auth()
    shell.telemetry("Exemple")
    [post] = _posts(network)
    return post


def _own(attributes):
    """Les attributs d'un span sans ceux que tout span porte sur l'extension."""
    return {key: value for key, value in attributes.items()
            if key not in ("extension.uuid", "extension.name", "extension.version")}


def _spans(network):
    return [(name, _own(attributes)) for name, attributes in spans_sent(network, COLLECTOR)]


@pytest.fixture
def browser(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


def test_a_span_is_posted_to_the_collector_off_the_main_thread(tmp_path, network):
    post = _emit(tmp_path, network)
    assert (post.method, post.url, post.timeout) == ("POST", COLLECTOR, 5)
    assert not post.on_main_thread


def test_a_span_emitted_from_a_worker_thread_reaches_the_collector(tmp_path, network):
    """Le moteur émet depuis le thread de son run, une fois son travail fait."""
    network.on("POST", COLLECTOR)
    shell = MainJobShell(_job(tmp_path))

    def work_then_emit():
        time.sleep(0.05)
        shell.telemetry("Exemple")

    threading.Thread(target=work_then_emit).start()
    assert [name for name, _ in _spans(network)] == ["Exemple"]


def test_the_request_names_the_extension_and_the_device(tmp_path, network):
    post = _emit(tmp_path, network)
    assert post.header("Content-Type") == "application/json"
    assert post.header("User-Agent") == get_user_agent()
    assert post.header("X-Client-UUID") == _device(tmp_path)


@pytest.mark.parametrize("served, authorization", [
    ({"telemetryKey": "cle"}, "Basic cle"),
    ({"telemetryKey": "cle", "telemetryAuthorizationType": "Bearer"}, "Bearer cle"),
    ({"telemetryKey": "cle", "telemetryAuthorizationType": "basic"}, None),
    ({"telemetryKey": "cle", "telemetryAuthorizationType": "Digest"}, None),
    ({}, None),
])
def test_the_key_of_the_device_management_is_presented_as_basic_or_bearer_only(
        tmp_path, network, served, authorization):
    """Sans clé, ou avec un type autre que « Basic » et « Bearer » à la casse
    près, le span part sans autorisation."""
    post = _emit_with_device_management(tmp_path, network, **served)
    assert post.header("Authorization") == authorization


def test_a_key_left_in_the_user_settings_is_not_presented(tmp_path, network):
    """Les secrets quittent les réglages de l'utilisateur au démarrage : la clé
    de télémétrie n'y est pas relue."""
    assert _emit(tmp_path, network, telemetryKey="cle-locale").header("Authorization") is None


def test_a_key_packaged_with_the_extension_is_presented(tmp_path, network, monkeypatch):
    """Un profil hors ligne embarque ses réglages dans l'OXT, clé comprise."""
    package = tmp_path / "oxt" / "config.default.json"
    package.parent.mkdir()
    package.write_text(json.dumps({"enabled": False, "telemetryKey": "cle-du-paquet"}),
                       encoding="utf-8")
    monkeypatch.setattr(
        local_config, "package_config_candidates", lambda module_dir: [str(package)])
    assert _emit(tmp_path, network).header("Authorization") == "Basic cle-du-paquet"


def test_the_body_is_one_otlp_span_always_successful_and_one_millisecond_long(tmp_path, network):
    [resource_spans] = _emit(tmp_path, network).json()["resourceSpans"]
    assert otlp_values(resource_spans["resource"]["attributes"]) == {
        "service.name": "mirai-libreoffice", "service.version": "1.0.0",
        "extension.uuid": _device(tmp_path)}
    [scope_spans] = resource_spans["scopeSpans"]
    assert scope_spans["scope"] == {"name": "mirai-extension", "version": "1.0.0"}
    [span] = scope_spans["spans"]
    assert (span["name"], span["kind"], span["status"]) == ("Exemple", 1, {"code": 1})
    assert re.fullmatch(r"[0-9a-f]{32}", span["traceId"])
    assert re.fullmatch(r"[0-9a-f]{16}", span["spanId"])
    assert isinstance(span["startTimeUnixNano"], str)
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 1_000_000


def test_each_span_opens_its_own_trace(tmp_path, network):
    network.on("POST", COLLECTOR)
    shell = MainJobShell(_job(tmp_path))
    shell.telemetry("Exemple")
    shell.telemetry("Exemple")
    traces = {post.json()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]
              for post in _posts(network)}
    assert len(traces) == 2


def test_a_span_names_the_extension_then_carries_the_callers_attributes(tmp_path, network):
    _emit(tmp_path, network, {"compte": 3, "abouti": True})
    [(name, attributes)] = spans_sent(network, COLLECTOR)
    assert name == "Exemple"
    assert attributes == {
        "extension.uuid": _device(tmp_path), "extension.name": "mirai",
        "extension.version": "1.0.0", "compte": 3, "abouti": True,
        "plugin.action": "Exemple", "trigger.source": "auto"}


def test_the_caller_may_replace_an_attribute_naming_the_extension(tmp_path, network):
    _emit(tmp_path, network, {"extension.version": "9.9"})
    [(_, attributes)] = spans_sent(network, COLLECTOR)
    assert attributes["extension.version"] == "9.9"


def test_a_span_the_shell_knows_gets_its_short_action_name(tmp_path, network):
    _emit(tmp_path, network, name="SummarizeSelection")
    assert _spans(network) == [
        ("SummarizeSelection", {"plugin.action": "summarize", "trigger.source": "auto"})]


def test_the_caller_keeps_the_action_and_the_source_it_gives(tmp_path, network):
    given = {"plugin.action": "correct", "trigger.source": "palette"}
    _emit(tmp_path, network, dict(given), name="SummarizeSelection")
    assert _spans(network) == [("SummarizeSelection", given)]


@pytest.mark.parametrize("command, source", [
    ("Documentation&src=menu", "menu"), ("Documentation&src=key", "key"),
    ("Documentation", "user")])
def test_a_span_says_where_the_action_came_from(tmp_path, network, browser, command, source):
    network.on("POST", COLLECTOR)
    _job(tmp_path, doc_url="https://doc.example").trigger(command)
    [(_, attributes)] = _spans(network)
    assert attributes["trigger.source"] == source


@pytest.mark.parametrize("action, span, attributes", [
    ("settings", "OpenSettings", {"action": "open_settings", "plugin.action": "settings"}),
    ("AboutDialog", "AboutDialog", {"action": "about", "plugin.action": "about"}),
    ("Documentation", "OpenDocumentation",
     {"action": "open_documentation", "plugin.action": "docs"}),
])
def test_a_shell_action_is_reported(tmp_path, network, browser, action, span, attributes):
    network.on("POST", COLLECTOR)
    _job(tmp_path, doc_url="https://doc.example").trigger(action)
    assert _spans(network) == [(span, {**attributes, "trigger.source": "user"})]


def test_loading_the_extension_is_announced(tmp_path, network):
    network.on("POST", COLLECTOR)
    seed_user_config(str(tmp_path), {"telemetryEndpoint": COLLECTOR})
    make_job(config_dir=str(tmp_path), announce_load=True)
    assert _spans(network) == [("ExtensionLoaded", {
        "event.type": "extension_loaded", "extension.context": "libreoffice_writer",
        "plugin.action": "launch", "trigger.source": "auto"})]


def test_each_new_shell_announces_the_load_again(tmp_path, network):
    """LibreOffice construit une coquille par action."""
    network.on("POST", COLLECTOR)
    seed_user_config(str(tmp_path), {"telemetryEndpoint": COLLECTOR})
    make_job(config_dir=str(tmp_path), announce_load=True)
    make_job(config_dir=str(tmp_path), announce_load=True)
    assert [name for name, _ in _spans(network)] == ["ExtensionLoaded", "ExtensionLoaded"]


def test_a_first_enrollment_that_cannot_log_in_is_reported_as_canceled(tmp_path, network):
    """Ici la connexion échoue faute de réglages Keycloak : le span ne distingue
    pas ce cas d'un abandon par l'utilisateur."""
    network.on("GET", "/config/config.json", {"config": {}})
    network.on("POST", COLLECTOR)
    _job(tmp_path, enabled=True, bootstrap_urls=[DM]).trigger("OpenAssistant")
    assert _spans(network) == [("EnrollFailed", {
        "status": "canceled", "plugin.action": "enroll.fail", "trigger.source": "user"})]


def test_an_interrupted_model_test_is_reported_as_a_model_without_tools(tmp_path, network):
    unreachable = urllib.error.URLError("hôte injoignable")
    network.on("GET", LLM, error=unreachable)
    network.on("POST", LLM, error=unreachable)
    network.on("POST", COLLECTOR)
    _job(tmp_path, llm_base_urls=LLM).trigger("TestModel")
    assert dict(_spans(network))["AssistantModelProbe"] == {
        "assistant.accepts_tools": "false", "assistant.calls_tool": "false",
        "assistant.chains": "false", "plugin.action": "assistant.probe",
        "trigger.source": "user"}


@pytest.mark.parametrize("enabled, sent", [(True, 1), (False, 0), ("false", 1)])
def test_only_a_false_setting_holds_the_span_back(tmp_path, network, enabled, sent):
    """Le texte « false » n'est pas faux : il laisse partir le span."""
    network.on("POST", COLLECTOR)
    MainJobShell(_job(tmp_path, telemetryEnabled=enabled)).telemetry("Exemple")
    assert len(_posts(network)) == sent


def test_a_device_without_identifier_gets_one_when_it_sends(tmp_path, network):
    network.on("POST", COLLECTOR)
    job = _job(tmp_path)
    job.set_config("extensionUUID", "")
    MainJobShell(job).telemetry("Exemple")
    [post] = _posts(network)
    assert _device(tmp_path)
    assert post.header("X-Client-UUID") == _device(tmp_path)


def test_a_refusal_from_the_collector_is_logged_without_its_answer(tmp_path, network, caplog):
    network.on("POST", COLLECTOR, "détail renvoyé par le collecteur", status=401)
    with caplog.at_level(logging.INFO):
        MainJobShell(_job(tmp_path)).telemetry("Exemple")
        posts = _posts(network)
    assert len(posts) == 1
    assert "Status: 401" in caplog.text
    assert "détail renvoyé par le collecteur" not in caplog.text


def test_an_unreachable_collector_is_tried_once_and_breaks_nothing(tmp_path, network, caplog):
    network.on("POST", COLLECTOR, error=urllib.error.URLError("hôte injoignable"))
    with caplog.at_level(logging.INFO):
        MainJobShell(_job(tmp_path)).telemetry("Exemple")
        posts = _posts(network)
    assert len(posts) == 1
    assert "hôte injoignable" in caplog.text


def test_the_detailed_log_never_shows_the_key(tmp_path, network, caplog):
    with caplog.at_level(logging.INFO):
        _emit_with_device_management(
            tmp_path, network, telemetryKey="cle-a-taire", telemetrylogJson=True)
    assert "Authorization: <redacted>" in caplog.text
    assert "cle-a-taire" not in caplog.text
