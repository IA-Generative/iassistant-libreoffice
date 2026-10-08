"""ConversationStore : roundtrip, caps, clear, corruption, contexte plafonné."""

import json
import os
import tempfile

from src.mirai.core.conversation import ConversationStore


def _store(**kwargs):
    directory = tempfile.mkdtemp()
    return ConversationStore(directory, **kwargs)


def test_roundtrip():
    store = _store()
    store.append("user", "bonjour", "writer")
    store.append("assistant", "salut !", "writer")
    entries = store.load()
    assert [e["role"] for e in entries] == ["user", "assistant"]
    assert entries[0]["text"] == "bonjour"


def test_clear():
    store = _store()
    store.append("user", "x")
    store.clear()
    assert store.load() == []


def test_max_exchanges_cap():
    store = _store(max_exchanges=2)
    for i in range(10):
        store.append("user", f"q{i}")
        store.append("assistant", f"r{i}")
    entries = store.load()
    assert len(entries) == 4
    assert entries[-1]["text"] == "r9"


def test_max_bytes_cap():
    store = _store(max_bytes=2000)
    for _ in range(10):
        store.append("user", "x" * 400)
        store.append("assistant", "y" * 400)
    size = os.path.getsize(store._path)
    assert size <= 3000  # marge pour l'enveloppe JSON
    assert len(store.load()) >= 2


def test_corrupted_file_recovers_empty():
    store = _store()
    store.append("user", "ok")
    with open(store._path, "w", encoding="utf-8") as fh:
        fh.write("{pas du json")
    assert store.load() == []
    store.append("user", "après corruption")   # ne lève pas
    assert len(store.load()) == 1


def test_wrong_shape_recovers_empty():
    store = _store()
    with open(store._path, "w", encoding="utf-8") as fh:
        json.dump(["liste", "inattendue"], fh)
    assert store.load() == []


def test_context_messages_capped_and_ordered():
    store = _store()
    store.append("user", "ancienne question " + "a" * 100)
    store.append("assistant", "ancienne réponse " + "b" * 100)
    store.append("user", "récente question")
    store.append("assistant", "récente réponse")
    messages = store.context_messages(max_chars=60)
    assert messages[-1]["content"] == "récente réponse"
    assert len(messages) <= 3
    assert [m["content"] for m in messages] == ["récente question", "récente réponse"]  # ordre chronologique conservé
    assert messages[0]["role"] in ("user", "assistant")


def test_missing_file_is_empty():
    store = _store()
    assert store.load() == []
    assert store.context_messages() == []
