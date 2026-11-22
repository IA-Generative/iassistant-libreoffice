"""Renouvellement du jeton SSO : quand plusieurs fils trouvent le jeton d'accès
expiré en même temps, le jeton de renouvellement n'est échangé qu'une fois."""

import threading
import time

from src.mirai.shell import token_refresh


def test_a_token_renewed_while_waiting_is_reused_without_a_second_exchange():
    lock = threading.Lock()
    stored = {"token": ""}
    exchanges = []

    def refresh():
        exchanges.append(1)
        time.sleep(0.2)                 # le SSO répond lentement
        stored["token"] = "neuf"
        return "neuf"

    results = []
    threads = [threading.Thread(target=lambda: results.append(
        token_refresh.refresh_once(lock, lambda: stored["token"], refresh))) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(exchanges) == 1
    assert results == ["neuf", "neuf", "neuf"]


def test_a_refused_exchange_gives_no_token():
    assert token_refresh.refresh_once(threading.Lock(), lambda: "", lambda: None) is None


def test_the_client_comes_from_the_dm_configuration_then_from_the_settings():
    settings = {"keycloak_client_id": "local", "client_secret": "s"}
    assert token_refresh.refresh_payload("rt", {"clientId": "dm"}, settings.get) == {
        "grant_type": "refresh_token", "refresh_token": "rt", "client_id": "dm",
        "client_secret": "s"}
    assert token_refresh.refresh_payload("rt", {}, lambda _key: "")["client_id"] == ""
