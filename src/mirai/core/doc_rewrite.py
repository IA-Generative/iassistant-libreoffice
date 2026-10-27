"""Réécriture du document entier — pilotée par Python, pas par le modèle.

Pourquoi ne pas s'en remettre au tool calling : sur `llama-3.3-70b-instruct`,
une demande comme « réécris l'article en deux paragraphes » sans sélection
donnait invariablement `iterations=2` — le modèle appelait bien l'outil de
LECTURE, recevait la carte du document… puis répondait du texte, sans jamais
appeler l'outil d'écriture. Trois renforts successifs (consigne système, rappel
dans le résultat de l'outil, ligne de portée en tête de la demande) ont amélioré
la situation sans la régler : les modèles de cette taille n'enchaînent pas deux
tool calls de façon fiable.

La parade est celle du plan pour les presets « pipeline » : **le LLM n'est
qu'une fonction texte**, et Python applique le résultat. Aucun tool call n'est
requis, donc plus rien à espérer du modèle — c'est déterministe.

Ce module ne connaît pas UNO : il reçoit des paragraphes, rend des paragraphes.
"""

from __future__ import annotations

import dataclasses
import difflib
import re

# Détecter les DEMANDES D'INFORMATION plutôt que les demandes de modification.
# Énumérer les verbes de modification est sans fin — « réduis », « reformate »,
# « aère », « convertis »… — et chaque oubli redonne une action sans effet.
# Les questions, elles, forment un ensemble fermé et reconnaissable : tout ce
# qui n'en est pas une, sans sélection, est un ordre portant sur le document.
_QUESTION_OPENERS = (
    "qu'", "que ", "quel", "quelle", "quels", "quelles", "qui ", "quoi",
    "où ", "ou est", "quand", "pourquoi", "comment", "combien",
    "est-ce", "y a-t-il", "y a t il", "peux-tu me dire", "peux tu me dire",
    "dis-moi", "dis moi", "explique", "explique-moi", "décris", "decris",
    "de quoi", "en quoi", "à quoi", "a quoi", "sais-tu", "connais-tu",
)

# Un mot isolé (« bonjour », « merci ») n'est pas un ordre de réécriture.
_MIN_WORDS_FOR_ORDER = 3


def is_question(prompt: str) -> bool:
    """Vrai si la demande attend une RÉPONSE, pas une modification.

    Attention aux ordres polis : « peux-tu restructurer le document ? » finit
    par un point d'interrogation mais reste un ordre. Seule l'ouverture compte.
    """
    text = (prompt or "").strip().lower()
    return any(text.startswith(opener) for opener in _QUESTION_OPENERS)


def wants_document_rewrite(prompt: str) -> bool:
    """Vrai si la demande vise une réécriture du document.

    Appelée seulement quand rien n'est sélectionné : la portée est alors le
    document entier, et la question devient « faut-il l'écrire, ou seulement
    répondre ? ». On répond OUI par défaut — le coût d'un faux positif est
    faible (le modèle réécrit à l'identique, un Ctrl+Z suffit), celui d'un faux
    négatif est une action sans effet, exactement ce qu'on cherche à éliminer.
    """
    text = (prompt or "").strip()
    if len(text.split()) < _MIN_WORDS_FOR_ORDER:
        return False
    return not is_question(text)


# Styles qui désignent un titre. On teste en minuscules et par préfixe : les
# noms varient selon la langue de l'interface et la version (« Heading 1 »,
# « Titre 1 », « Title »…).
_HEADING_PREFIXES = ("heading", "titre", "title", "subtitle", "überschrift", "encabezado")


def is_heading(style_name: str) -> bool:
    """Vrai si ce style de paragraphe est un titre."""
    name = (style_name or "").strip().lower()
    return any(name.startswith(prefix) for prefix in _HEADING_PREFIXES)


@dataclasses.dataclass(frozen=True)
class Section:
    """Un titre et le corps qui le suit, jusqu'au titre suivant. Index des
    paragraphes à partir de 0 ; `heading` vaut None pour le texte placé avant
    le premier titre."""
    heading: int | None
    body: tuple


def sections(texts, styles):
    """Sections du document dont le corps contient du texte, dans l'ordre.

    Les titres ne sont jamais réécrits : réécrire d'un seul tenant une plage
    qui contient un titre y écrit du corps de texte, qui prend le style Titre.
    Le corps va du premier au dernier paragraphe non vide de la section : les
    paragraphes vides qui l'encadrent espacent souvent un titre de son texte,
    et la réécriture les laisse en place.
    """
    found, heading, body = [], None, []
    for index, style in enumerate(styles):
        if is_heading(style):
            found.append((heading, body))
            heading, body = index, []
        else:
            body.append(index)
    found.append((heading, body))
    result = []
    for heading, body in found:
        filled = [index for index in body if texts[index].strip()]
        if filled:
            result.append(Section(heading, tuple(range(filled[0], filled[-1] + 1))))
    return result


def chunks(texts, indexes, max_chars):
    """Découpe des paragraphes consécutifs en morceaux d'au plus `max_chars`
    caractères, un appel au modèle chacun. Un paragraphe n'est jamais coupé :
    plus long que la limite, il forme son propre morceau."""
    result, current, size = [], [], 0
    for index in indexes:
        length = len(texts[index])
        if current and size + length > max_chars:
            result.append(tuple(current))
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        result.append(tuple(current))
    return result


def build_rewrite_prompt(paragraphs, instruction: str, headings=None) -> str:
    """Demande au modèle un texte brut, un paragraphe par ligne.

    Pas de JSON, pas de tool call, pas de marqueurs : le format le plus simple
    qu'un modèle moyen produit de façon fiable.
    """
    numbered = "\n".join(f"[P{index}] {text}"
                         for index, text in enumerate(paragraphs, start=1))
    context = ""
    if headings:
        # Le titre est donné pour le CONTEXTE, jamais à réécrire : il garde son
        # style, et le modèle ne doit pas le reprendre dans sa réponse.
        joined = " / ".join(headings)
        context = (f"TITRE (à NE PAS reprendre dans ta réponse, "
                   f"il reste en place) : {joined}\n\n")
    return (
        context +
        "TEXTE À RÉÉCRIRE (un paragraphe par ligne, numérotés) :\n"
        f"{numbered}\n\n"
        f"DEMANDE : {instruction}\n\n"
        "RÈGLES DE RÉPONSE :\n"
        "- Renvoie UNIQUEMENT le document réécrit, rien d'autre.\n"
        "- UN paragraphe par ligne, sans les marqueurs [Pn], sans ligne vide.\n"
        "- Pas d'introduction, pas de commentaire, pas de markdown.\n"
        "- Conserve la langue d'origine."
        + ("\n- Ne reprends pas le titre : il n'est pas dans le texte à réécrire."
           if headings else "")
    )


def build_compose_prompt(instruction: str, headings=None) -> str:
    """Demande de rédaction pour un document sans texte de corps : il n'y a
    rien à réécrire, le modèle écrit ce que la demande décrit, sous les titres
    déjà en place."""
    context = ""
    if headings:
        context = (f"TITRES DÉJÀ DANS LE DOCUMENT (à NE PAS reprendre dans ta "
                   f"réponse, ils restent en place) : {' / '.join(headings)}\n\n")
    return (
        context +
        f"DEMANDE : {instruction}\n\n"
        "RÈGLES DE RÉPONSE :\n"
        "- Renvoie UNIQUEMENT le texte demandé, rien d'autre.\n"
        "- UN paragraphe par ligne, sans ligne vide.\n"
        "- Pas d'introduction, pas de commentaire, pas de markdown.\n"
        "- Rédige dans la langue de la demande."
    )


def build_document_prompt(paragraphs, styles, instruction: str, headings=None) -> str:
    """Demande portant sur tout le corps du document, d'un seul appel : elle
    garde ainsi son sens global (« en deux paragraphes »). Les titres
    intérieurs y figurent, marqués « # » ; le modèle les garde ou les retire
    selon la demande, et `styled_lines` leur rend leur style."""
    marked = [f"# {text}" if is_heading(style) else text
              for text, style in zip(paragraphs, styles, strict=True)]
    prompt = build_rewrite_prompt(marked, instruction, headings=headings)
    if any(is_heading(style) for style in styles):
        prompt += ("\n- Seule exception au markdown : une ligne du texte qui commence "
                   "par « # » est un titre intérieur, distinct du TITRE donné en "
                   "contexte ; si tu le gardes, rends-le sur sa propre ligne, "
                   "précédé de « # ».")
    return prompt


# Le marqueur de titre est suivi d'une espace : « #JO2024 » ou « #1 » restent
# du corps de texte.
_HEADING_LINE = re.compile(r"#+(?:\s+(.*))?")


def styled_lines(answer, originals, styles):
    """(texte, style) de chaque paragraphe rendu pour `build_document_prompt`.

    Le corps ne prend jamais un style de titre. Une ligne « # … » reprend le
    style du titre d'origine au texte le plus proche, sinon celui du premier
    titre ; une ligne sans marqueur qui reprend exactement un titre d'origine
    garde son style ; une ligne de corps reprend le style du paragraphe de
    corps à la même place, sinon celui du dernier corps rencontré."""
    heading_styles = {text.strip().lower(): style
                      for text, style in zip(originals, styles, strict=True)
                      if is_heading(style)}
    first_heading = next((style for style in styles if is_heading(style)), "")
    body_style = next((style for style in styles if not is_heading(style)), "")
    result = []
    for position, line in enumerate(parse_rewritten(answer)):
        marked = _HEADING_LINE.fullmatch(line) if first_heading else None
        if marked:
            text = (marked.group(1) or "").strip()
            if text:
                result.append((text, _closest_heading(text, heading_styles) or first_heading))
            continue
        if line.lower() in heading_styles:
            # Un titre gardé sans son marqueur, comme le demande le prompt système.
            result.append((line, heading_styles[line.lower()]))
            continue
        if position < len(styles) and not is_heading(styles[position]):
            body_style = styles[position]
        result.append((line, body_style))
    return result


def _closest_heading(text, heading_styles):
    """Style du titre d'origine le plus proche : le modèle corrige souvent
    l'orthographe ou la casse d'un titre qu'il garde."""
    close = difflib.get_close_matches(text.lower(), heading_styles, n=1, cutoff=0.8)
    return heading_styles[close[0]] if close else None


def parse_rewritten(text: str):
    """Extrait les paragraphes de la réponse du modèle.

    Tolérant : retire les marqueurs [Pn] si le modèle les a conservés, les
    puces, les clôtures de bloc de code, et ignore les lignes vides.
    """
    paragraphs = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^\[P\d+\]\s*", "", line)
        line = re.sub(r"^[-*•]\s+", "", line)
        if line:
            paragraphs.append(line)
    return paragraphs
