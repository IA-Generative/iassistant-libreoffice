"""
Headless tests for _summarize_selection stop-phrase logic.

Reproduces the bug where GPT-style models (e.g. gptoss) that naturally
include phrases like "end of document" produce an empty result because the
stop-phrase early-return skips text insertion.
"""
import unittest
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install

install()

from src.mirai.menu_actions.writer import _summarize_selection

# Helpers

def _make_job(stream_chunks):
    """Return a mock job whose stream_request calls the callback with *stream_chunks*."""
    job = MagicMock()
    job.get_config.side_effect = lambda key, default="": {
        "api_type": "chat",
        "summarize_selection_max_tokens": 15000,
    }.get(key, default)

    def _fake_stream(request, api_type, callback):
        for chunk in stream_chunks:
            callback(chunk)

    job.stream_request.side_effect = _fake_stream
    job.make_api_request.return_value = MagicMock()
    return job


def _make_text_harness():
    """Return (text, selection, text_range, cursor, inserted_calls) mocks."""
    inserted = []

    cursor = MagicMock()

    text = MagicMock()
    text.createTextCursorByRange.return_value = cursor
    text.insertString.side_effect = lambda cur, s, b: inserted.append(s)

    text_range = MagicMock()
    text_range.getString.return_value = "Un texte de test suffisamment long pour le résumé."

    selection = MagicMock()
    selection.getByIndex.return_value = text_range

    return text, selection, text_range, cursor, inserted


# Tests

class TestSummarizeStopPhraseNormal(unittest.TestCase):
    """Happy-path: no stop phrases → all chunks inserted."""

    def test_all_chunks_inserted(self):
        chunks = ["Ce texte ", "parle de ", "choses importantes."]
        job = _make_job(chunks)
        text, selection, text_range, _, inserted = _make_text_harness()

        _summarize_selection(job, text, selection, text_range)

        full = "".join(inserted)
        self.assertIn("Ce texte ", full)
        self.assertIn("parle de ", full)
        self.assertIn("choses importantes.", full)


class TestSummarizeStopPhraseAtEndOfChunk(unittest.TestCase):
    """Stop phrase '[END]' appears alone in its own chunk — nothing after it inserted."""

    def test_text_before_stop_phrase_is_present(self):
        chunks = ["Résumé du document. ", "[END]", " texte supplémentaire"]
        job = _make_job(chunks)
        text, selection, text_range, _, inserted = _make_text_harness()

        _summarize_selection(job, text, selection, text_range)

        full = "".join(inserted)
        self.assertIn("Résumé du document.", full)
        # "[END]" itself should NOT appear
        self.assertNotIn("[END]", full)
        # text after the stop phrase must NOT appear
        self.assertNotIn("texte supplémentaire", full)


class TestSummarizeStopPhraseMidChunk(unittest.TestCase):
    """
    BUG REPRODUCTION: stop phrase appears MID-CHUNK (e.g. 'end of document' inside a
    sentence).  The text BEFORE the stop phrase in that chunk MUST be inserted.
    Previously the entire chunk was silently dropped, causing empty results.
    """

    def test_partial_chunk_before_stop_phrase_is_inserted(self):
        # GPT model returns one big chunk containing "end of document" mid-sentence
        chunks = ["Ce résumé couvre la fin du document[END] et quelques détails."]
        job = _make_job(chunks)
        text, selection, text_range, _, inserted = _make_text_harness()

        _summarize_selection(job, text, selection, text_range)

        full = "".join(inserted)
        # The text BEFORE the stop marker must be in the output
        self.assertIn("Ce résumé couvre la fin du document", full)
        # The stop marker itself must NOT appear
        self.assertNotIn("[END]", full)
        # Text AFTER the stop marker must NOT appear
        self.assertNotIn("et quelques détails", full)

    def test_end_of_document_phrase_passes_through(self):
        """'end of document' is natural language — NOT a stop phrase.
        GPT models can produce it mid-summary; it must pass through intact."""
        chunks = ["Voici un résumé. end of document sections analysées."]
        job = _make_job(chunks)
        text, selection, text_range, _, inserted = _make_text_harness()

        _summarize_selection(job, text, selection, text_range)

        full = "".join(inserted)
        # All text must appear — "end of document" is not a stop phrase
        self.assertIn("Voici un résumé.", full)
        self.assertIn("end of document", full)
        self.assertIn("sections analysées.", full)

    def test_stop_phrase_first_chunk_not_empty(self):
        """
        When the stop phrase is the VERY FIRST thing returned, the result is empty
        (nothing before it to insert) — but must not crash and delimiters still appear.
        """
        chunks = ["[END]"]
        job = _make_job(chunks)
        text, selection, text_range, _, inserted = _make_text_harness()

        _summarize_selection(job, text, selection, text_range)

        # Delimiters should still be inserted
        full = "".join(inserted)
        self.assertIn("---début-du-résumé---", full)
        self.assertIn("---fin-du-résumé---", full)
        # No stop phrase itself
        self.assertNotIn("[END]", full)


class TestSummarizeStopPhraseNoLeakAfterDetection(unittest.TestCase):
    """
    After a stop phrase is detected, subsequent stream chunks must be silently
    ignored — they must NOT appear in the document.
    """

    def test_no_leak_after_stop_phrase(self):
        chunks = ["Début du résumé. ", "---END--- ", "Ceci ne doit PAS apparaître."]
        job = _make_job(chunks)
        text, selection, text_range, _, inserted = _make_text_harness()

        _summarize_selection(job, text, selection, text_range)

        full = "".join(inserted)
        self.assertIn("Début du résumé.", full)
        self.assertNotIn("---END---", full)
        self.assertNotIn("Ceci ne doit PAS apparaître.", full)


if __name__ == "__main__":
    unittest.main()
