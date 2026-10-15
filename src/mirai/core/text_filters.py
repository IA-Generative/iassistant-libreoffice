"""Filtres texte partagés : blocs <think>, markdown, stop phrases et motifs de
question des presets."""

import re

_RE_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_RE_THINK_DANGLING = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)

STOP_PHRASES = ["[END]", "---END---"]

EXTEND_QUESTION_PATTERNS = [
    "puis-je vous", "puis-je t'", "comment puis-je", "en quoi puis-je",
    "que puis-je faire", "puis-je vous aider",
    "pouvez-vous préciser", "pouvez-vous clarifier",
    "could you clarify", "how can i help", "would you like me to",
    "voulez-vous que je", "souhaitez-vous que",
]

SIMPLIFY_QUESTION_PATTERNS = [
    "would you like", "do you want", "should i", "can i help",
    "voulez-vous", "souhaitez-vous", "dois-je", "puis-je",
]


def strip_think_blocks(text):
    """Retire les blocs <think>…</think> (deepseek-r1 et similaires)."""
    return _RE_THINK.sub("", text).lstrip("\n")


def strip_markdown(text):
    """Markdown → texte brut propre (cellules Calc, réponses palette)."""
    text = _RE_THINK.sub("", text)
    text = _RE_THINK_DANGLING.sub("", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text)
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"^[\s]*[-*]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def check_stop_phrase(accumulated, chunk, stop_phrases=None):
    """Retourne (texte_à_insérer, stop_détecté) pour le chunk courant.

    Quand une stop phrase apparaît dans `accumulated`, retourne la portion de
    `chunk` qui la précède (éventuellement vide) et True. Sinon le chunk
    inchangé et False.
    """
    phrases = STOP_PHRASES if stop_phrases is None else stop_phrases
    acc_lower = accumulated.lower()
    for phrase in phrases:
        pos = acc_lower.find(phrase.lower())
        if pos == -1:
            continue
        already_inserted = len(accumulated) - len(chunk)
        partial = chunk[:max(0, pos - already_inserted)] if pos > already_inserted else ""
        return partial, True
    return chunk, False


def _pattern_regex(pattern):
    head = r"(?<!\w)" if pattern[:1].isalnum() else ""
    tail = r"(?!\w)" if pattern[-1:].isalnum() else ""
    return head + re.escape(pattern) + tail


def contains_pattern(text, patterns):
    """True si `text` (minuscule) contient l'un des motifs, en mots entiers :
    « should i » ne trouve pas « should include »."""
    lowered = text.lower()
    return any(re.search(_pattern_regex(p), lowered) for p in patterns)


def patterns_absent_from(source, patterns):
    """Motifs absents du texte source : une question citée de la sélection
    n'est pas une question du modèle."""
    return [p for p in patterns if not contains_pattern(source, [p])]
