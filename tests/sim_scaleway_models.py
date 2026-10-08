#!/usr/bin/env python3
"""
Benchmark all Scaleway text-generation models against Writer menu scenarios.

Simulates:
  - ExtendSelection    (generate continuation of a text)
  - SummarizeSelection (summarize a document excerpt)
  - SimplifySelection  (rewrite in plain language)

Benchmark texts are sourced from Wikipedia (fr) — article
"Administration électronique en France" (licence CC BY-SA),
fetched live via the Wikipedia API at startup (--no-wikipedia to skip).

Credentials are read from the local LibreOffice config file.
Writes results to bench/scaleway_model_comparison.md.

Usage:
    python tests/sim_scaleway_models.py
    python tests/sim_scaleway_models.py --models gpt-oss-120b,llama-3.3-70b-instruct
    python tests/sim_scaleway_models.py --scenarios extend,summarize
    python tests/sim_scaleway_models.py --timeout 60
    python tests/sim_scaleway_models.py --no-wikipedia   # use built-in fallback texts
    python tests/sim_scaleway_models.py --max-tokens 2000
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ─── Config ─────────────────────────────────────────────────────────────────

_CONFIG_PATH = os.path.expanduser(
    "~/Library/Application Support/LibreOffice/4/user/config/config.json"
)
_FALLBACK_BASE_URL = "https://api.scaleway.ai/v1"
_OUTPUT_FILE = Path(__file__).parent.parent / "bench" / "scaleway_model_comparison.md"

# Wikipedia article used as benchmark source
_WIKIPEDIA_ARTICLE = "Administration_électronique_en_France"
_WIKIPEDIA_SOURCE  = "https://fr.wikipedia.org/wiki/Administration_%C3%A9lectronique_en_France"
_WIKIPEDIA_LICENSE = "CC BY-SA 4.0 — Wikipédia contributeurs"

ALL_MODELS = [
    "gpt-oss-120b",
    "llama-3.3-70b-instruct",
    "llama-3.1-8b-instruct",
    "mistral-nemo-instruct-2407",
    "mistral-small-3.2-24b-instruct-2506",
    "gemma-3-27b-it",
    "pixtral-12b-2409",
    "qwen3-235b-a22b-instruct-2507",
    "qwen3-coder-30b-a3b-instruct",
    "voxtral-small-24b-2507",
    "holo2-30b-a3b",
    "deepseek-r1-distill-llama-70b",
    "devstral-2-123b-instruct-2512",
]

# ─── Wikipedia fetch ──────────────────────────────────────────────────────────

def _fetch_wikipedia_texts(article=_WIKIPEDIA_ARTICLE):
    """Fetch the Wikipedia article and return (extend, summarize, simplify) texts.

    Returns None if the fetch fails (caller should use fallback).
    """
    url = (
        "https://fr.wikipedia.org/w/api.php"
        f"?action=query&titles={urllib.request.quote(article)}"
        "&prop=extracts&explaintext=true&format=json"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AssistantMiraiLibreOffice/benchmark (github.com/IA-Generative)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        full_text = list(data["query"]["pages"].values())[0]["extract"]
    except Exception as exc:
        print(f"[WARN] Wikipedia fetch failed: {exc} — using built-in fallback texts",
              file=sys.stderr)
        return None

    # Non-empty, non-section-header lines
    lines = [line.strip() for line in full_text.split("\n")
             if line.strip() and not line.strip().startswith("==")]

    # ExtendSelection — intro paragraph (first non-empty line)
    extend = lines[0] if lines else ""

    # SummarizeSelection — intro + terminologie + 4 premières entrées historique
    summarize = "\n".join(lines[:8])[:1200].rsplit(".", 1)[0] + "."

    # SimplifySelection — entrée «Rapport Carcenac» (texte administratif complexe)
    idx = full_text.find("Rapport Carcenac")
    if idx >= 0:
        simplify = full_text[idx:idx+700].split("\n")[0]
    else:
        simplify = lines[2] if len(lines) > 2 else lines[-1]

    return extend, summarize, simplify


# ─── Fallback texts (used when Wikipedia is unreachable) ──────────────────────
# Source: rédaction interne MIrAI (textes de synthèse fictifs)

_FALLBACK_EXTEND = (
    "L'administration électronique en France désigne l'utilisation des technologies "
    "de l'information et de la communication par les services publics pour améliorer "
    "l'accès aux démarches administratives et moderniser le fonctionnement de l'État. "
    "Depuis les années 2000, plusieurs programmes gouvernementaux successifs ont "
    "structuré cette transformation numérique, en s'appuyant sur des référentiels "
    "d'interopérabilité et des infrastructures mutualisées."
)

_FALLBACK_SUMMARIZE = (
    "Le programme PAGSI, lancé en 1997, a posé les bases de la présence en ligne "
    "des services publics français. Il a été suivi du rapport Carcenac en 2001, "
    "qui a proposé 57 mesures pour adapter l'administration aux téléprocédures. "
    "La création de l'ADAE en 2003, puis sa fusion dans la DGME, ont permis de "
    "centraliser le pilotage de la modernisation. Le référentiel général "
    "d'interopérabilité (RGI) et le référentiel général de sécurité (RGS) ont "
    "ensuite fourni le cadre technique des échanges inter-administrations. "
    "Aujourd'hui, France Connect fédère l'authentification citoyenne et "
    "demarches-simplifiees.fr concentre la dématérialisation des formulaires."
)

_FALLBACK_SIMPLIFY = (
    "Conformément aux dispositions de l'ordonnance n°2005-1516 du 8 décembre 2005 "
    "relative aux échanges électroniques entre les usagers et les autorités "
    "administratives et entre les autorités administratives, les administrations "
    "sont tenues de mettre en œuvre les téléservices permettant aux usagers "
    "d'accomplir en ligne les démarches et formalités les concernant, dans le "
    "respect des référentiels d'accessibilité, d'interopérabilité et de sécurité "
    "arrêtés par voie réglementaire."
)

# Runtime-resolved texts (set in main() after optional Wikipedia fetch)
EXTEND_TEXT   = _FALLBACK_EXTEND
SUMMARIZE_TEXT = _FALLBACK_SUMMARIZE
SIMPLIFY_TEXT  = _FALLBACK_SIMPLIFY

# ─── Question detection patterns (mirrors writer.py) ─────────────────────────

_EXTEND_QUESTION_PATTERNS = [
    "puis-je vous", "puis-je t'", "comment puis-je", "en quoi puis-je",
    "que puis-je faire", "puis-je vous aider",
    "pouvez-vous préciser", "pouvez-vous clarifier",
    "could you clarify", "how can i help", "would you like me to",
    "voulez-vous que je", "souhaitez-vous que",
]

_SIMPLIFY_QUESTION_PATTERNS = [
    "would you like", "do you want", "should i", "can i help",
    "voulez-vous", "souhaitez-vous", "dois-je", "puis-je",
    "here is", "voici", "voilà",
]

_STOP_PHRASES = ["[END]", "---END---"]


def _detect_question(text, patterns):
    """Return the first matching question pattern, or None."""
    lower = text.lower()
    for p in patterns:
        if p in lower:
            return p
    return None


def _detect_stop_phrase(text):
    for phrase in _STOP_PHRASES:
        if phrase.lower() in text.lower():
            return phrase
    return None


# ─── API helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except Exception as exc:
        print(f"[WARN] Could not read config: {exc}", file=sys.stderr)
        return {}


def _call_model(base_url: str, api_key: str, model: str,
                system_prompt: str, user_prompt: str,
                max_tokens: int, timeout: int) -> dict:
    """Call the model and return {text, elapsed, error, finish_reason}."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        elapsed = time.time() - t0
        choice = body.get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "") or ""
        finish_reason = choice.get("finish_reason", "?")
        return {"text": text.strip(), "elapsed": elapsed,
                "finish_reason": finish_reason, "error": None}
    except urllib.error.HTTPError as exc:
        elapsed = time.time() - t0
        body_raw = exc.read().decode(errors="replace")
        return {"text": "", "elapsed": elapsed, "finish_reason": "error",
                "error": f"HTTP {exc.code}: {body_raw[:300]}"}
    except Exception as exc:
        elapsed = time.time() - t0
        return {"text": "", "elapsed": elapsed, "finish_reason": "error",
                "error": str(exc)[:200]}


# ─── Scenario builders ────────────────────────────────────────────────────────

def scenario_extend(base_url, api_key, model, timeout, max_tokens=None):
    system_prompt = (
        "Continue DIRECTEMENT le texte fourni par l'utilisateur. "
        "Écris uniquement la suite naturelle, sans question, sans reformulation, "
        "sans introduction."
    )
    user_prompt = EXTEND_TEXT
    result = _call_model(base_url, api_key, model, system_prompt, user_prompt,
                         max_tokens=max_tokens or 500, timeout=timeout)
    result["question_pattern"] = _detect_question(result["text"], _EXTEND_QUESTION_PATTERNS)
    result["stop_phrase"] = _detect_stop_phrase(result["text"])
    return result


def scenario_summarize(base_url, api_key, model, timeout, max_tokens=None):
    system_prompt = (
        "Tu es un résumeur professionnel. Tu crées des résumés ultra-concis "
        "en utilisant le minimum de mots nécessaire tout en préservant "
        "les informations clés. Tu réponds TOUJOURS dans la même langue "
        "que le texte fourni."
    )
    user_prompt = (
        "TEXTE À RÉSUMER :\n"
        + SUMMARIZE_TEXT
        + "\n\nCrée le résumé le plus court possible qui capture les informations essentielles.\n"
        "Sois extrêmement concis — utilise le minimum de mots nécessaire.\n"
        "Ne pose AUCUNE question.\n"
        "Produis UNIQUEMENT le texte du résumé, sans introduction ni explication.\n"
        "IMPORTANT : Réponds dans la MÊME LANGUE que le texte original.\n\n"
        "RÉSUMÉ :"
    )
    result = _call_model(base_url, api_key, model, system_prompt, user_prompt,
                         max_tokens=max_tokens or 300, timeout=timeout)
    result["question_pattern"] = _detect_question(result["text"], _EXTEND_QUESTION_PATTERNS)
    result["stop_phrase"] = _detect_stop_phrase(result["text"])
    return result


def scenario_simplify(base_url, api_key, model, timeout, max_tokens=None):
    system_prompt = (
        "Tu es un expert en langage simplifié. Tu réécris les textes complexes "
        "dans un langage clair et simple accessible à tous. Tu utilises TOUJOURS "
        "la même langue que le texte fourni. Tu utilises des phrases courtes "
        "et des mots courants."
    )
    user_prompt = (
        "TEXTE À REFORMULER :\n"
        + SIMPLIFY_TEXT
        + "\n\nRéécris ce texte dans un langage clair et simple compréhensible par tous.\n"
        "Utilise :\n"
        "- Des phrases courtes\n"
        "- Des mots courants (évite le jargon et les termes techniques)\n"
        "- La voix active\n"
        "- Des exemples concrets quand c'est possible\n\n"
        "RÈGLES :\n"
        "- Garde la MÊME LANGUE que le texte original\n"
        "- Ne traduis PAS dans une autre langue\n"
        "- Ne pose AUCUNE question\n"
        "- N'ajoute AUCUNE explication\n"
        "- Produis UNIQUEMENT le texte reformulé\n\n"
        "VERSION REFORMULÉE :"
    )
    result = _call_model(base_url, api_key, model, system_prompt, user_prompt,
                         max_tokens=max_tokens or 600, timeout=timeout)
    result["question_pattern"] = _detect_question(result["text"], _SIMPLIFY_QUESTION_PATTERNS)
    result["stop_phrase"] = _detect_stop_phrase(result["text"])
    return result


SCENARIOS = {
    "extend":    ("ExtendSelection",    scenario_extend),
    "summarize": ("SummarizeSelection", scenario_summarize),
    "simplify":  ("SimplifySelection",  scenario_simplify),
}


# ─── Markdown report ─────────────────────────────────────────────────────────

def _status_badge(result: dict) -> str:
    if result["error"]:
        return "❌ ERROR"
    if result["finish_reason"] == "length":
        return "⚠️ TRUNCATED"
    if result["question_pattern"]:
        return f"🔄 QUESTION ({result['question_pattern']})"
    if result["stop_phrase"]:
        return f"🛑 STOP ({result['stop_phrase']})"
    return "✅ OK"


def _truncate(text, max_chars=600):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n… [tronqué — {len(text)} caractères au total]"


def _collect_scenario_keys(all_results):
    seen = []
    for model_results in all_results.values():
        for k in model_results:
            if k not in seen:
                seen.append(k)
    return seen


def _summary_table_lines(all_results, scenario_keys):
    header = ["Modèle"] + [SCENARIOS[k][0] for k in scenario_keys if k in SCENARIOS] + ["Temps moy."]
    lines = [
        "## Tableau récapitulatif",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for model, model_results in all_results.items():
        times = [r["elapsed"] for r in model_results.values() if not r["error"]]
        avg_time = f"{sum(times)/len(times):.1f}s" if times else "—"
        cells = [f"`{model}`"] + [
            _status_badge(model_results[sk]) if sk in model_results else "—"
            for sk in scenario_keys
        ] + [avg_time]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _result_detail_lines(sk, result):
    scenario_label = SCENARIOS[sk][0] if sk in SCENARIOS else sk
    status = _status_badge(result)
    lines = [f"#### {scenario_label} — {status} — {result['elapsed']:.2f}s", ""]
    if result["error"]:
        lines.append(f"**Erreur :** `{result['error']}`")
    else:
        lines += [
            f"**finish_reason :** `{result['finish_reason']}`  ",
            f"**Longueur :** {len(result['text'])} caractères",
        ]
        if result["question_pattern"]:
            lines.append(f"**Motif détecté :** `{result['question_pattern']}`")
        if result["stop_phrase"]:
            lines.append(f"**Stop phrase détectée :** `{result['stop_phrase']}`")
        lines += ["", "**Réponse :**", "", "```", _truncate(result["text"]), "```"]
    lines.append("")
    return lines


def _detail_section_lines(all_results):
    lines = ["---", "", "## Résultats détaillés", ""]
    for model, model_results in all_results.items():
        lines += [f"### `{model}`", ""]
        for sk, result in model_results.items():
            lines += _result_detail_lines(sk, result)
    return lines


def build_report(all_results, run_time, text_source=None):
    scenario_keys = _collect_scenario_keys(all_results)
    source_line = f"**Textes source :** {text_source or 'built-in fallback'}"
    lines = [
        "# Comparaison des modèles Scaleway — Writer menus",
        "",
        f"**Date d'exécution :** {run_time}",
        source_line,
        "",
        "Scénarios testés :",
        "- **ExtendSelection** — continuation d'un paragraphe",
        "- **SummarizeSelection** — résumé d'un extrait",
        "- **SimplifySelection** — reformulation en langage simple",
        "",
        "Statuts : ✅ OK | ⚠️ TRUNCATED | 🔄 QUESTION | 🛑 STOP | ❌ ERROR",
        "",
    ]
    lines += _summary_table_lines(all_results, scenario_keys)
    lines += _detail_section_lines(all_results)
    lines += ["---", "*Généré par `tests/sim_scaleway_models.py`*"]
    return "\n".join(lines)


# ─── Main helpers ─────────────────────────────────────────────────────────────

def _resolve_texts(no_wikipedia):
    """Fetch Wikipedia texts or return fallback. Returns (extend, summarize, simplify, source)."""
    if no_wikipedia:
        print("Wikipedia fetch skipped (--no-wikipedia)")
        return _FALLBACK_EXTEND, _FALLBACK_SUMMARIZE, _FALLBACK_SIMPLIFY, "built-in fallback"
    sys.stdout.write(f"Fetching Wikipedia: {_WIKIPEDIA_ARTICLE} ... ")
    sys.stdout.flush()
    wiki = _fetch_wikipedia_texts()
    if wiki:
        print("OK")
        return wiki[0], wiki[1], wiki[2], _WIKIPEDIA_SOURCE
    print("failed — using fallback")
    return _FALLBACK_EXTEND, _FALLBACK_SUMMARIZE, _FALLBACK_SIMPLIFY, "built-in fallback"


def _parse_args():
    parser = argparse.ArgumentParser(description="Benchmark Scaleway models against Writer menus")
    parser.add_argument("--models", default="",
                        help="Comma-separated list of models (default: all)")
    parser.add_argument("--scenarios", default="",
                        help="Comma-separated scenarios: extend,summarize,simplify (default: all)")
    parser.add_argument("--timeout", type=int, default=90,
                        help="HTTP timeout per request in seconds (default: 90)")
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="Override max_tokens for all scenarios (0 = use scenario defaults)")
    parser.add_argument("--no-wikipedia", action="store_true",
                        help="Skip Wikipedia fetch and use built-in fallback texts")
    parser.add_argument("--output", default=str(_OUTPUT_FILE),
                        help="Output file path (default: bench/scaleway_model_comparison.md)")
    return parser.parse_args()


def _run_model(base_url, api_key, model, scenario_keys, timeout, max_tokens_override):
    print(f"{'─'*60}")
    print(f"Model: {model}")
    model_results = {}
    for sk in scenario_keys:
        scenario_label, scenario_fn = SCENARIOS[sk]
        sys.stdout.write(f"  {scenario_label:25s} ... ")
        sys.stdout.flush()
        result = scenario_fn(base_url, api_key, model, timeout, max_tokens=max_tokens_override)
        model_results[sk] = result
        status = _status_badge(result)
        print(f"{status:35s} {result['elapsed']:.2f}s")
        if result["error"]:
            print(f"    ERROR: {result['error'][:120]}")
    return model_results


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()

    extend, summarize, simplify, text_source = _resolve_texts(args.no_wikipedia)
    global EXTEND_TEXT, SUMMARIZE_TEXT, SIMPLIFY_TEXT
    EXTEND_TEXT, SUMMARIZE_TEXT, SIMPLIFY_TEXT = extend, summarize, simplify

    cfg = _load_config()
    base_url = cfg.get("llm_base_urls", _FALLBACK_BASE_URL)
    api_key  = cfg.get("llm_api_tokens", "")
    if not api_key:
        print("[ERROR] No API key found in config. Set llm_api_tokens.", file=sys.stderr)
        sys.exit(1)

    models = [m.strip() for m in args.models.split(",") if m.strip()] or ALL_MODELS
    scenario_keys = [s.strip() for s in args.scenarios.split(",") if s.strip()] or list(SCENARIOS.keys())
    unknown = [s for s in scenario_keys if s not in SCENARIOS]
    if unknown:
        print(f"[ERROR] Unknown scenarios: {unknown}. Choose from: {list(SCENARIOS.keys())}", file=sys.stderr)
        sys.exit(1)

    max_tokens_override = args.max_tokens or None

    print(f"Base URL   : {base_url}")
    print(f"Models     : {models}")
    print(f"Scenarios  : {scenario_keys}")
    print(f"Timeout    : {args.timeout}s")
    print(f"Texts from : {text_source}")
    if max_tokens_override:
        print(f"max_tokens : {max_tokens_override} (override)")
    print()

    all_results = {
        model: _run_model(base_url, api_key, model, scenario_keys, args.timeout, max_tokens_override)
        for model in models
    }

    print()
    print("Writing report...")
    run_time = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    report = build_report(all_results, run_time, text_source)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report saved to: {output_path}")


if __name__ == "__main__":
    main()
