"""Client des routes /v1 d'Éloignement : chemins, erreurs lisibles, contrôle de version."""

import json

import pytest

from src.mirai.core.eloignement import api
from tests.stubs.fake_eloignement import ScriptedRelay


def client(*responses):
    relay = ScriptedRelay(*responses)
    return api.Client(relay), relay


def test_paths_go_through_the_eloignement_service_of_the_relay():
    c, relay = client((200, {}, {"items": [{"id": "m1", "nom": "OQTF"}]}))
    assert c.modeles("oqtf") == [{"id": "m1", "nom": "OQTF"}]
    assert relay.calls[0]["method"] == "GET"
    assert relay.calls[0]["path"] == "eloignement/v1/modeles?q=oqtf"


def test_search_sends_the_single_field_as_is():
    c, relay = client((200, {}, {"items": []}))
    c.rechercher("KOFFI Marc 17/04/1992")
    assert relay.calls[0]["path"] == "eloignement/v1/dossiers?q=KOFFI+Marc+17%2F04%2F1992&limit=20"


@pytest.mark.parametrize("bad", ["../admin", "12/../34", "a b", "", "%2e%2e", "x" * 80, None])
def test_an_identifier_from_a_received_file_never_reaches_the_path(bad):
    c, relay = client()
    with pytest.raises(ValueError):
        c.dossier(bad)
    assert relay.calls == []


def test_problem_detail_is_shown_first_with_its_field_errors():
    body = {"title": "Requête invalide", "detail": "Identité incomplète",
            "erreurs": [{"champ": "nom", "message": "nom requis"}]}
    c, _ = client((422, {}, body))
    with pytest.raises(api.EloignementError) as err:
        c.dossier("12")
    assert err.value.message == "Identité incomplète (nom requis) (HTTP 422)"
    assert not err.value.transient


def test_relay_refusal_and_unknown_body_fall_back_to_the_mail_plugin_wording():
    c, _ = client((429, {"Retry-After": "7"}, b""))
    with pytest.raises(api.EloignementError) as err:
        c.dossier("12")
    assert err.value.message == "trop de demandes : réessayer dans une minute"
    assert err.value.retry_after == 7
    assert err.value.transient


def test_network_failure_becomes_unreachable_service():
    c, _ = client(OSError("timed out"))
    with pytest.raises(api.EloignementError) as err:
        c.modeles()
    assert err.value.status == 0
    assert err.value.message == "service injoignable"
    assert err.value.transient


def test_generation_is_normalized_from_the_frozen_contract():
    c, relay = client((201, {"ETag": '"3"'}, {
        "acte_id": "a-1", "nom": "12_oqtf_1.odt", "version": 1,
        "modele": {"id": "m1", "nom": "OQTF"},
        "balises_vides": [{"nom": "etranger.alias", "libelle": "Alias", "valeur": None}]}))
    acte = c.generer("12", "m1")
    assert acte == {"id": "a-1", "nom": "12_oqtf_1.odt", "version": "1",
                    "balises_vides": [{"nom": "etranger.alias", "libelle": "Alias"}],
                    "modele": {"id": "m1", "nom": "OQTF"}}
    call = relay.calls[0]
    assert (call["method"], call["path"]) == ("POST", "eloignement/v1/dossiers/12/actes")
    assert json.loads(call["body"]) == {"modele_id": "m1"}
    assert call["headers"]["Idempotency-Key"].startswith("acte:12:m1:")


def test_download_returns_bytes_and_version_whatever_the_header_case():
    c, relay = client((200, {"etag": 'W/"5"'}, b"PK\x03\x04"))
    data, version = c.contenu("12", "a-1")
    assert data == b"PK\x03\x04"
    assert version == "5"
    assert relay.calls[0]["path"] == "eloignement/v1/dossiers/12/actes/a-1/contenu"


def test_new_version_is_posted_with_if_match_and_the_file():
    c, relay = client((201, {}, {"acte_id": "a-1", "version": 4, "nom": "12_oqtf_1.odt"}))
    result = c.verser("12", "a-1", b"PK-odt", "3", "12_oqtf_1.odt")
    assert result["version"] == "4"
    call = relay.calls[0]
    assert (call["method"], call["path"]) == ("POST", "eloignement/v1/dossiers/12/actes/a-1/versions")
    assert call["headers"]["If-Match"] == '"3"'
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="fichier"; filename="12_oqtf_1.odt"' in call["body"]
    assert b"PK-odt" in call["body"]


def test_a_new_version_without_reference_version_is_refused_before_any_call():
    c, relay = client()
    with pytest.raises(api.EloignementError) as err:
        c.verser("12", "a-1", b"x", "", "n.odt")
    assert err.value.status == 428
    assert relay.calls == []


def test_conflict_keeps_the_current_version_announced_by_eloignement():
    c, _ = client((412, {}, {"title": "Précondition", "detail": "version périmée",
                             "version": 7}))
    with pytest.raises(api.EloignementError) as err:
        c.verser("12", "a-1", b"x", "3")
    assert err.value.status == 412
    assert api.server_version(err.value) == "7"


def test_tag_values_and_labels_come_from_eloignement_only():
    c, relay = client((200, {}, {"items": [
        {"nom": "etranger.nom", "libelle": "Nom", "valeur": "KOFFI"},
        {"nom": "etranger.alias", "libelle": "", "valeur": None}]}))
    valeurs, libelles = c.balises("12", "m1")
    assert valeurs == {"etranger.nom": "KOFFI", "etranger.alias": None}
    assert libelles == {"etranger.nom": "Nom", "etranger.alias": "etranger.alias"}
    assert relay.calls[0]["path"] == "eloignement/v1/dossiers/12/balises?modele_id=m1"


@pytest.mark.parametrize("discovery,expected", [
    ({"services": ["eloignement"]}, True),
    ({"passthrough": [{"service": "eloignement", "protocol": "passthrough"}]}, True),
    ({"services": ["ocr"], "tools": []}, False),
    ([], False),
])
def test_discovery(discovery, expected):
    assert api.announces(discovery) is expected
