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

# Simplifier reconnaît aussi tout ce que reconnaissent les autres actions,
# demandes de précision comprises.
SIMPLIFY_QUESTION_PATTERNS = EXTEND_QUESTION_PATTERNS + [
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


def stop_phrase_position(text, phrases):
    """Position de la première stop phrase dans `text`, ou -1."""
    lowered = text.lower()
    found = [pos for pos in (lowered.find(p.lower()) for p in phrases) if pos != -1]
    return min(found, default=-1)


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
