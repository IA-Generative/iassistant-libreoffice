"""Liens distribués par le DM : lecture par rôle, contrat appliqué entrée par entrée."""

from src.mirai.core.links import find_link, normalize_links

PORTAL = {
    "id": "search-portal",
    "label": "Chercher aussi dans d'autres sources avec le portail de recherche",
    "url": "https://portail.fake-domain.name/",
    "hint": "en bêta : s'inscrire",
    "hintUrl": "https://communaute.fake-domain.name/invite/abc",
}
FEEDBACK = {"id": "beta-feedback", "label": "Donner mon avis sur la bêta",
            "url": "https://portail.fake-domain.name"}


def test_valid_links_are_kept_in_order_with_their_optional_fields():
    assert normalize_links([PORTAL, FEEDBACK]) == [PORTAL, FEEDBACK]


def test_a_link_is_found_by_its_role_and_absent_roles_give_none():
    assert find_link([PORTAL, FEEDBACK], "beta-feedback") == FEEDBACK
    assert find_link([PORTAL], "beta-feedback") is None
    assert find_link(None, "search-portal") is None
    assert find_link("pas une liste", "search-portal") is None


def test_an_entry_outside_the_contract_is_dropped_alone():
    bad = [
        {"id": "Majuscule", "label": "x", "url": "https://a.fake-domain.name/"},
        {"id": "http-only", "label": "x", "url": "http://a.fake-domain.name/"},
        {"id": "sans-label", "url": "https://a.fake-domain.name/"},
        {"id": "gabarit", "label": "x", "url": "https://a.fake-domain.name/${{VAR}}"},
        {"id": "label-long", "label": "x" * 81, "url": "https://a.fake-domain.name/"},
        "pas un objet",
    ]
    assert normalize_links(bad + [FEEDBACK]) == [FEEDBACK]


def test_a_bad_hint_url_is_dropped_but_the_link_and_its_hint_stay():
    link = find_link([dict(PORTAL, hintUrl="javascript:alert(1)")], "search-portal")
    assert link["hint"] == PORTAL["hint"]
    assert "hintUrl" not in link


def test_duplicate_roles_keep_the_first_entry():
    other = dict(FEEDBACK, url="https://b.fake-domain.name/")
    assert normalize_links([FEEDBACK, other]) == [FEEDBACK]
