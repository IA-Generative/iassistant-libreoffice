"""Sinks de streaming : où va le texte généré par le LLM.

Contrat : stream_delta(chunk) reçoit les deltas au fil de l'eau (si le client
streame en direct) ; finish(text, streamed) clôt le run — un sink qui n'a reçu
aucun delta écrit alors le texte complet. Jamais de perte.

Les sinks sont appelés depuis le thread worker du run (stream_delta à chaque
delta, puis finish). Les sinks Writer reportent chaque accès UNO sur le thread
principal via ctx.on_main.
"""

from .text_filters import (
    contains_pattern,
    stop_phrase_position,
    strip_markdown,
)


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


class _FilteredWriterSink:
    """Filtre commun des sinks Writer : arrêt sur une question du modèle,
    coupure aux stop phrases, rejeu d'une réponse arrivée d'un bloc. Les
    sous-classes décident où va le texte retenu (`_emit`)."""

    def __init__(self, ctx, question_patterns=None, on_question=None,
                 stop_phrases=None, tee=None):
        self.ctx = ctx
        self.question_patterns = question_patterns or []
        self.on_question = on_question
        self.stop_phrases = stop_phrases
        self.tee = tee                    # callback optionnel (palette)
        self.accumulated = ""
        self.done = False
        self.question_detected = False

    def _before_delta(self):
        pass

    def _emit(self, text):
        raise NotImplementedError

    def stream_delta(self, chunk):
        if self.done:
            return
        self._before_delta()
        self.accumulated += chunk
        if self.question_patterns and contains_pattern(
                self.accumulated, self.question_patterns):
            self.done = True
            self.question_detected = True
            if self.on_question:
                self.on_question(self)
            return
        pos = (stop_phrase_position(self.accumulated, self.stop_phrases)
               if self.stop_phrases else -1)
        if pos == -1:
            self._emit(chunk)
            return
        # La stop phrase peut arriver en plusieurs deltas : on coupe à sa
        # position dans le texte accumulé, pas dans le dernier delta.
        self._emit(self.accumulated[len(self.accumulated) - len(chunk):pos])
        self.accumulated = self.accumulated[:pos].rstrip()
        self.done = True

    def _replay(self, text):
        # Aucun delta reçu : réponse arrivée d'un bloc (mode JSON), ou deltas
        # envoyés ailleurs (la palette streame une demande libre vers sa zone
        # de réponse). Le texte final passe alors par le même filtrage.
        if text and not self.accumulated and not self.done:
            self.stream_delta(text)


class WriterInsertSink(_FilteredWriterSink):
    """Insertion streamée après la sélection : marqueurs d'en-tête/pied,
    stop phrases et détection de question."""

    def __init__(self, ctx, header_marker, footer_marker,
                 question_patterns=None, on_question=None,
                 stop_phrases=None, tee=None):
        super().__init__(ctx, question_patterns=question_patterns,
                         on_question=on_question, stop_phrases=stop_phrases,
                         tee=tee)
        self.header_marker = header_marker
        self.footer_marker = footer_marker
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

    def _before_delta(self):
        self._ensure_started()

    def _emit(self, text):
        self._insert(text)

    def insert_message(self, message):
        """Insère un message de repli (ex. échec après retry)."""
        self._ensure_started()
        self._insert(message)

    def finish(self, text, streamed):
        self._ensure_started()
        self._replay(text)
        if self.footer_marker:
            self.ctx.on_main(lambda: self._text_obj.insertString(
                self._cursor, self.footer_marker, False))


class WriterReplaceSink(_FilteredWriterSink):
    """Remplace la sélection par le texte final retenu, puis la resélectionne
    pour permettre l'itération. Une question du modèle laisse la sélection
    intacte."""

    def _emit(self, text):
        if text and self.tee:
            self.tee(text)

    def insert_message(self, message):
        """Message de repli : en remplacement, jamais dans le document."""
        if self.tee:
            self.tee(message)

    def finish(self, text, streamed):
        self._replay(text)
        if self.question_detected:
            return
        final = self.accumulated.strip()
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
    """Réponse destinée à une cellule Calc. Le flux part vers la palette
    (`tee`) ; la cellule n'est écrite qu'une fois la réponse complète, d'un
    seul appel sur le thread principal : `write(texte)` y écrit par
    safe_set_string.

    Après `finish`, `written` dit si la cellule a été écrite. Une réponse vide
    n'écrit rien."""

    def __init__(self, ctx, write, tee=None):
        self.ctx = ctx
        self.write = write
        self.tee = tee
        self.written = False

    def stream_delta(self, chunk):
        if self.tee:
            self.tee(chunk)

    def finish(self, text, streamed):
        final = strip_markdown(text or "")
        if self.tee:
            self.tee("\n")       # chaque réponse finit sa ligne dans la palette
        if not final.strip():
            return
        self.ctx.on_main(lambda: self.write(final))
        self.written = True
