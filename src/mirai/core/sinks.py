"""Sinks de streaming : où va le texte généré par le LLM.

Contrat : stream_delta(chunk) reçoit les deltas au fil de l'eau (si le client
streame en direct) ; finish(text, streamed) clôt le run — si streamed est
False, le sink écrit le texte complet à ce moment-là. Jamais de perte.

Les sinks sont appelés depuis le thread worker du run (stream_delta à chaque
delta, puis finish). Les sinks Writer reportent chaque accès UNO sur le thread
principal via ctx.on_main.
"""

from .text_filters import (
    check_stop_phrase,
    contains_pattern,
    strip_markdown,
)
from .tools.calc_tools import safe_set_string


class PaletteSink:
    """Route le texte vers la zone de réponse de la palette (callbacks)."""

    def __init__(self, on_delta=None, on_finish=None):
        self._on_delta = on_delta
        self._on_finish = on_finish
        self.text = ""

    def stream_delta(self, chunk):
        self.text += chunk
        if self._on_delta:
            self._on_delta(chunk)

    def finish(self, text, streamed):
        if not streamed and text:
            self.text = text
            if self._on_delta:
                self._on_delta(text)
        if self._on_finish:
            self._on_finish(self.text or text)


class WriterInsertSink:
    """Insertion streamée après la sélection : marqueurs d'en-tête/pied,
    stop phrases et détection de question."""

    def __init__(self, ctx, header_marker, footer_marker,
                 question_patterns=None, on_question=None,
                 stop_phrases=None, tee=None):
        self.ctx = ctx
        self.header_marker = header_marker
        self.footer_marker = footer_marker
        self.question_patterns = question_patterns or []
        self.on_question = on_question
        self.stop_phrases = stop_phrases
        self.tee = tee                    # callback optionnel (palette)
        self.accumulated = ""
        self.done = False
        self.question_detected = False
        self._cursor = None
        self._text_obj = None
        self._started = False

    def _ensure_started(self):
        if self._started:
            return
        self._started = True
        self.ctx.on_main(self._start_on_main)

    def _start_on_main(self):
        """Prépare le curseur d'insertion. Thread principal exclusivement."""
        rng = self.ctx.controller.getSelection().getByIndex(0)
        self._text_obj = rng.getText()
        self._cursor = self._text_obj.createTextCursorByRange(rng)
        self._cursor.collapseToEnd()
        if self.header_marker:
            self._text_obj.insertString(self._cursor, self.header_marker, False)

    def _insert(self, text):
        if not text:
            return
        self.ctx.on_main(lambda: self._insert_on_main(text))
        if self.tee:
            self.tee(text)

    def _insert_on_main(self, text):
        """Écrit dans le document et suit du regard. Thread principal exclusivement."""
        self._text_obj.insertString(self._cursor, text, False)
        try:
            view_cursor = self.ctx.controller.getViewCursor()
            view_cursor.gotoRange(self._cursor.getEnd(), False)
        except Exception:
            pass

    def stream_delta(self, chunk):
        if self.done:
            return
        self._ensure_started()
        self.accumulated += chunk
        if self.question_patterns and contains_pattern(
                self.accumulated, self.question_patterns):
            self.done = True
            self.question_detected = True
            if self.on_question:
                self.on_question(self)
            return
        if self.stop_phrases is not None:
            to_insert, stopped = check_stop_phrase(
                self.accumulated, chunk, self.stop_phrases)
            if stopped:
                self.accumulated = self.accumulated[
                    :len(self.accumulated) - len(chunk) + len(to_insert)].rstrip()
                self.done = True
            self._insert(to_insert)
            return
        self._insert(chunk)

    def insert_message(self, message):
        """Insère un message de repli (ex. échec après retry)."""
        self._ensure_started()
        self._insert(message)

    def finish(self, text, streamed):
        self._ensure_started()
        if not streamed and text and not self.accumulated and not self.done:
            # Texte arrivé d'un bloc (mode JSON retenu) : rejoue le filtrage.
            self.stream_delta(text)
        if self.footer_marker:
            self.ctx.on_main(lambda: self._text_obj.insertString(
                self._cursor, self.footer_marker, False))


class WriterReplaceSink:
    """Remplace la sélection par le texte final accumulé, puis la resélectionne
    pour permettre l'itération."""

    def __init__(self, ctx, tee=None):
        self.ctx = ctx
        self.tee = tee
        self.accumulated = ""

    def stream_delta(self, chunk):
        self.accumulated += chunk
        if self.tee:
            self.tee(chunk)

    def finish(self, text, streamed):
        final = (self.accumulated or text or "").strip()
        if not final:
            return
        self.ctx.on_main(lambda: self._replace_on_main(final))

    def _replace_on_main(self, final):
        """Remplace puis resélectionne. Thread principal exclusivement."""
        rng = self.ctx.controller.getSelection().getByIndex(0)
        rng.setString(final)
        try:
            self.ctx.controller.select(rng)
        except Exception:
            pass


class CalcCellSink:
    """Accumule et réécrit la cellule (markdown nettoyé) à chaque delta."""

    def __init__(self, cell, tee=None):
        self.cell = cell
        self.tee = tee
        self.accumulated = ""

    def stream_delta(self, chunk):
        self.accumulated += chunk
        safe_set_string(self.cell, strip_markdown(self.accumulated))
        if self.tee:
            self.tee(chunk)

    def finish(self, text, streamed):
        final = self.accumulated or text or ""
        if final:
            safe_set_string(self.cell, strip_markdown(final))
