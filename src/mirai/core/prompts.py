"""Prompts système du moteur (français) + protocole d'outils du mode JSON."""

from ..i18n import t as _t

# Système hérité de make_api_request — conservé pour l'iso-fonctionnalité des
# presets pipeline (texte brut, même langue, /no_thinking pour Qwen3).
LEGACY_TEXT_SYSTEM = (
    "/no_thinking\n"
    "Renvoie uniquement du texte brut. N'utilise pas de markdown, de blocs de "
    "code ni de symboles de formatage comme **, *, _, ou #. RÈGLE ABSOLUE : tu "
    "DOIS répondre dans la MÊME LANGUE que le texte fourni par l'utilisateur. "
    "Si le texte est en français, réponds en français. Si le texte est en "
    "anglais, réponds en anglais. Ne change jamais la langue."
)

JSON_TOOL_PROTOCOL = (
    "PROTOCOLE D'OUTILS :\n"
    "Pour utiliser un outil, réponds UNIQUEMENT avec un objet JSON de la forme "
    '{"tool_calls": [{"name": "<nom>", "arguments": {…}}]} — rien d\'autre, '
    "pas de texte autour, pas de bloc de code. Un seul outil à la fois de "
    "préférence. Quand la tâche est terminée (ou pour répondre à l'utilisateur), "
    "réponds normalement en texte, sans JSON."
)

_APP_LABELS = {"writer": "Writer (traitement de texte)",
               "calc": "Calc (tableur)"}


def build_system(app, registry, mode, preset_extra=""):
    """Prompt système du run agentique.

    En mode "auto" le protocole JSON est TOUJOURS inclus : si le client
    bascule natif → json en cours de run (détection), le prompt reste valide.
    """
    parts = [
        "Tu es MIrAI, l'assistant intégré à LibreOffice "
        + _APP_LABELS.get(app, app) + " du ministère de l'Intérieur. "
        "Tu aides l'utilisateur à travailler sur SON document, via les outils "
        "fournis. Tes réponses finales sont en texte brut, sans markdown. "
        + _t("llm.answer_language") + " "
        "Les modifications apportées au document avec les outils conservent la "
        "langue du texte modifié, sauf demande de traduction. "
        "Règles : lis le contexte nécessaire avec les outils de lecture avant "
        "de modifier quoi que ce soit ; fais des modifications minimales et "
        "précises ; si la demande est ambiguë, pose ta question en réponse "
        "finale (l'utilisateur répondra dans la conversation).",
        # Sans cette consigne, une demande du type « restructure ce document en
        # deux paragraphes » recevait une réponse en TEXTE décrivant la
        # restructuration, sans que le document soit modifié. L'utilisateur
        # voyait alors « il ne se passe rien ».
        "AGIS, NE DÉCRIS PAS. Quand l'utilisateur demande une modification du "
        "document — restructurer, réorganiser, réécrire, corriger, traduire —, "
        "tu dois l'APPLIQUER avec les outils d'écriture. Ne te contente jamais "
        "de renvoyer le texte modifié dans ta réponse en laissant le document "
        "inchangé. Si rien n'est sélectionné, la demande porte sur le document "
        "entier : lis-le avec l'outil de carte du document, puis écris avec "
        "l'outil de remplacement de paragraphes. Ta réponse finale se borne à "
        "dire, en une phrase, ce que tu as fait.",
    ]
    if mode in ("json", "auto"):
        catalog = registry.prompt_catalog(app)
        parts.append("OUTILS DISPONIBLES :\n" + catalog)
        parts.append(JSON_TOOL_PROTOCOL)
    else:
        parts.append("Tu disposes d'outils pour lire et modifier le document — "
                     "utilise-les plutôt que de demander à l'utilisateur de "
                     "copier-coller.")
    if preset_extra:
        parts.append(preset_extra)
    return "\n\n".join(parts)
