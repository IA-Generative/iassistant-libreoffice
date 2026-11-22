"""File d'envoi : jamais de perte silencieuse, jamais d'écrasement sur conflit."""

import json
import os
import tempfile

import pytest

from src.mirai.core.eloignement import api, outbox
from tests.stubs.fake_eloignement import ScriptedRelay, saved_file


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def box():
    directory = tempfile.mkdtemp(prefix="mirai-outbox-")
    clock = Clock()
    return outbox.Outbox(directory, clock=clock), clock, directory


def put(box_, directory, content=b"v1", key="a-1", version="3"):
    path = saved_file(directory, "travail.odt", content)
    return box_.put(key, "12", "a-1", version, "12_oqtf_1.odt", path)


def test_a_saved_document_is_frozen_and_waits(box):
    b, _, directory = box
    entry = put(b, directory)
    assert entry.state == outbox.WAITING
    with open(entry.snapshot, "rb") as fh:
        assert fh.read() == b"v1"
    assert [e.key for e in b.due()] == ["a-1"]


def test_the_queue_survives_a_restart(box):
    b, clock, directory = box
    put(b, directory)
    again = outbox.Outbox(directory, clock=clock)
    assert [(e.key, e.version, e.state) for e in again.entries()] == [("a-1", "3", "attente")]


def test_the_latest_save_replaces_the_waiting_one(box):
    b, _, directory = box
    put(b, directory, b"v1")
    put(b, directory, b"v2")
    entries = b.entries()
    assert len(entries) == 1
    with open(entries[0].snapshot, "rb") as fh:
        assert fh.read() == b"v2"


def test_success_removes_the_entry_and_its_copy(box):
    b, _, directory = box
    entry = put(b, directory)
    client = api.Client(ScriptedRelay((201, {}, {"acte_id": "a-1", "version": 4,
                                                 "nom": "12_oqtf_1.odt"})))
    [result] = outbox.send_due(b, client)
    assert (result.state, result.version, result.nom) == ("envoye", "4", "12_oqtf_1.odt")
    assert b.entries() == []
    assert not os.path.exists(entry.snapshot)


def test_service_down_keeps_the_entry_and_plans_a_retry(box):
    b, clock, directory = box
    put(b, directory)
    client = api.Client(ScriptedRelay((503, {"Retry-After": "45"}, b"")))
    [result] = outbox.send_due(b, client)
    assert result.state == outbox.WAITING
    entry = b.get("a-1")
    assert entry.attempts == 1
    assert entry.next_at == clock.now + 45
    assert b.due() == []            # pas avant l'échéance
    clock.now += 46
    assert [e.key for e in b.due()] == ["a-1"]


def test_unreachable_relay_backs_off_exponentially(box):
    b, clock, directory = box
    put(b, directory)
    client = api.Client(ScriptedRelay(OSError("down"), OSError("down")))
    outbox.send_due(b, client)
    assert b.get("a-1").next_at == clock.now + 30
    clock.now += 31
    outbox.send_due(b, client)
    assert b.get("a-1").next_at == clock.now + 60


def test_conflict_never_overwrites_and_waits_for_the_agent(box):
    b, _, directory = box
    put(b, directory, version="3")
    relay = ScriptedRelay((412, {}, {"detail": "version périmée", "version": 7}))
    [result] = outbox.send_due(b, api.Client(relay))
    assert result.state == outbox.CONFLICT
    entry = b.get("a-1")
    assert (entry.state, entry.server_version, entry.version) == ("conflit", "7", "3")
    assert b.due() == []            # plus rejoué sans décision
    assert len(relay.calls) == 1


def test_a_new_save_does_not_hide_an_unresolved_conflict(box):
    b, _, directory = box
    put(b, directory)
    b.conflict("a-1", "version périmée", "7")
    put(b, directory, b"v2")
    assert b.get("a-1").state == outbox.CONFLICT


def test_keep_mine_resends_on_top_of_the_current_version(box):
    b, _, directory = box
    put(b, directory, version="3")
    b.conflict("a-1", "version périmée", "7")
    b.keep_mine("a-1")
    relay = ScriptedRelay((201, {}, {"acte_id": "a-1", "version": 8}))
    [result] = outbox.send_due(b, api.Client(relay))
    assert result.state == "envoye"
    assert relay.calls[0]["headers"]["If-Match"] == '"7"'


def test_archived_dossier_is_refused_with_the_reason_and_the_file_stays(box):
    b, _, directory = box
    entry = put(b, directory)
    relay = ScriptedRelay((409, {}, {"detail": "Ce dossier est archivé"}))
    [result] = outbox.send_due(b, api.Client(relay))
    assert result.state == outbox.REFUSED
    assert "archivé" in b.get("a-1").message
    assert os.path.exists(entry.snapshot)


def test_a_document_filled_in_writer_is_created_once_then_versioned(box):
    b, _, directory = box
    path = saved_file(directory, content=b"rempli")
    b.put("nouveau-12-x", "12", "", "", "acte.odt", path, modele="m1")
    relay = ScriptedRelay(OSError("coupure"),
                          (201, {}, {"acte_id": "a-9", "version": 1, "nom": "2026_11_22_KOFFI.odt"}))
    client = api.Client(relay)
    [first] = outbox.send_due(b, client)
    assert first.state == outbox.WAITING
    b.keep_mine("nouveau-12-x")          # « Réessayer l'envoi »
    [second] = outbox.send_due(b, client)
    assert (second.state, second.document, second.version) == ("envoye", "a-9", "1")
    call = relay.calls[1]
    assert (call["method"], call["path"]) == ("POST", "eloignement/v1/dossiers/12/actes/fichier")
    assert b'name="modele_id"' in call["body"] and b"m1" in call["body"]
    # même contenu, même clé : un renvoi après coupure ne crée pas deux actes
    assert relay.calls[0]["headers"]["Idempotency-Key"] == call["headers"]["Idempotency-Key"]


def test_an_unreadable_queue_file_is_set_aside_not_overwritten(box):
    _, clock, directory = box
    with open(os.path.join(directory, "file_envoi.json"), "w") as fh:
        fh.write("{tronqué")
    again = outbox.Outbox(directory, clock=clock)
    assert again.entries() == []
    assert os.path.exists(os.path.join(directory, "file_envoi.json.illisible"))


def test_the_queue_file_holds_no_document_content(box):
    b, _, directory = box
    put(b, directory, b"KOFFI Marc")
    raw = open(os.path.join(directory, "file_envoi.json"), encoding="utf-8").read()
    assert "KOFFI" not in raw
    assert json.loads(raw)["entries"][0]["dossier"] == "12"
