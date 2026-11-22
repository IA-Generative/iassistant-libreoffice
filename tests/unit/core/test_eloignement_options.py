"""Options de génération : décrites par Éloignement, posées là où il les attend."""

import json

import pytest

from src.mirai.core.eloignement import api, options
from tests.stubs.fake_eloignement import ScriptedRelay

OQTF_3 = {
    "id": "m-oqtf3", "nom": "OQTF 611-1 3°", "chemin": "OQTF", "version": "4",
    "natures": ["OQTF"],
    "options_requises": [
        {"option": "voluntary_departure_delay", "champ": "oqtf.delai_depart_volontaire_jours",
         "libelle": "Délai de départ volontaire", "type": "choix",
         "valeurs": [{"valeur": 0, "libelle": "Sans délai"}, {"valeur": 30, "libelle": "30 jours"}]},
        {"option": "return_ban_duration", "champ": "oqtf.duree_interdiction_retour_annees",
         "libelle": "Durée de l'interdiction de retour (années)", "type": "entier"},
    ],
}


def test_a_simple_letter_requires_nothing():
    assert options.required({"id": "c", "nom": "Courrier d'essai IAssistant", "natures": [],
                             "options_requises": []}) == []
    assert options.required({"id": "c", "nom": "Ancien contrat"}) == []


def test_answers_are_placed_where_eloignement_expects_them():
    wanted = options.required(OQTF_3)
    body = options.build(wanted, {"voluntary_departure_delay": 0, "return_ban_duration": " 2 "})
    assert body == {"oqtf": {"delai_depart_volontaire_jours": 0,
                             "duree_interdiction_retour_annees": 2}}


def test_without_labels_or_types_the_code_and_a_free_field_are_shown():
    [option] = options.required({"options_requises": [
        {"option": "cra_location", "champ": "placement_in_cra.cra_location"}]})
    assert (option.libelle, option.choices()) == ("cra_location", [])
    assert options.build([option], {"cra_location": "CRA de Vincennes"}) == {
        "placement_in_cra": {"cra_location": "CRA de Vincennes"}}


def test_an_untyped_numeric_answer_is_sent_as_a_number_a_typed_text_stays_text():
    untyped, text = options.required({"options_requises": [
        {"option": "voluntary_departure_delay", "champ": "oqtf.delai_depart_volontaire_jours"},
        {"option": "measure_number", "champ": "numero", "type": "texte"}]})
    assert options.build([untyped, text], {"voluntary_departure_delay": "0",
                                           "measure_number": "0042"}) == {
        "oqtf": {"delai_depart_volontaire_jours": 0}, "numero": "0042"}


def test_a_boolean_is_asked_as_yes_or_no():
    [option] = options.required({"options_requises": [
        {"option": "after_cra_release", "champ": "house_arrest_order.aar_suite_a_un_placement_en_cra",
         "libelle": "Fait suite à un placement en CRA", "type": "booleen"}]})
    assert [c["libelle"] for c in option.choices()] == ["Oui", "Non"]
    assert options.build([option], {"after_cra_release": False}) == {
        "house_arrest_order": {"aar_suite_a_un_placement_en_cra": False}}


@pytest.mark.parametrize("answers,message", [
    ({"voluntary_departure_delay": 0}, "à renseigner"),
    ({"voluntary_departure_delay": 0, "return_ban_duration": ""}, "à renseigner"),
    ({"voluntary_departure_delay": 0, "return_ban_duration": "deux"}, "nombre entier"),
])
def test_a_missing_or_invalid_answer_is_said_in_french(answers, message):
    with pytest.raises(ValueError, match=message):
        options.build(options.required(OQTF_3), answers)


@pytest.mark.parametrize("champ,kept", [
    ("oqtf.regime", True), ("oqtf", True),
    ("", False), ("../x", False), ("a.b.c.d", False), ("Oqtf.Delai", False), ("oqtf..x", False),
])
def test_an_option_whose_place_is_malformed_is_ignored(champ, kept):
    found = options.required({"options_requises": [{"option": "x", "champ": champ}]})
    assert bool(found) is kept


@pytest.mark.parametrize("body,ok", [
    ({"oqtf": {"delai_depart_volontaire_jours": 0}}, True),
    ({"oqtf": {"regime": "oqtf_611_1_3"}}, True),
    ({"oqtf": {"x": [1, 2]}}, False),
    ({"Oqtf": {}}, False),
    ("oqtf", False),
])
def test_only_nested_objects_with_plain_values_are_sent(body, ok):
    assert options.valid_body(body) is ok


def test_the_options_travel_in_the_generation_request():
    relay = ScriptedRelay((201, {"ETag": '"1"'}, {"acte_id": "a-1", "nom": "x.odt", "version": 1,
                                                  "balises_vides": []}))
    api.Client(relay).generer("13", "m-oqtf3", {"oqtf": {"delai_depart_volontaire_jours": 0}})
    sent = json.loads(relay.calls[0]["body"])
    assert sent == {"modele_id": "m-oqtf3",
                    "options": {"oqtf": {"delai_depart_volontaire_jours": 0}}}


def test_the_options_of_an_oqtf_as_eloignement_describes_them():
    """Forme rendue par /v1/modeles : libellés et choix du front d'Éloignement, sans défaut."""
    modele = {"id": "m", "nom": "OQTF 611-1 3°", "options_requises": [
        {"option": "voluntary_departure_delay", "champ": "oqtf.delai_depart_volontaire_jours",
         "libelle": "Délai de départ volontaire", "type": "choix",
         "valeurs": [{"valeur": 0, "libelle": "Refusé"}, {"valeur": 30, "libelle": "30 jours"}]},
        {"option": "after_cra_release", "champ": "house_arrest_order.aar_suite_a_un_placement_en_cra",
         "libelle": "Suite à un placement en CRA", "type": "booleen", "valeurs": []},
        {"option": "cra_location", "champ": "placement_in_cra.cra_location",
         "libelle": "Lieu du CRA", "type": "texte", "valeurs": []},
    ]}
    delay, after_cra, place = options.required(modele)
    assert [c["libelle"] for c in delay.choices()] == ["Refusé", "30 jours"]
    assert delay.defaut is None                      # l'agent choisit, rien n'est présélectionné
    assert [c["libelle"] for c in after_cra.choices()] == ["Oui", "Non"]
    assert place.choices() == []
    assert options.build([delay, after_cra, place], {
        "voluntary_departure_delay": 0, "after_cra_release": True, "cra_location": "Vincennes"}) == {
        "oqtf": {"delai_depart_volontaire_jours": 0},
        "house_arrest_order": {"aar_suite_a_un_placement_en_cra": True},
        "placement_in_cra": {"cra_location": "Vincennes"}}
