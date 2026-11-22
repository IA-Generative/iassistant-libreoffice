"""Session : seul un document lié réagit à son enregistrement, sans boucle ni écrasement."""

import os
from types import SimpleNamespace

import pytest

from src.mirai.core.eloignement import link, outbox, session as session_mod
from tests.stubs.fake_eloignement import FakeEloignementShell, FakeLinkedDoc, ScriptedRelay


class FakeUI:
    def __init__(self, confirm=True):
        self.confirm = confirm
        self.asked = []
        self.refreshed = 0
        self.conflicts = []

    def confirm_unknown_link(self, model, lnk):
        self.asked.append(lnk.dossier)
        return self.confirm

    def refresh(self, model):
        self.refreshed += 1

    def show_panel(self, model):
        pass

    def show_conflict(self, model):
        self.conflicts.append(model)

    def document_closed(self, model):
        pass


@pytest.fixture
def env(tmp_path):
    session_mod.reset_for_tests()
    relay = ScriptedRelay()
    shell = FakeEloignementShell(relay, data_dir=str(tmp_path / "data"))
    sess = session_mod.Session(shell)
    sess.kick = lambda: None          # l'envoi est joué à la main dans les tests
    sess.ui = FakeUI()
    path = tmp_path / "acte.odt"
    path.write_bytes(b"PK-odt")
    doc = FakeLinkedDoc(location=session_mod.file_url(str(path)))
    return sess, doc, relay


def linked(sess, doc, version="3"):
    lnk = link.Link(dossier="12", modele="m1", document="a-1", version=version)
    link.write_link(doc, lnk)
    sess.known.add("12", sess.key_for(doc, lnk))
    return lnk


def event(name):
    return SimpleNamespace(EventName=name)


def test_an_unlinked_document_saves_as_usual(env):
    sess, doc, relay = env
    assert sess.on_loaded(doc) is False
    assert doc.listeners == []
    assert sess.on_saved(doc) is None
    assert sess.outbox.entries() == []
    assert relay.calls == []


def test_a_linked_document_is_followed_once(env):
    sess, doc, _ = env
    linked(sess, doc)
    assert sess.on_loaded(doc) is True
    sess.on_loaded(doc)
    assert len(doc.listeners) == 1


def test_only_save_events_send_and_a_copy_sends_nothing(env):
    sess, doc, _ = env
    linked(sess, doc)
    sess.on_loaded(doc)
    listener = doc.listeners[0]
    listener.documentEventOccured(event("OnCopyToDone"))
    listener.documentEventOccured(event("OnModifyChanged"))
    assert sess.outbox.entries() == []
    listener.documentEventOccured(event("OnSaveDone"))
    assert [e.key for e in sess.outbox.entries()] == ["a-1"]


def test_save_queues_the_saved_file_with_the_reference_version(env):
    sess, doc, _ = env
    linked(sess, doc, version="3")
    entry = sess.on_saved(doc)
    assert (entry.dossier, entry.document, entry.version) == ("12", "a-1", "3")
    assert entry.nom == "acte.odt"
    assert sess.describe(doc) == ("En attente d'envoi", "warning")


def test_a_link_received_from_elsewhere_asks_before_sending(env):
    sess, doc, _ = env
    link.write_link(doc, link.Link(dossier="99", document="a-9", version="1"))
    sess.ui.confirm = False
    assert sess.on_saved(doc) is None
    assert sess.ui.asked == ["99"]
    assert sess.outbox.entries() == []
    sess.ui.confirm = True
    assert sess.on_saved(doc) is not None
    sess.on_saved(doc)
    assert sess.ui.asked == ["99", "99"]     # demandé une fois de plus, puis retenu


def test_success_stores_the_new_version_without_triggering_a_new_send(env):
    sess, doc, _ = env
    linked(sess, doc, version="3")
    sess.on_loaded(doc)
    sess.on_saved(doc)
    sess.outbox.sent("a-1")             # ce que fait send_one après un 2xx
    sess.apply_outcome(outbox.Outcome("a-1", outbox.SENT, document="a-1", version="4",
                                      nom="12_oqtf_1.odt"))
    assert link.read_link(doc).version == "4"
    assert doc.stored == 1
    # L'enregistrement fait par le plugin ne repart pas en envoi.
    doc.listeners[0].documentEventOccured(event("OnSaveDone"))
    assert sess.outbox.entries() == []
    text, tone = sess.describe(doc)
    assert (text, tone) == ("Enregistré dans le dossier n° 12 sous « 12_oqtf_1.odt ».", "success")


def test_success_never_saves_edits_made_meanwhile(env):
    sess, doc, _ = env
    linked(sess, doc)
    sess.on_saved(doc)
    doc.modified = True                 # l'agent a continué à écrire
    sess.apply_outcome(outbox.Outcome("a-1", outbox.SENT, document="a-1", version="4"))
    assert doc.stored == 0
    assert link.read_link(doc).version == "4"


def test_conflict_is_shown_and_nothing_is_stored(env):
    sess, doc, _ = env
    linked(sess, doc)
    sess.on_saved(doc)
    sess.outbox.conflict("a-1", "version périmée", "7")
    sess.apply_outcome(outbox.Outcome("a-1", outbox.CONFLICT, "version périmée"))
    assert sess.ui.conflicts == [doc]
    assert doc.stored == 0
    assert sess.describe(doc)[1] == "error"


def test_feature_off_still_keeps_the_save_in_the_queue(env):
    sess, doc, _ = env
    linked(sess, doc)
    sess.shell.enabled = False
    entry = sess.on_saved(doc)
    assert entry is not None
    assert sess.outbox.get("a-1").state == outbox.WAITING


def test_an_act_filled_in_writer_stays_local_and_says_why(env):
    sess, doc, _ = env
    link.write_link(doc, link.Link(dossier="12"))
    assert sess.on_saved(doc) is None
    assert sess.outbox.entries() == []
    text, tone = sess.describe(doc)
    assert tone == "warning" and "poste" in text


def test_discovery_requires_both_the_switch_and_the_relay(env):
    sess, _doc, relay = env
    relay.responses.append((200, {}, {"services": ["ocr"]}))
    with pytest.raises(Exception) as err:
        sess.require()
    assert "pas proposé" in str(err.value)
    sess.shell.enabled = False
    with pytest.raises(Exception) as err:
        sess.require()
    assert "désactivé" in str(err.value)


def test_file_names_from_eloignement_cannot_escape_the_work_directory():
    assert session_mod.safe_filename("../../etc/passwd") == "passwd.odt"
    assert session_mod.safe_filename('12_oqtf:1?.odt') == "12_oqtf_1_.odt"
    assert session_mod.safe_filename("") == "acte.odt"


def test_file_url_round_trip(tmp_path):
    path = str(tmp_path / "dossier 12" / "acte é.odt")
    assert session_mod.file_path(session_mod.file_url(path)) == os.path.abspath(path)
    assert session_mod.file_path("https://exemple/acte.odt") == ""
