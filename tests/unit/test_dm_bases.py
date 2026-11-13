"""Bases DM : liste de l'OXT, liste annoncée, ordre des candidats, DM actif,
hôtes sans vérification TLS."""

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


def test_same_base_ignores_the_case_of_scheme_and_host_and_the_trailing_slash():
    assert dm_bases.same_base("HTTPS://DM-A.example/", "https://dm-a.example") is True
    assert dm_bases.same_base("https://dm-a.example:8443", "https://dm-a.example") is False


def test_the_announced_list_comes_from_bootstrap_urls_of_the_config_section():
    data = {"config": {"bootstrapUrls": [" https://b/ ", "https://a", "https://B", None, 42]}}
    assert dm_bases.announced_list(data) == ["https://b", "https://a"]


def test_the_announced_list_is_none_when_absent_empty_or_unusable():
    for data in (None, {}, {"config": {}}, {"config": {"bootstrapUrls": []}},
                 {"config": {"bootstrapUrls": ["", "/"]}},
                 {"config": {"bootstrapUrls": "https://a"}}):
        assert dm_bases.announced_list(data) is None


def test_candidates_follow_the_announced_list_then_the_baked_one_then_the_last_winner():
    assert dm_bases.candidate_order(["https://b", "https://a"], ["https://a", "https://c"],
                                    "https://c") == ["https://b", "https://a", "https://c"]


def test_the_last_winner_no_longer_orders_the_list():
    assert dm_bases.candidate_order(None, ["https://a", "https://b"], "https://b") == [
        "https://a", "https://b"]


def test_a_last_winner_outside_both_lists_is_a_trailing_fallback():
    assert dm_bases.candidate_order(["https://c"], ["https://a"], "https://b") == [
        "https://c", "https://a", "https://b"]


def test_without_an_announced_list_the_baked_order_holds():
    assert dm_bases.candidate_order(None, ["https://a", "https://b"], "") == [
        "https://a", "https://b"]


def test_duplicates_fold_on_the_normalized_base_and_keep_the_first_spelling():
    assert dm_bases.candidate_order(["https://A/"], ["https://a", "https://b"], "https://a/") == [
        "https://A/", "https://b"]


def test_unreachable_bases_go_last_in_their_order():
    assert dm_bases.candidate_order(["https://b", "https://a"], ["https://c"], "",
                                    unreachable={"https://b", "https://c"}) == [
        "https://a", "https://b", "https://c"]


def test_the_bases_named_ahead_of_the_winner_are_the_ones_to_try_now():
    assert dm_bases.ahead_of(["https://b", "https://a"], "https://a") == ["https://b"]
    assert dm_bases.ahead_of(["https://a", "https://b"], "https://a") == []
    assert dm_bases.ahead_of(["https://c", "https://b"], "https://a") == ["https://c", "https://b"]
    assert dm_bases.ahead_of(None, "https://a") == []
    assert dm_bases.ahead_of(["https://b", "https://c"], "https://a",
                             unreachable={"https://b"}) == ["https://c"]


def test_marking_reachability_normalizes_the_base_and_reports_a_change():
    seen = set()
    assert dm_bases.mark_reachability(seen, "HTTPS://B.example/", False) is True
    assert seen == {"https://b.example"}
    assert dm_bases.mark_reachability(seen, "https://b.example", False) is False
    assert dm_bases.mark_reachability(seen, "https://b.example/", True) is True
    assert seen == set()


def test_the_active_base_is_the_resolved_then_the_persisted_winner_then_the_first_candidate():
    assert dm_bases.active_base("https://b", "https://a", ["https://a", "https://b"]) == "https://b"
    assert dm_bases.active_base("", "https://b", ["https://c", "https://a", "https://b"]) == "https://b"
    assert dm_bases.active_base("", "https://b", ["https://c"]) == "https://b"
    assert dm_bases.active_base("", "", ["https://c"]) == "https://c"
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
