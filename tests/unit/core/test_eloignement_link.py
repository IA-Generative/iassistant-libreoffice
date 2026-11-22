"""Le lien document ↔ dossier vit dans les propriétés personnalisées, jamais dans le texte."""

from src.mirai.core.eloignement import link
from tests.stubs.fake_eloignement import FakeLinkedDoc


def test_a_document_without_properties_is_not_linked():
    assert link.read_link(FakeLinkedDoc()) is None


def test_write_then_read_the_four_properties():
    doc = FakeLinkedDoc(text="Arrêté portant obligation de quitter le territoire")
    link.write_link(doc, link.Link(dossier="12", modele="m1", document="a-1", version="3"))

    assert doc.props.user.values == {
        "MirAI.Eloignement.Dossier": "12",
        "MirAI.Eloignement.Modele": "m1",
        "MirAI.Eloignement.Document": "a-1",
        "MirAI.Eloignement.Version": "3",
    }
    assert link.read_link(doc) == link.Link("12", "m1", "a-1", "3")
    assert doc.text == "Arrêté portant obligation de quitter le territoire"


def test_properties_are_removable_strings():
    doc = FakeLinkedDoc()
    link.write_link(doc, link.Link(dossier="12"))
    assert set(doc.props.user.attributes.values()) == {128}
    assert all(isinstance(v, str) for v in doc.props.user.values.values())


def test_rewriting_updates_in_place():
    doc = FakeLinkedDoc()
    link.write_link(doc, link.Link(dossier="12", document="a-1", version="3"))
    link.set_version(doc, "4")
    assert link.read_link(doc).version == "4"


def test_a_local_copy_reopened_finds_its_dossier():
    doc = FakeLinkedDoc()
    link.write_link(doc, link.Link(dossier="12", modele="m1", document="a-1", version="3"))
    doc.storeToURL("file:///tmp/copie.odt", ())

    _url, copied = doc.copies[0]
    reopened = FakeLinkedDoc(user_values=copied)
    assert link.read_link(reopened) == link.Link("12", "m1", "a-1", "3")


def test_clear_link():
    doc = FakeLinkedDoc()
    link.write_link(doc, link.Link(dossier="12"))
    link.clear_link(doc)
    assert link.read_link(doc) is None
    assert doc.props.user.values == {}
