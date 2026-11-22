"""Actualiser un acte généré : l'ancienne valeur est retrouvée dans le texte, jamais
dans un mot plus long, et une valeur retouchée à la main n'est pas remplacée."""

import re

import pytest

from src.mirai.core.eloignement import snapshot
from tests.stubs.fake_eloignement import FakeLinkedDoc


def _python(pattern):
    """La même expression pour `re` : \\p{L}\\p{N}_ y devient \\w."""
    return pattern.replace(r"\p{L}\p{N}_", r"\w")


@pytest.mark.parametrize("text,found", [
    ("Prénom : Karim", ["Karim"]),
    ("Karim, né le", ["Karim"]),
    ("Karimou", []),
    ("Abdel-Karim", []),
    ("l'Karim", []),
    ("Karim_2", []),
    ("Karim Karim", ["Karim", "Karim"]),
])
def test_a_value_counts_only_where_nothing_is_stuck_to_it(text, found):
    assert re.findall(_python(snapshot.bounded_pattern("Karim")), text) == found


def test_regex_characters_of_a_value_are_taken_literally():
    pattern = _python(snapshot.bounded_pattern("01/01/1990 (Ville-Fictive)"))
    assert re.search(pattern, "Né le 01/01/1990 (Ville-Fictive).")
    assert not re.search(pattern, "Né le 01x01x1990 (Ville-Fictive).")


def test_the_snapshot_keeps_the_inserted_text_of_valued_balises_only():
    assert snapshot.as_snapshot({"etranger.prenom": "Karim", "etranger.alias": None,
                                 "etranger.date_naissance": "01/01/1990", "mauvais nom": "x"}) == {
        "etranger.prenom": "Karim", "etranger.date_naissance": "01/01/1990"}


def test_only_changed_values_are_proposed_and_a_vanished_one_is_flagged():
    snap = {"etranger.prenom": "Karim", "etranger.nom": "EXEMPLE-FICTIF",
            "etranger.lieu_naissance": "Ville-Fictive"}
    values = {"etranger.prenom": "Karima", "etranger.nom": "EXEMPLE-FICTIF",
              "etranger.lieu_naissance": "Autre-Ville"}
    plan = snapshot.text_plan(snap, values, {"etranger.prenom": 1, "etranger.lieu_naissance": 0})
    by_balise = {c.balise: c for c in plan}
    assert set(by_balise) == {"etranger.prenom", "etranger.lieu_naissance"}
    assert (by_balise["etranger.prenom"].current, by_balise["etranger.prenom"].new) == (
        "Karim", "Karima")
    assert by_balise["etranger.prenom"].modified_by_hand is False
    assert by_balise["etranger.lieu_naissance"].modified_by_hand is True   # retouchée à la main
    assert all(c.bookmark == "" for c in plan)


def test_the_snapshot_lives_in_the_document_properties():
    doc = FakeLinkedDoc()
    assert snapshot.read_snapshot(doc) == {}
    snapshot.write_snapshot(doc, {"etranger.prenom": "Karim"})
    snapshot.write_snapshot(doc, {"etranger.prenom": "Karima"})
    assert snapshot.read_snapshot(doc) == {"etranger.prenom": "Karima"}
