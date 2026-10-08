"""Analyse du document par le modèle → propositions d'amélioration.

Les suggestions statiques (`suggestions.py`) ne regardent que la longueur de la
sélection ; ce module demande au modèle des propositions fondées sur le contenu.

Ce module fabrique la demande envoyée au modèle et remet en forme sa réponse.
Il ne fait **aucun appel réseau et ne touche pas à UNO** : c'est la palette qui
l'orchestre depuis un thread de travail. Cette séparation le rend testable sans
LibreOffice ni relais.

Le rendu ne remplace jamais les propositions statiques par du vide : en cas
d'échec, l'appelant garde les siennes (cf. `AssistantPalette`).
"""

from ..i18n import t as _t

MAX_CHARS = 6000          # au-delà, on tronque : une analyse structurelle n'a
                          # pas besoin du document entier, et le relais plafonne
MIN_CHARS = 200           # en deçà, il n'y a rien à structurer
MAX_ITEMS = 5             # une liste plus longue n'est plus lue

# Budget large : les modèles à raisonnement puisent réflexion ET réponse dans le
# même `max_tokens`, et la réponse vient en dernier.
MAX_TOKENS = 12000

SYSTEM_PROMPT = (
    "Tu es relecteur de documents professionnels. On te donne un extrait. "
    "Tu proposes des améliorations CONCRÈTES de structure et de rédaction : "
    "découpage en sections, titres manquants, paragraphes trop longs, "
    "répétitions, tournures lourdes, incohérences de ton. "
    "Règles absolues : une proposition par ligne, préfixée d'un tiret ; "
    f"{MAX_ITEMS} propositions au maximum ; chacune tient en une phrase et dit "
    "QUOI faire, pas ce qui est bien. N'écris rien d'autre : ni introduction, "
    "ni conclusion, ni numérotation, ni texte réécrit."
)


def build_messages(text):
    """Messages prêts pour le client LLM, ou None si le texte ne vaut pas l'appel."""
    body = (text or "").strip()
    if len(body) < MIN_CHARS:
        return None
    if len(body) > MAX_CHARS:
        # Couper sur une frontière de ligne : un paragraphe tronqué en plein
        # milieu se lit comme une faute de rédaction, et le modèle la signale.
        body = body[:MAX_CHARS].rsplit("\n", 1)[0] or body[:MAX_CHARS]
    return [{"role": "system",
             "content": SYSTEM_PROMPT + " " + _t("llm.answer_language")},
            {"role": "user", "content": body}]


def parse(raw, truncated=False):
    """Extrait les propositions de la réponse du modèle.

    Tolérant sur la forme — les modèles ajoutent volontiers une phrase
    d'introduction, une numérotation ou des puces variées, malgré la consigne.

    `truncated` (flux arrêté sur `length`) retire la DERNIÈRE proposition : elle
    est coupée en plein mot et ne vaut pas mieux que rien.
    """
    items = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for marker in ("- ", "– ", "— ", "* ", "• "):
            if line.startswith(marker):
                line = line[len(marker):]
                break
        else:
            # Numérotation « 1. » / « 2) » — on l'accepte aussi.
            head = line.split(" ", 1)
            if len(head) == 2 and head[0].rstrip(".)").isdigit():
                line = head[1]
            else:
                continue      # ni tiret ni numéro : phrase de liaison, ignorée
        line = line.strip(" .").strip()
        if line and line not in items:
            items.append(line)
    if truncated and len(items) > 1:
        items = items[:-1]
    return items[:MAX_ITEMS]


def render(items):
    """Met en forme les propositions pour l'onglet Suggestions."""
    if not items:
        return _t("analysis.unavailable")
    lines = [_t("analysis.header"), ""]
    lines += [f"· {item}" for item in items]
    return "\n".join(lines)
