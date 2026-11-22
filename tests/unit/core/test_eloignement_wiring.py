"""Câblage Éloignement : interrupteur, transport du relais, présentation des dossiers."""

from types import SimpleNamespace

import pytest

from src.mirai.core import entry
from src.mirai.core.shell_facade import build_tools_request, feature_from
from src.mirai.ui import eloignement as ui


@pytest.mark.parametrize("features,config,expected", [
    ({"eloignement": True}, None, True),
    ({"eloignement": "true"}, True, False),     # strictement True côté DM
    ({"eloignement": False}, True, False),      # le DM l'emporte
    ({"autre": True}, None, False),             # le DM a répondu, sans la clé
    ({"autre": True}, True, True),              # repli sur la configuration
    ({}, None, None),                           # pas encore de réponse du DM
    (None, "on", True),
])
def test_feature_switch(features, config, expected):
    assert feature_from(features, "eloignement", config) is expected


def test_relay_request_carries_the_pair_and_the_agent_token_only():
    request = build_tools_request(
        "https://dm.exemple/tools/v1/", "post", "eloignement/v1/dossiers/12/actes", b"{}",
        {"Content-Type": "application/json"},
        {"X-Relay-Client": "c", "X-Relay-Key": "k"}, "jeton")
    assert request.full_url == "https://dm.exemple/tools/v1/eloignement/v1/dossiers/12/actes"
    assert request.get_method() == "POST"
    headers = {k.lower(): v for k, v in request.header_items()}
    assert headers["x-user-authorization"] == "Bearer jeton"
    assert headers["x-relay-client"] == "c"
    assert "authorization" not in headers


def test_discovery_url_is_the_endpoint_itself():
    request = build_tools_request("https://dm.exemple/tools/v1", "GET", "", None, {}, {}, "t")
    assert request.full_url == "https://dm.exemple/tools/v1"


@pytest.mark.parametrize("endpoint,error", [
    ("", RuntimeError),
    ("http://dm.exemple/tools/v1", RuntimeError),
])
def test_relay_request_refusals(endpoint, error):
    with pytest.raises(error):
        build_tools_request(endpoint, "GET", "", None, {}, {}, "t")


def test_relay_request_without_session_is_refused():
    with pytest.raises(PermissionError):
        build_tools_request("https://dm.exemple/tools/v1", "GET", "", None, {}, {}, "")


def test_local_relay_may_use_http():
    request = build_tools_request("http://localhost:8089/tools/v1", "GET", "x", None, {}, {}, "t")
    assert request.full_url == "http://localhost:8089/tools/v1/x"


def test_job_event_model_is_read_from_the_environment():
    model = object()
    args = (SimpleNamespace(Name="Config", Value=()),
            SimpleNamespace(Name="Environment", Value=(
                SimpleNamespace(Name="EventName", Value="onLoad"),
                SimpleNamespace(Name="Model", Value=model))))
    assert entry._job_model(args) is model
    assert entry._job_model(()) is None


def test_switch_off_means_no_effect_at_document_load():
    config = {"eloignement_enabled": False}
    job = SimpleNamespace(get_config=lambda key, default=None: config.get(key, default),
                          _log=lambda _m: None)
    model = SimpleNamespace()   # aucune méthode : la moindre lecture échouerait
    args = (SimpleNamespace(Name="Environment",
                            Value=(SimpleNamespace(Name="Model", Value=model),)),)
    assert entry.eloignement_on_load(job, args) is False


def test_command_with_switch_off_does_nothing():
    job = SimpleNamespace(get_config=lambda key, default=None: default, _log=lambda _m: None)
    assert entry.open_eloignement(job, None) is None


DOSSIER = {
    "id": 12, "statut": "actif", "statut_detaille": "en_instruction",
    "mesure_envisagee": "OQTF",
    "identite": {"nom": "Koffi", "prenom": "Marc", "date_naissance": "1992-04-17",
                 "numero_etranger": "0123456789", "nationalite": "ivoirienne"},
}


def test_result_line_matches_the_mail_plugin_wording():
    assert ui.resume_line(DOSSIER) == ("KOFFI Marc · né(e) le 17/04/1992 · n° étranger 0123456789"
                                       " · OQTF · en instruction · dossier n° 12")


def test_header_lets_the_agent_check_it_is_the_right_dossier():
    text = ui.header_text(DOSSIER)
    assert "Dossier n° 12 — en instruction" in text
    assert "Né(e) le 17/04/1992" in text and "Nationalité : ivoirienne" in text
    assert "Mesure envisagée : OQTF" in text


@pytest.mark.parametrize("change,reason", [
    ({"statut_detaille": "archive"}, ui.ARCHIVED),
    ({"statut": "brouillon", "statut_detaille": "brouillon"}, ui.DRAFT),
    ({}, ""),
])
def test_drafts_and_archived_dossiers_cannot_receive_an_act(change, reason):
    assert ui.blocked_reason({**DOSSIER, **change}) == reason


def test_unknown_identity_is_said():
    assert ui.resume_line({"id": 3, "statut": "actif"}).startswith("identité non renseignée")


def test_a_hand_edited_passage_is_shown_but_never_offered_for_replacement():
    labels = {"etranger.prenom": "Prénom", "etranger.nationalite": "Nationalité"}
    by_dossier = {"balise": "etranger.prenom", "bookmark": "m1", "current": "Karim",
                  "new": "Karima", "modified_by_hand": False}
    by_hand = {"balise": "etranger.nationalite", "bookmark": "m2", "current": "J",
               "new": "marocain", "modified_by_hand": True}
    assert ui.change_line(by_dossier, labels) == "Prénom : « Karim » → « Karima »"
    line = ui.change_line(by_hand, labels)
    assert "non remplacé" in line and "« J »" not in line   # l'étendue retouchée n'est pas sûre


def test_the_panel_says_that_ctrl_s_saves_into_the_dossier():
    assert ui.save_hint("13") == "Ctrl+S enregistre dans le dossier n° 13 d'Éloignement."


class _ToolbarManager:
    """Configuration des barres de Writer : une barre enregistrée par nom."""

    def __init__(self, stored_style=None):
        self.saved = {}
        if stored_style is not None:
            self.saved[ui.TOOLBAR] = [[SimpleNamespace(Name="Style", Value=stored_style)]]

    def hasSettings(self, name):
        return name in self.saved

    def createSettings(self):
        return SimpleNamespace(items=[])

    def getSettings(self, name, _writable):
        return SimpleNamespace(getByIndex=lambda i: self.saved[name][i])

    def insertSettings(self, name, settings):
        self.saved[name] = settings.items

    replaceSettings = insertSettings

    def removeSettings(self, name):
        del self.saved[name]

    def store(self):
        pass


def _sync(monkeypatch, manager, enabled=True):
    monkeypatch.setattr(ui, "PropertyValue", lambda: SimpleNamespace(Name="", Value=None))
    monkeypatch.setattr(ui.uno, "Any", lambda _type, value: value, raising=False)
    monkeypatch.setattr(ui.uno, "invoke", lambda obj, _m, args: obj.items.insert(args[0], args[1]),
                        raising=False)
    supplier = SimpleNamespace(getUIConfigurationManager=lambda _module: manager)
    ctx = SimpleNamespace(getServiceManager=lambda: SimpleNamespace(
        createInstanceWithContext=lambda _name, _ctx: supplier))
    shell = SimpleNamespace(uno_ctx=ctx, set_config=lambda *_a: None)
    ui.sync_toolbar(shell, enabled)


def _button(manager):
    return {p.Name: p.Value for p in manager.saved[ui.TOOLBAR][0]}


def test_the_toolbar_button_is_an_icon_with_its_label_as_tooltip(monkeypatch):
    manager = _ToolbarManager()
    _sync(monkeypatch, manager)
    button = _button(manager)
    assert button["CommandURL"] == ui.TOOLBAR_COMMAND
    assert (button["Style"], button["Tooltip"]) == (128, "Éloignement")


def test_a_toolbar_saved_with_another_style_is_rebuilt(monkeypatch):
    manager = _ToolbarManager(stored_style=256 | 32)
    _sync(monkeypatch, manager)
    assert _button(manager)["Style"] == 128


def test_the_toolbar_icon_is_declared_for_the_toolbar_command():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    with open(os.path.join(root, "oxt", "Addons.xcu"), encoding="utf-8") as fh:
        addons = fh.read()
    assert ui.TOOLBAR_COMMAND.replace("&", "&amp;") in addons
    for size in (16, 26):
        assert os.path.isfile(os.path.join(root, "oxt", "assets", f"eloignement{size}.png"))
