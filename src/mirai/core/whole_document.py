"""Actions sur tout le document Writer, quand rien n'est sélectionné.

Le document est lu sur le thread principal avant le run. Dans le worker, le
modèle réécrit ou résume chaque section, sans que rien ne soit écrit. Le
résultat est écrit à la fin, en un seul passage sur le thread principal et
dans une seule action d'annulation, si le run n'a pas été arrêté et si le
document n'a pas changé entre-temps.
"""

import bisect
import dataclasses

from ..i18n import t as _t
from . import doc_rewrite
from .sinks import DraftSink
from .tools.writer_tools import (
    PARAGRAPH_BREAK,
    document_body,
    paragraph_style,
    write_paragraphs,
)

# Un appel au modèle reçoit au plus ce nombre de caractères du document :
# environ 5 000 jetons en français, ce qui laisse la place de la réponse dans
# un contexte de 32 000 jetons.
DEFAULT_MAX_CHARS = 20000


def max_chars(shell):
    """Le seuil configuré, ou le seuil par défaut s'il n'est pas un nombre
    positif : à 0, chaque paragraphe partirait dans son propre appel."""
    try:
        value = int(shell.get_config("document_chunk_max_chars", DEFAULT_MAX_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return value if value > 0 else DEFAULT_MAX_CHARS


@dataclasses.dataclass
class Snapshot:
    """Le document lu avant le run. `paragraphs` sont des références UNO, à
    n'utiliser que sur le thread principal. `tables` donne, pour chaque
    tableau du corps, l'index du paragraphe qui le suit : leur texte n'est ni
    lu ni écrit, et aucun morceau réécrit ne les enjambe."""
    paragraphs: list
    texts: list
    styles: list
    tables: tuple = ()

    @property
    def empty(self):
        return not any(text.strip() for text in self.texts)

    @property
    def text(self):
        return "\n".join(text for text in self.texts if text.strip())


def read(ctx):
    """Thread principal exclusivement."""
    items, tables = _body(ctx)
    return Snapshot(items, [p.getString() for p in items],
                    [paragraph_style(p) for p in items], tables)


def _body(ctx):
    """Paragraphes du document hors index (table des matières…) : Writer les
    régénère, et l'API les laisse écrire malgré leur protection. Puis, pour
    chaque tableau du corps, l'index parmi eux du paragraphe qui le suit."""
    paragraphs, tables = document_body(ctx)
    kept = [index for index, p in enumerate(paragraphs) if _index_of(p) is None]
    return ([paragraphs[index] for index in kept],
            tuple(bisect.bisect_left(kept, after) for after in tables))


def _index_of(paragraph):
    try:
        return paragraph.getPropertyValue("DocumentIndex")
    except Exception:
        return None


@dataclasses.dataclass
class Draft:
    """Le texte rendu pour des paragraphes consécutifs du document, un
    paragraphe par entrée. `styles`, un par paragraphe, est donné quand le
    modèle a rendu la structure ; sinon chaque paragraphe garde le sien.
    `heading` est l'index du titre de la section réécrite, s'il y en a un."""
    indexes: tuple
    paragraphs: list
    styles: list = None
    heading: int = None


@dataclasses.dataclass
class Outcome:
    """Ce que le modèle a rendu, ou pourquoi il n'y a rien à écrire."""
    drafts: list = dataclasses.field(default_factory=list)
    text: str = ""
    error: str = ""          # code d'erreur du client, ou "empty_response"
    question: bool = False   # le modèle a posé une question au lieu de répondre
    step: object = None      # dernier aller au modèle, pour expliquer une réponse vide


class Model:
    """Un appel au modèle par morceau, filtré et affiché comme par les sinks
    Writer. Worker."""

    def __init__(self, llm, system_prompt, cancel_event=None, progress=None,
                 question_patterns=None, stop_phrases=None):
        self.llm = llm
        self.system_prompt = system_prompt
        self.cancel_event = cancel_event
        self.progress = progress
        self.question_patterns = question_patterns
        self.stop_phrases = stop_phrases
        self.last_step = None

    def ask(self, prompt, tee=None):
        """Rend le texte retenu, ou un Outcome qui dit pourquoi il n'y en a pas."""
        if self.cancel_event is not None and self.cancel_event.is_set():
            return Outcome(error="cancelled")
        sink = DraftSink(self.question_patterns, self.stop_phrases, tee)
        step = self.llm.step([{"role": "system", "content": self.system_prompt},
                              {"role": "user", "content": prompt}],
                             on_text_delta=sink.stream_delta,
                             cancel_event=self.cancel_event,
                             progress=self.progress)
        self.last_step = step
        if step.error:
            return Outcome(error=step.error)
        sink.finish(step.text, step.streamed)
        if sink.question_detected:
            return Outcome(question=True)
        text = sink.accumulated
        if not text.strip():
            return Outcome(error="empty_response", step=step)
        if step.finish_reason == "length":
            # Écrire une réponse coupée supprimerait les paragraphes qu'elle
            # n'a pas atteints.
            return Outcome(error="truncated", step=step)
        if tee:
            tee("\n")
        return text


def rewrite(model, snapshot, instruction_for, limit, tee=None):
    """Fait réécrire le corps de chaque section, un appel par morceau, sans
    rien écrire. Dans la palette, chaque section s'affiche sous son titre.
    `instruction_for(paragraphes)` donne la demande d'un morceau. Worker."""
    drafts = []
    for section in doc_rewrite.sections(snapshot.texts, snapshot.styles):
        heading = snapshot.texts[section.heading] if section.heading is not None else ""
        if tee and heading.strip():
            tee(heading + "\n")
        for indexes in _chunks(snapshot, section.body, limit):
            originals = [snapshot.texts[i] for i in indexes]
            answer = model.ask(doc_rewrite.build_rewrite_prompt(
                originals, instruction_for(originals),
                headings=[heading] if heading.strip() else None), tee)
            if isinstance(answer, Outcome):
                return answer
            paragraphs = doc_rewrite.parse_rewritten(answer)
            if not paragraphs:
                # Écrire une liste vide supprimerait les paragraphes du morceau.
                return Outcome(error="empty_response", step=model.last_step)
            drafts.append(Draft(indexes, paragraphs, heading=section.heading))
    return Outcome(drafts=drafts)


def _chunks(snapshot, body, limit):
    """Les morceaux du corps d'une section, jamais à cheval sur un tableau :
    effacer les paragraphes en trop d'un tel morceau supprimerait le tableau
    placé entre eux. Chaque suite de paragraphes entre deux tableaux va de
    son premier à son dernier paragraphe non vide."""
    runs, current = [], []
    for index in body:
        if index in snapshot.tables and current:
            runs.append(current)
            current = []
        current.append(index)
    runs.append(current)
    filled = ([index for index in run if snapshot.texts[index].strip()] for run in runs)
    return [chunk for run in filled if run
            for chunk in doc_rewrite.chunks(snapshot.texts, range(run[0], run[-1] + 1), limit)]


def spans_table(snapshot, indexes):
    """Vrai si un tableau du corps sépare deux des paragraphes consécutifs
    `indexes`."""
    return any(indexes[0] < after <= indexes[-1] for after in snapshot.tables)


def compose(model, snapshot, instruction, tee=None):
    """Document sans texte de corps, vide ou fait de titres seuls : le modèle
    rédige ce que la demande décrit, les titres servant de contexte. Worker."""
    headings = [text for text in snapshot.texts if text.strip()]
    answer = model.ask(doc_rewrite.build_compose_prompt(instruction, headings), tee)
    if isinstance(answer, Outcome):
        return answer
    paragraphs = doc_rewrite.parse_rewritten(answer)
    if not paragraphs:
        return Outcome(error="empty_response", step=model.last_step)
    return Outcome(drafts=[Draft(_trailing_blanks(snapshot), paragraphs)])


def _trailing_blanks(snapshot):
    """Index des paragraphes vides qui terminent le document, après son
    dernier tableau : remplir aussi ceux d'avant répartirait le texte de part
    et d'autre du tableau, ou le supprimerait avec un surplus effacé."""
    blanks = []
    for index in reversed(range(max(snapshot.tables, default=0), len(snapshot.texts))):
        if snapshot.texts[index].strip():
            break
        blanks.insert(0, index)
    return tuple(blanks)


def body_span(snapshot):
    """Index du premier au dernier paragraphe de corps non vide, titres
    intérieurs compris ; None si le document n'a que des titres."""
    found = doc_rewrite.sections(snapshot.texts, snapshot.styles)
    if not found:
        return None
    return tuple(range(found[0].body[0], found[-1].body[-1] + 1))


def rewrite_whole(model, snapshot, instruction, tee=None):
    """Fait réécrire tout le corps du document d'un seul appel, titres
    intérieurs compris, pour qu'une demande globale (« en deux paragraphes »)
    garde son sens. Les titres placés avant le corps, comme celui du document,
    restent en place et servent de contexte. Worker."""
    indexes = body_span(snapshot)
    if indexes is None:
        return Outcome()
    texts = [snapshot.texts[i] for i in indexes]
    styles = [snapshot.styles[i] for i in indexes]
    context = [text for text in snapshot.texts[:indexes[0]] if text.strip()]
    answer = model.ask(doc_rewrite.build_document_prompt(
        texts, styles, instruction, headings=context or None), tee)
    if isinstance(answer, Outcome):
        return answer
    lines = doc_rewrite.styled_lines(answer, texts, styles)
    if not lines:
        return Outcome(error="empty_response", step=model.last_step)
    return Outcome(drafts=[Draft(indexes, [text for text, _ in lines],
                                 styles=[style for _, style in lines])])


def summarize(model, snapshot, prompt_for, limit, tee=None):
    """Résumé du document, d'un appel s'il tient sous `limit` caractères ;
    sinon par étapes : chaque morceau de section, puis l'ensemble des résumés
    partiels. Seul le résumé final s'affiche dans la palette. Worker."""
    text = snapshot.text
    if len(text) > limit:
        partials = []
        for section in doc_rewrite.sections(snapshot.texts, snapshot.styles):
            heading = [snapshot.texts[section.heading]] if section.heading is not None else []
            for indexes in doc_rewrite.chunks(snapshot.texts, section.body, limit):
                answer = model.ask(prompt_for("\n".join(
                    heading + [snapshot.texts[i] for i in indexes])))
                if isinstance(answer, Outcome):
                    return answer
                partials.append(answer.strip())
        text = "\n\n".join(partials)
    answer = model.ask(prompt_for(text), tee)
    return answer if isinstance(answer, Outcome) else Outcome(text=answer)


def write(ctx, snapshot, apply, undo_label, cancel_event=None):
    """Applique `apply()` dans une seule action d'annulation, si le run n'a pas
    été arrêté et si le document n'a pas changé depuis sa lecture. Rend
    "written", "stopped" ou "changed". Thread principal exclusivement, celui
    du bouton Arrêter : un arrêt demandé avant cette écriture est donc vu."""
    if cancel_event is not None and cancel_event.is_set():
        return "stopped"
    if [p.getString() for p in _body(ctx)[0]] != snapshot.texts:
        return "changed"
    undo = ctx.model.getUndoManager()
    undo.enterUndoContext(undo_label)
    try:
        apply()
    finally:
        undo.leaveUndoContext()
    return "written"


def with_tables(snapshot, message):
    """Le bilan d'une action qui a écrit son résultat, suivi du nombre de
    tableaux qu'elle a laissés de côté, s'il y en a."""
    if not snapshot.tables:
        return message
    return message + " " + _t("palette.tables_left", count=len(snapshot.tables))


def replace(ctx, snapshot, drafts):
    """Écrit chaque morceau réécrit à la place de ses paragraphes. Les titres,
    hors des morceaux, gardent texte et style. Thread principal exclusivement."""
    for draft in drafts:
        write_paragraphs(ctx.model.Text,
                         [snapshot.paragraphs[i] for i in draft.indexes],
                         draft.paragraphs, draft.styles)


def headings_kept(snapshot, drafts):
    """Nombre de titres encore dans le document une fois les morceaux écrits
    à leur place : ceux hors des morceaux, plus ceux que le modèle a rendus."""
    rewritten = {index for draft in drafts for index in draft.indexes}
    outside = sum(1 for index, style in enumerate(snapshot.styles)
                  if index not in rewritten and snapshot.texts[index].strip()
                  and doc_rewrite.is_heading(style))
    returned = sum(1 for draft in drafts for style in draft.styles or ()
                   if doc_rewrite.is_heading(style))
    return outside + returned


def rewritten_lines(snapshot, drafts):
    """Lignes (texte, style) du bloc ajouté après le document : chaque section
    sous son titre, chaque paragraphe réécrit dans le style que le modèle lui
    a donné, ou à défaut dans celui du corps qu'il remplace."""
    lines, shown = [], set()
    for draft in drafts:
        if draft.heading is not None and draft.heading not in shown:
            shown.add(draft.heading)
            lines.append((snapshot.texts[draft.heading], snapshot.styles[draft.heading]))
        styles = draft.styles or [snapshot.styles[draft.indexes[0]]] * len(draft.paragraphs)
        lines.extend(zip(draft.paragraphs, styles, strict=True))
    return lines


def add_text(ctx, snapshot, draft):
    """Écrit le texte rédigé pour un document sans texte de corps, dans le
    style du corps : les paragraphes vides qui terminent le document sont
    remplis d'abord, le reste suit le dernier paragraphe. Thread principal
    exclusivement."""
    style = _body_style(snapshot)
    if draft.indexes:
        write_paragraphs(ctx.model.Text, [snapshot.paragraphs[i] for i in draft.indexes],
                         draft.paragraphs, [style] * len(draft.paragraphs))
    else:
        _insert_after(ctx, snapshot.paragraphs[-1],
                      [(text, style) for text in draft.paragraphs])


def append_block(ctx, snapshot, lines, header, footer):
    """Insère après le dernier paragraphe un bloc encadré par deux marqueurs,
    un paragraphe par ligne (texte, style), et y amène le curseur. Une ligne
    sans style, comme les marqueurs, prend celui du corps du document. Thread
    principal exclusivement."""
    body_style = _body_style(snapshot)
    _insert_after(ctx, snapshot.paragraphs[-1],
                  [(header, body_style),
                   *[(text, style or body_style) for text, style in lines],
                   (footer, body_style)])


def _body_style(snapshot):
    """Le premier style de corps du document ; « Standard », le style par
    défaut de Writer, s'il n'a que des titres."""
    return next((style for style in snapshot.styles
                 if style and not doc_rewrite.is_heading(style)), "Standard")


def _insert_after(ctx, paragraph, lines):
    """Insère un paragraphe par ligne (texte, style) après `paragraph`, et y
    amène le curseur. Thread principal exclusivement."""
    text_obj = ctx.model.Text
    cursor = text_obj.createTextCursorByRange(paragraph.getEnd())
    for text, style in lines:
        text_obj.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
        # Sans style explicite, le paragraphe hériterait de celui qui le
        # précède, souvent un titre.
        try:
            cursor.setPropertyValue("ParaStyleName", style)
        except Exception:
            pass
        text_obj.insertString(cursor, text, False)
    try:
        ctx.controller.getViewCursor().gotoRange(cursor.getEnd(), False)
    except Exception:
        pass
