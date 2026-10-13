"""Internationalisation (i18n) de l'interface MIrAI.

Catalogues JSON, un fichier par langue dans `locales/`, lus avec la bibliotheque
standard (pas de gettext compile), pour les chaines visibles par l'utilisateur
dans les boites de dialogue de configuration. Le francais est la langue source ;
l'extension suit la langue de LibreOffice, et l'anglais quand elle ne la
propose pas.

Ce module vit sous `src/mirai/` et NON sous `core/` ou `ui/` : la regle
d'architecture `tests/unit/rules/test_no_entrypoint_import.py` interdit la
sous-chaine "entrypoint" dans ces deux paquets, et la facade `core` doit rester
independante de la coquille UNO.

Usage (la coquille lit la langue de LibreOffice, voir shell/ui_locale.py) :
    from .i18n import t, set_locale, resolve_locale
    set_locale(resolve_locale("fr-FR"))
    label = t("proxy.section")
"""

import json
import os

DEFAULT_LOCALE = "fr"

# Langue retenue quand celle de LibreOffice n'est pas proposee par l'extension.
FALLBACK_LOCALE = "en"

SUPPORTED = ("fr", "en", "es", "pt", "zh")


_LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")


def _load_catalog():
    """Une entree par cle, {langue: texte}, lue dans locales/<langue>.json."""
    catalog = {}
    for lang in SUPPORTED:
        with open(os.path.join(_LOCALES_DIR, f"{lang}.json"), encoding="utf-8") as fh:
            for key, text in json.load(fh).items():
                catalog.setdefault(key, {})[lang] = text
    return catalog


CATALOG = _load_catalog()


_current_locale = DEFAULT_LOCALE


def normalize_locale(raw):
    """Retourne un code de langue supporte, ou None si non reconnu.

    Accepte les formes regionales ("pt-BR", "en_US", "zh-CN.UTF-8").
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    for separator in (".", "@"):
        head, _sep, _tail = candidate.partition(separator)
        if head:
            candidate = head
    candidate = candidate.replace("_", "-")
    primary = candidate.split("-", 1)[0].strip().lower()
    if primary in SUPPORTED:
        return primary
    return None


def set_locale(code):
    """Fixe la langue courante. Retourne le code effectivement retenu."""
    global _current_locale
    normalized = normalize_locale(code)
    _current_locale = normalized or DEFAULT_LOCALE
    return _current_locale


def get_locale():
    return _current_locale


def resolve_locale(ui_locale):
    """Langue de LibreOffice *ui_locale* si l'extension la propose, sinon anglais."""
    return normalize_locale(ui_locale) or FALLBACK_LOCALE


def t(key, **kwargs):
    """Traduit une cle. Repli : langue courante -> francais -> cle brute."""
    entry = CATALOG.get(key)
    if not entry:
        return key
    text = entry.get(_current_locale) or entry.get(DEFAULT_LOCALE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
