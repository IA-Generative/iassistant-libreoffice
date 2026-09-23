"""Le catalogue de traduction est complet, coherent et effectivement branche.

L'extension parle cinq langues (fr source, en, es, pt, zh) sans dependance
externe : `src/mirai/i18n.py` porte le catalogue et la resolution de langue.
Ces tests verrouillent les quatre proprietes dont depend l'IHM :

1. aucune cle n'est partiellement traduite (parite des cinq locales) ;
2. la resolution de langue suit l'ordre persiste -> UNO -> environnement -> fr ;
3. toute cle referencee par un appel `_t(...)` existe vraiment, et les cles a
   placeholders s'interpolent sans laisser d'accolade visible ;
4. les libelles statiques d'`oxt/Addons.xcu` correspondent au catalogue.
"""

import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET

import pytest

from src.mirai import i18n
from tests.stubs.uno_stubs import install, make_job

install()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENV_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")

_LOCALE_DEPENDENT_FILES = (
    "src/mirai/entrypoint.py",
    "src/mirai/menu_actions/calc.py",
    "src/mirai/core/presets.py",
    "src/mirai/core/suggestions.py",
    "src/mirai/core/selection_info.py",
    "src/mirai/core/progress.py",
    "src/mirai/core/orchestrator.py",
    "src/mirai/core/doc_analysis.py",
    "src/mirai/core/entry.py",
    "src/mirai/core/capabilities.py",
    "src/mirai/core/clickable.py",
    "src/mirai/ui/palette.py",
)

_ADDON_KEYS = (
    "addon.menubar",
    "addon.open_assistant",
    "addon.settings",
    "addon.test_model",
    "addon.documentation",
    "addon.about",
    "addon.toolbar",
)

# (cle, kwargs) — chaque couple doit s'interpoler dans les cinq locales.
_PLACEHOLDER_CASES = (
    ("common.error", {"detail": "boom"}),
    ("proxy.test_failed", {"detail": "boom"}),
    ("settings.id_prefix", {"value": "42"}),
    ("settings.token_error_proxy", {"detail": "boom", "url": "http://p"}),
    ("settings.token_error_unreachable", {"detail": "boom", "url": "http://p"}),
    ("about.version", {"version": "1.2.3"}),
    ("about.update_available", {"target": "0.0.2"}),
    ("about.downloading", {"target": "0.0.2"}),
    ("about.installed_restart", {"target": "0.0.2"}),
    ("about.download_failed", {"target": "0.0.2"}),
    ("about.uptodate", {"current": "0.0.1"}),
    ("resize.ok_format", {"delta": 3, "new_word_count": 120, "sign": "+"}),
    ("edit.selection_prefix", {"snippet": "texte", "warning": ""}),
    ("edit.prepare_suggestions", {"dots": "..."}),
    ("calc.out_new_col", {"letter": "D", "name": "Resultat"}),
    ("calc.out_col", {"letter": "D"}),
    ("calc.out_col_header", {"header": "Resultat"}),
    ("calc.selection_one", {"n": 1, "ref": "A1:A1"}),
    ("calc.selection_many", {"n": 20, "ref": "A1:D5"}),
    ("calc.formula.detail", {"formula": "=1+1"}),
    ("calc.formula.detail_explained", {"explanation": "somme", "formula": "=1+1"}),
    ("calc.formula.applied", {"formula": "=1+1"}),
    ("calc.formula.filled_down", {"count": 5}),
    ("calc.formula.error_line", {"err": "#ERREUR: x"}),
    ("enroll.auth_waiting", {"dots": "..."}),
    ("enroll.auth_progress", {"dots": "..."}),
    ("enroll.enrolling_progress", {"dots": "..."}),
    ("enroll.error_config_text", {"error": "boom"}),
    ("enroll.failed_text", {"reason": "boom"}),
    ("msg.kc_expired_body", {"redirect_uri": "http://localhost:28443/callback"}),
    ("msg.quota_body", {"delay": "30 secondes"}),
    ("msg.delay_seconds", {"seconds": 30}),
    ("msg.action_failed_body", {"action": "OuvrirAssistant", "exc": "boom"}),
    ("msg.action_unavailable_body", {"action": "OuvrirAssistant"}),
    ("preset.resized", {"target": 7}),
    ("preset.done_transform", {"count": 3, "col": "D"}),
    ("sel.writer_selection", {"excerpt": "abc"}),
    ("sel.calc_range", {"count": 5, "first": "A4", "last": "A8"}),
    ("palette.header", {"app": "Writer"}),
    ("palette.refuse_app", {"app": "Writer"}),
    ("palette.mode_tools", {"mode": "json"}),
    ("palette.capabilities", {"tools": "lecture"}),
    ("palette.journal_prepare", {"label": "Résumer"}),
    ("palette.journal_selection_start", {"chars": 12}),
    ("palette.journal_selection_done", {"how": "remplacée"}),
    ("palette.selection_summary", {"how": "remplacée"}),
    ("palette.journal_read", {"count": 13}),
    ("palette.journal_heading_kept", {"heading": "Titre"}),
    ("palette.journal_rewrite_start", {"first": 1, "last": 3}),
    ("palette.journal_written", {"before": 3, "after": 3}),
    ("palette.document_summary", {"how": "réécrit", "before": 3, "after": 3, "kept": ""}),
    ("palette.err_generic", {"text": "HTTP 500"}),
    ("run.err_generic", {"code": "http_500"}),
    ("entry.test_failed", {"detail": "erreur"}),
    ("entry.model_line", {"model": "mistral"}),
    ("entry.detail", {"detail": "probe"}),
)


def _read(relative_path):
    with open(os.path.join(_REPO_ROOT, relative_path), encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_catalog_every_key_covers_every_supported_locale():
    incomplete = {
        key: sorted(set(i18n.SUPPORTED) - set(entry))
        for key, entry in i18n.CATALOG.items()
        if set(entry) != set(i18n.SUPPORTED)
    }
    assert incomplete == {}


def test_catalog_is_not_shrinking():
    # 223 cles au 2026-09 : le seuil garde la parite ci-dessus utile (un
    # catalogue vide serait trivialement « complet »).
    assert len(i18n.CATALOG) >= 223


def test_catalog_values_are_non_empty_strings():
    blank = [
        (key, code)
        for key, entry in i18n.CATALOG.items()
        for code, value in entry.items()
        if not isinstance(value, str) or not value
    ]
    assert blank == []


def test_catalog_has_no_obvious_placeholder_typo():
    for key, entry in i18n.CATALOG.items():
        reference = set(re.findall(r"\{(\w+)\}", entry[i18n.DEFAULT_LOCALE]))
        for code, value in entry.items():
            assert set(re.findall(r"\{(\w+)\}", value)) == reference, (key, code)


def test_supported_locales_and_labels_agree():
    assert i18n.SUPPORTED == ("fr", "en", "es", "pt", "zh")
    assert i18n.DEFAULT_LOCALE == "fr"
    assert i18n.LANGUAGE_CODES == i18n.SUPPORTED
    assert len(i18n.language_names()) == len(i18n.LANGUAGE_CODES)


# ---------------------------------------------------------------------------
# t()
# ---------------------------------------------------------------------------


def test_unknown_key_returns_the_key():
    assert i18n.t("does.not.exist") == "does.not.exist"


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_t_returns_the_value_of_the_current_locale(code):
    i18n.set_locale(code)
    for key, entry in i18n.CATALOG.items():
        assert i18n.t(key) == entry[code], key


def test_missing_locale_falls_back_to_french(monkeypatch):
    monkeypatch.setitem(i18n.CATALOG, "_test.partial", {"fr": "Bonjour"})
    assert i18n.t("_test.partial") == "Bonjour"
    i18n.set_locale("zh")
    assert i18n.t("_test.partial") == "Bonjour"


def test_t_interpolates_placeholders():
    i18n.set_locale("fr")
    assert "20" in i18n.t("calc.selection_many", n=20, ref="A1:D5")


def test_bad_placeholder_degrades_to_the_raw_template():
    # `t()` avale l'erreur de formatage : on documente ici la degradation
    # (accolade visible) plutôt que de la laisser passer pour un mystere.
    i18n.set_locale("fr")
    rendered = i18n.t("calc.selection_many", unexpected="x")
    assert "{" in rendered
    assert rendered == i18n.CATALOG["calc.selection_many"]["fr"]


@pytest.mark.parametrize("key,kwargs", _PLACEHOLDER_CASES)
@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_placeholder_keys_interpolate_without_leftover_braces(key, kwargs, code):
    i18n.set_locale(code)
    rendered = i18n.t(key, **kwargs)
    assert rendered
    assert "{" not in rendered, (key, code, rendered)
    assert "}" not in rendered, (key, code, rendered)


@pytest.mark.parametrize("key,kwargs", _PLACEHOLDER_CASES)
def test_placeholder_keys_substitute_at_least_one_value(key, kwargs):
    i18n.set_locale("fr")
    rendered = i18n.t(key, **kwargs)
    assert any(str(value) in rendered for value in kwargs.values()), rendered


# ---------------------------------------------------------------------------
# normalize_locale / set_locale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    (
        ("fr", "fr"),
        ("FR", "fr"),
        ("pt-BR", "pt"),
        ("pt_BR", "pt"),
        ("en_US", "en"),
        ("zh-CN.UTF-8", "zh"),
        ("fr@euro", "fr"),
        ("zh_CN.utf8", "zh"),
        ("  es  ", "es"),
        ("de", None),
        ("", None),
        ("   ", None),
        (None, None),
        (123, None),
        (["fr"], None),
    ),
)
def test_normalize_locale(raw, expected):
    assert i18n.normalize_locale(raw) == expected


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_set_locale_round_trip(code):
    assert i18n.set_locale(code) == code
    assert i18n.get_locale() == code


@pytest.mark.parametrize("raw", ("de", "", None, 42))
def test_set_locale_falls_back_to_french_on_unknown_input(raw):
    assert i18n.set_locale(raw) == i18n.DEFAULT_LOCALE
    assert i18n.get_locale() == i18n.DEFAULT_LOCALE


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_language_index_and_code_for_index_are_inverse(code):
    assert i18n.code_for_index(i18n.language_index(code)) == code


@pytest.mark.parametrize("raw", ("de", "", None, 42, -1, 99, "abc", 1.5))
def test_language_index_falls_back_to_zero(raw):
    assert i18n.language_index(raw) == 0


@pytest.mark.parametrize("raw", (-1, 99, "abc", None))
def test_code_for_index_falls_back_to_french(raw):
    assert i18n.code_for_index(raw) == i18n.DEFAULT_LOCALE


# ---------------------------------------------------------------------------
# resolve_locale
# ---------------------------------------------------------------------------


def _clear_locale_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_resolve_locale_prefers_lc_all(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LC_ALL", "pt_BR.UTF-8")
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert i18n.resolve_locale(None) == "pt"


def test_resolve_locale_falls_back_to_lc_messages_then_lang(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LC_MESSAGES", "es_ES.UTF-8")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n.resolve_locale(None) == "es"


def test_resolve_locale_uses_lang_last(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n.resolve_locale(None) == "zh"


def test_resolve_locale_ignores_unsupported_locales(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert i18n.resolve_locale(None) == i18n.DEFAULT_LOCALE


def test_resolve_locale_defaults_to_french_without_any_signal(monkeypatch):
    _clear_locale_env(monkeypatch)
    assert i18n.resolve_locale(None) == i18n.DEFAULT_LOCALE


def test_uno_locale_ignores_non_string_values_from_the_stub():
    # Les doublures UNO rendent des MagicMock : leur lecture doit echouer
    # silencieusement, sinon la langue dependrait de l'environnement de test.
    assert i18n._uno_ui_locale(None) is None


# ---------------------------------------------------------------------------
# Persistance de la langue
# ---------------------------------------------------------------------------


def test_ui_language_is_persisted_and_reread():
    config_dir = tempfile.mkdtemp()
    job = make_job(config_dir=config_dir)
    job.set_config("ui_language", "zh")
    with open(os.path.join(config_dir, "config.json"), encoding="utf-8") as handle:
        assert json.load(handle)["ui_language"] == "zh"
    assert job._get_config_from_file("ui_language", "") == "zh"


def test_persisted_language_is_applied_at_startup():
    config_dir = tempfile.mkdtemp()
    with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as handle:
        json.dump({"ui_language": "es"}, handle)
    make_job(config_dir=config_dir)
    assert i18n.get_locale() == "es"


def test_startup_falls_back_to_environment_when_nothing_is_persisted(monkeypatch):
    config_dir = tempfile.mkdtemp()
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LC_ALL", "pt_BR.UTF-8")
    make_job(config_dir=config_dir)
    assert i18n.get_locale() == "pt"


def test_startup_ignores_an_unknown_persisted_language():
    config_dir = tempfile.mkdtemp()
    with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as handle:
        json.dump({"ui_language": "kl"}, handle)
    make_job(config_dir=config_dir)
    assert i18n.get_locale() in i18n.SUPPORTED


# ---------------------------------------------------------------------------
# Branchement des libelles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative_path", _LOCALE_DEPENDENT_FILES)
def test_every_referenced_key_exists_in_the_catalog(relative_path):
    keys = set(re.findall(r'_t\(\s*"([^"]+)"', _read(relative_path)))
    assert keys
    assert sorted(key for key in keys if key not in i18n.CATALOG) == []


def test_context_menu_keys_are_translated():
    for key in ("menu.summarize", "menu.reformulate", "menu.correct", "menu.translate"):
        assert set(i18n.CATALOG[key]) == set(i18n.SUPPORTED)


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_calc_fallback_prompts_follow_the_locale(code):
    job = make_job()
    i18n.set_locale(code)
    prompts = job._fallback_calc_prompts()
    assert len(prompts) == 10
    assert prompts == [i18n.CATALOG[f"calc.suggest.{position}"][code] for position in range(1, 11)]


def test_edit_suggestion_keys_are_distinct_across_locales():
    keys = [f"edit.suggest.{position}" for position in range(1, 11)]
    i18n.set_locale("fr")
    french = [i18n.t(key) for key in keys]
    i18n.set_locale("en")
    english = [i18n.t(key) for key in keys]
    assert all(french)
    assert all(english)
    assert french != english


# Libelles legitimement identiques en francais et en anglais : noms propres,
# marques, sigles et termes techniques qui ne se traduisent pas. Toute autre
# egalite signale une traduction oubliee.
_IDENTICAL_BY_DESIGN = {
    "about.version",  # "Version {version}" : mot identique dans les deux langues
    "about.window_title",  # nom de la marque "MIrAI — IA'ssistant LibreOffice"
    "addon.documentation",  # "📚 Documentation" : mot identique
    "addon.menubar",  # "🤖 MIrAI" : nom de la marque
    "calc.out_col",  # "  →  col. {letter}" : abreviation technique
    "common.ok",  # "OK" : sigle international
    "common.suggestions",  # "Suggestions..." : mot identique
    "edit.suggestions_plain",  # "Suggestions" : mot identique
    "menu.root",  # "MIrAI" : nom de la marque
    "palette.header",  # "  MIrAI — Assistant ({app})" : nom de la marque
    "palette.title",  # "MIrAI — Assistant" : nom de la marque
    "proxy.title",  # "Proxy" : terme technique identique
    "settings.id_prefix",  # "ID: {value}" : sigle international
    "settings.model_label",  # "Model:" : mot identique
    "settings.models_failed_title",  # "API" : sigle international
    "settings.proxy_button",  # "Proxy" : terme technique identique
    "settings.reload_dialog_title",  # "Configuration" : mot identique
    "tab.actions",  # "Actions" : mot identique
    "tab.conversation",  # "Conversation" : mot identique
    "tab.suggestions",  # "Suggestions" : mot identique
}


def test_no_key_is_left_identical_between_french_and_english_by_accident():
    identical = {
        key
        for key, entry in i18n.CATALOG.items()
        if entry["fr"] == entry["en"]
    }
    assert identical <= _IDENTICAL_BY_DESIGN


# ---------------------------------------------------------------------------
# Page de retour navigateur (callback OAuth)
# ---------------------------------------------------------------------------


def _render_callback_html():
    match = re.search(r'html = (f""".*?""")', _read("src/mirai/entrypoint.py"), re.S)
    assert match, "callback HTML f-string not found in entrypoint.py"
    globals_for_eval = {"_t": i18n.t, "_i18n_get_locale": i18n.get_locale}
    return eval(match.group(1), globals_for_eval)  # noqa: S307 - source du depot


_CALLBACK_KEYS = (
    "callback.title",
    "callback.heading",
    "callback.badge",
    "callback.close_tab",
    "callback.if_stuck",
    "callback.no_action",
)


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_callback_page_is_rendered_in_the_current_locale(code):
    i18n.set_locale(code)
    html = _render_callback_html()
    assert f'<html lang="{code}">' in html
    for key in _CALLBACK_KEYS:
        assert i18n.t(key) in html, key


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_callback_page_keeps_its_css_intact(code):
    i18n.set_locale(code)
    html = _render_callback_html()
    assert "body { font-family: Arial, sans-serif;" in html
    assert ".card { background: #fff;" in html
    assert "}}" not in html


def test_callback_locale_switch_changes_the_page():
    i18n.set_locale("fr")
    french = _render_callback_html()
    i18n.set_locale("zh")
    chinese = _render_callback_html()
    assert french != chinese
    assert i18n.CATALOG["callback.badge"]["zh"] in chinese


# ---------------------------------------------------------------------------
# oxt/Addons.xcu (libelles statiques, hors Python)
# ---------------------------------------------------------------------------


def test_addons_xcu_is_valid_xml():
    ET.parse(os.path.join(_REPO_ROOT, "oxt", "Addons.xcu"))


def test_addons_xcu_declares_every_locale():
    content = _read("oxt/Addons.xcu")
    assert content.count("xml:lang=") == 5 * len(_ADDON_KEYS)
    for code in i18n.SUPPORTED:
        assert f'xml:lang="{code}"' in content


def test_addons_xcu_labels_match_the_catalog():
    content = _read("oxt/Addons.xcu")
    missing = [
        (key, code)
        for key in _ADDON_KEYS
        for code, value in i18n.CATALOG[key].items()
        if value not in content
    ]
    assert missing == []


def test_addons_xcu_has_no_bogus_locale_tag():
    # Les libelles etaient etiquetes `en-US` alors qu'ils etaient en francais.
    assert "en-US" not in _read("oxt/Addons.xcu")


# ---------------------------------------------------------------------------
# Langue des réponses du LLM
#
# Les prompts métier restent en français (décision de conception), mais chaque
# requête embarque la directive llm.answer_language pour que la RÉPONSE suive
# la langue choisie dans l'interface. Les tâches qui fixent elles-mêmes la
# langue (traduction, correction, continuation, remplacement dans le document)
# priment sur cette directive, qui le dit explicitement.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_llm_language_directive_follows_locale(code):
    job = make_job()
    i18n.set_locale(code)
    request = job.make_api_request("ping", "", 10)
    messages = json.loads(request.data)["messages"]
    system = messages[0]["content"]
    assert i18n.t("llm.answer_language") in system


def test_llm_language_directive_switches_with_the_locale():
    job = make_job()
    i18n.set_locale("fr")
    request = job.make_api_request("ping", "", 10)
    french = json.loads(request.data)["messages"][0]["content"]
    i18n.set_locale("zh")
    request = job.make_api_request("ping", "", 10)
    chinese = json.loads(request.data)["messages"][0]["content"]
    assert i18n.CATALOG["llm.answer_language"]["fr"] in french
    assert i18n.CATALOG["llm.answer_language"]["zh"] in chinese
    assert french != chinese


def test_core_text_pipeline_carries_the_directive():
    # Le moteur (palette/presets) construit son prompt système à l'exécution :
    # la directive doit suivre la locale, y compris après un changement à chaud.
    from src.mirai.core import presets

    for code in i18n.SUPPORTED:
        i18n.set_locale(code)
        assert i18n.t("llm.answer_language") in prompts_system(presets)


def prompts_system(presets):
    return presets._system("consigne spécifique")


@pytest.mark.parametrize(
    "relative_path",
    (
        "src/mirai/entrypoint.py",
        "src/mirai/menu_actions/calc.py",
        "src/mirai/core/prompts.py",
    ),
)
def test_llm_language_directive_is_wired(relative_path):
    assert '_t("llm.answer_language")' in _read(relative_path)


def test_no_absolute_french_only_rule_remains():
    # La palette d'éditions imposait « LANGUE OBLIGATOIRE : français » : ces
    # suggestions sont des éléments d'interface et doivent suivre la locale.
    for relative_path in ("src/mirai/entrypoint.py", "src/mirai/core/prompts.py"):
        source = _read(relative_path)
        assert "LANGUE OBLIGATOIRE" not in source
        assert "JAMAIS répondre en anglais" not in source
