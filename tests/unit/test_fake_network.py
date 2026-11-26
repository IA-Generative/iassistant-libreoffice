"""Le faux réseau des tests : ce qu'il répond et ce qu'il retient d'une requête."""
import json
import threading
import urllib.error
import urllib.request

import pytest

URL = "https://service.example/ressource"


def test_a_planned_request_gets_its_answer(network):
    network.on("GET", "/ressource", {"valeur": 3}, headers={"X-Request-Id": "r-1"})
    with urllib.request.urlopen(URL) as response:
        assert response.status == 200
        assert response.headers["X-Request-Id"] == "r-1"
        assert json.loads(response.read()) == {"valeur": 3}


def test_the_request_is_kept_as_it_left(network):
    network.on("POST", "/ressource")
    request = urllib.request.Request(
        URL, data=b'{"compte": 2}', headers={"X-Client-UUID": "poste-1"})
    urllib.request.urlopen(request, timeout=7)
    [sent] = network.requests("POST", "/ressource")
    assert (sent.method, sent.url, sent.timeout) == ("POST", URL, 7)
    assert sent.header("x-client-uuid") == "poste-1"
    assert sent.header("Authorization") is None
    assert sent.json() == {"compte": 2}
    assert sent.on_main_thread


def test_a_request_from_a_worker_is_marked_as_such(network):
    network.on("GET", "/ressource")
    worker = threading.Thread(target=urllib.request.urlopen, args=(URL,))
    worker.start()
    worker.join()
    assert not network.sent[0].on_main_thread


def test_an_error_status_raises_the_http_error_with_its_body(network):
    network.on("GET", "/ressource", {"error": {"code": "quota"}}, status=429,
               headers={"Retry-After": "30"})
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(URL)
    assert raised.value.code == 429
    assert raised.value.headers["Retry-After"] == "30"
    assert json.loads(raised.value.read()) == {"error": {"code": "quota"}}


def test_a_network_failure_is_raised_as_given(network):
    network.on("GET", "/ressource", error=urllib.error.URLError("hôte injoignable"))
    with pytest.raises(urllib.error.URLError, match="hôte injoignable"):
        urllib.request.urlopen(URL)


def test_answers_to_one_route_follow_one_another_and_the_last_one_stays(network):
    network.on("GET", "/ressource", error=urllib.error.URLError("premier essai"))
    network.on("GET", "/ressource", "ensuite")
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(URL)
    assert urllib.request.urlopen(URL).read() == b"ensuite"
    assert urllib.request.urlopen(URL).read() == b"ensuite"


def test_an_unplanned_request_fails_like_an_offline_host_and_is_remembered(network):
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(URL)
    assert [(sent.method, sent.url) for sent in network.unexpected] == [("GET", URL)]
    network.unexpected.clear()
