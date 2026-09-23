"""Ce que le modèle sait VRAIMENT faire avec des outils — mesuré, pas supposé.

Trois capacités distinctes, longtemps confondues en une seule :

  A. le relais accepte un corps portant `tools` ;
  B. le modèle APPELLE un outil quand la tâche l'exige ;
  C. le modèle ENCHAÎNE lecture → écriture sur plusieurs tours.

Seul A était sondé (`llm_tool_mode`). C'est pourtant **C** qui décide du chemin
d'exécution : un modèle qui lit le document puis répond du texte laisse le
document intact, le run se termine en succès, et l'utilisateur voit « il ne se
passe rien ».

Mesures réelles (Ollama, 2026-07-26) — trois modèles, trois comportements :

    llama3.2      A ✓  B ✓  C ✓   écrit dès le premier tour
    gemma4:12b    A ✓  B ✓  C ✗   lit le document puis s'arrête
    mistral       A ✓  B ✗  C ✗   répond du texte, n'appelle rien

La sonde coûte deux allers-retours : elle est donc déclenchée explicitement
(menu « Tester le modèle ») et son verdict mis en cache par couple
(endpoint, modèle) — jamais silencieusement au milieu du travail.
"""

from __future__ import annotations

import dataclasses
import json

from ..i18n import t as _t

CONFIG_KEY = "assistant_model_capabilities"

# Outils minimaux : une lecture, une écriture. La sonde ne mesure pas la
# qualité de la rédaction, seulement l'aptitude à enchaîner les deux.
PROBE_TOOLS = [
    {"type": "function", "function": {
        "name": "writer_get_document_map",
        "description": "Lit le document en paragraphes numérotés [P1], [P2]…",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "writer_replace_paragraphs",
        "description": ("Remplace les paragraphes [Pstart] à [Pend] par un "
                        "nouveau texte. À utiliser pour appliquer toute "
                        "modification du document."),
        "parameters": {"type": "object", "properties": {
            "start": {"type": "integer"},
            "end": {"type": "integer"},
            "text": {"type": "string"}},
            "required": ["start", "text"]}}},
]

PROBE_SYSTEM = (
    "Tu es un assistant intégré à un traitement de texte. Tu modifies le "
    "document EXCLUSIVEMENT via les outils fournis. Ne renvoie jamais le texte "
    "modifié en clair : il ne serait pas appliqué.")

PROBE_TASK = (
    "PORTÉE : aucune sélection — la demande porte sur le DOCUMENT ENTIER.\n\n"
    "Réduis ce document à deux paragraphes.")

PROBE_MAP = (
    "[P1] <Heading 1> Titre du document\n"
    "[P2] Premier paragraphe de contenu.\n"
    "[P3] Deuxième paragraphe de contenu.\n"
    "[FIN DU DOCUMENT — 3 paragraphes au total, de [P1] à [P3]]")

READ_TOOL = "writer_get_document_map"
WRITE_TOOL = "writer_replace_paragraphs"


@dataclasses.dataclass
class Capabilities:
    """Verdict pour un couple (endpoint, modèle)."""
    model: str = ""
    accepts_tools: bool = False
    calls_tool: bool = False
    chains: bool = False
    detail: str = ""

    @property
    def supports_agentic(self) -> bool:
        """Le mode agentique est-il utilisable pour MODIFIER le document ?

        Il faut savoir enchaîner : appeler un outil de lecture puis un outil
        d'écriture. Sans cela, le document reste intact.
        """
        return self.chains

    def summary(self) -> str:
        """Phrase destinée à l'utilisateur — sans jargon d'implémentation."""
        if not self.accepts_tools:
            return _t("caps.no_tools")
        if not self.calls_tool:
            return _t("caps.no_calls")
        if not self.chains:
            return _t("caps.no_chains")
        return _t("caps.full")

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return None
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def cache_key(endpoint: str, model: str) -> str:
    """Un verdict vaut pour un couple (endpoint, modèle), pas pour un poste."""
    return f"{(endpoint or '').strip()}|{(model or '').strip()}"


def load_cached(shell, endpoint, model):
    """Verdict mémorisé, ou None. Ne fait aucun appel réseau."""
    try:
        raw = shell.get_config(CONFIG_KEY, "") or ""
        store = json.loads(raw) if raw else {}
        return Capabilities.from_dict(store.get(cache_key(endpoint, model)))
    except Exception:
        return None          # cache illisible : on repartira d'une sonde


def save_cached(shell, endpoint, model, capabilities):
    """Mémorise le verdict sans écraser ceux des autres modèles."""
    try:
        raw = shell.get_config(CONFIG_KEY, "") or ""
        store = json.loads(raw) if raw else {}
        if not isinstance(store, dict):
            store = {}
    except Exception:
        store = {}
    store[cache_key(endpoint, model)] = capabilities.to_dict()
    try:
        shell.set_config(CONFIG_KEY, json.dumps(store, ensure_ascii=False))
    except Exception:
        pass          # préférence : ne doit jamais faire échouer la sonde


def _tool_names(step):
    return [call.name for call in (getattr(step, "tool_calls", None) or [])]


def probe(llm, model=""):
    """Mesure les trois capacités. Deux allers-retours au plus.

    `llm` est un LLMClient ; on ne passe que par `step()`, donc la sonde
    bénéficie de la même authentification et du même repli JSON que le reste.
    """
    capabilities = Capabilities(model=model)
    messages = [{"role": "system", "content": PROBE_SYSTEM},
                {"role": "user", "content": PROBE_TASK}]

    first = llm.step(messages, tools=PROBE_TOOLS)
    if first.error:
        capabilities.detail = f"appel refusé ({first.error})"
        return capabilities

    capabilities.accepts_tools = True
    names = _tool_names(first)
    capabilities.calls_tool = bool(names)
    if not names:
        capabilities.detail = "répond du texte, n'appelle aucun outil"
        return capabilities

    if WRITE_TOOL in names:
        capabilities.chains = True
        capabilities.detail = "écrit dès le premier tour"
        return capabilities

    # Le modèle a lu : lui rendre le document et voir s'il écrit ensuite.
    exchange = llm.encode_tool_exchange(
        first, [_FakeResult(call_id=getattr(c, "id", ""), content=PROBE_MAP)
                for c in (first.tool_calls or [])])
    second = llm.step(messages + exchange, tools=PROBE_TOOLS)
    if second.error:
        capabilities.detail = f"second tour refusé ({second.error})"
        return capabilities

    second_names = _tool_names(second)
    capabilities.chains = WRITE_TOOL in second_names
    capabilities.detail = (
        f"1er tour : {', '.join(names)} | "
        f"2e tour : {', '.join(second_names) or 'texte seul'}")
    return capabilities


@dataclasses.dataclass
class _FakeResult:
    """Résultat d'outil minimal, juste ce qu'attend `encode_tool_exchange`."""
    call_id: str = ""
    content: str = ""
    ok: bool = True
    data: dict = None
    error: str = ""
