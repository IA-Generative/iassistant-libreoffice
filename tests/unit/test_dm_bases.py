"""Bases DM : liste de l'OXT, DM actif, hôtes sans vérification TLS."""

from src.mirai.shell import dm_bases

OCP = "https://bootstrap.apps.ocp.internal.example"
DGX = "https://gpu.internal.example/bootstrap"
SCW = "https://bootstrap.fake-domain.name"


def test_the_baked_list_drops_blanks_and_duplicates():
    assert dm_bases.baked_list(["https://a", "", " https://b ", "https://a"], "") == [
        "https://a", "https://b"]


def test_the_baked_list_falls_back_to_the_single_legacy_url():
    assert dm_bases.baked_list(None, " https://only ") == ["https://only"]
    assert dm_bases.baked_list([], "") == []


def test_the_active_base_is_the_resolved_one_then_the_persisted_one_still_listed():
    assert dm_bases.active_base("https://b", "https://a", ["https://a", "https://b"]) == "https://b"
    assert dm_bases.active_base("", "https://b", ["https://a", "https://b/"]) == "https://b"
    assert dm_bases.active_base("", "https://old", ["https://new"]) == "https://new"
    assert dm_bases.active_base("", "", []) == ""


def test_an_insecure_host_matches_its_base_and_any_url_under_it():
    assert dm_bases.is_insecure_url(OCP, [OCP]) is True
    assert dm_bases.is_insecure_url(OCP + "/config/x?profile=prod", [OCP]) is True


def test_other_hosts_stay_verified():
    assert dm_bases.is_insecure_url(DGX, [OCP]) is False
    assert dm_bases.is_insecure_url(SCW, [OCP]) is False


def test_no_list_means_never_insecure():
    assert dm_bases.is_insecure_url(OCP, None) is False
    assert dm_bases.is_insecure_url("", [OCP]) is False


def test_a_bare_host_entry_is_tolerated():
    assert dm_bases.is_insecure_url(OCP, ["bootstrap.apps.ocp.internal.example"]) is True
