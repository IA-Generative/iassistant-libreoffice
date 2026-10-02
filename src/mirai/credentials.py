"""Secrets de l'extension — jamais dans un fichier du profil.

Jetons courts (llmToken et telemetryKey du DM, access_token Keycloak) : mémoire
du processus. La coquille et l'add-in =PROMPT() importent ce module sous le même
nom (`src.mirai.credentials`) : ils partagent donc ces jetons.
"""

import threading
import time

DM_LLM_TOKEN = "dm_llm_token"
DM_TELEMETRY_KEY = "dm_telemetry_key"

_memory = {}
_memory_lock = threading.Lock()


def _as_epoch(raw):
    try:
        return int(float(raw or 0))
    except (TypeError, ValueError):
        return 0


def remember(name, value, expires_at=0):
    with _memory_lock:
        if value in (None, ""):
            _memory.pop(name, None)
        else:
            _memory[name] = (str(value), _as_epoch(expires_at))


def recall(name, skew_seconds=60):
    """Valeur mémorisée, ou "" si absente ou expirée."""
    with _memory_lock:
        entry = _memory.get(name)
    if not entry:
        return ""
    value, expiry = entry
    if expiry and time.time() >= expiry - skew_seconds:
        return ""
    return value


def expires_at(name):
    with _memory_lock:
        entry = _memory.get(name)
    return entry[1] if entry else 0


def forget(name):
    with _memory_lock:
        _memory.pop(name, None)


def forget_all():
    with _memory_lock:
        _memory.clear()


def remember_dm_tokens(settings):
    """Mémorise les jetons d'une réponse /config. Une valeur vide efface : le DM
    signale ainsi un credential révoqué ; une clé absente ne touche à rien."""
    if not isinstance(settings, dict):
        return
    if "llmToken" in settings or "llm_api_tokens" in settings:
        token = settings.get("llmToken") or settings.get("llm_api_tokens") or ""
        remember(DM_LLM_TOKEN, token, settings.get("llmTokenExpiresAt"))
    if "telemetryKey" in settings:
        remember(DM_TELEMETRY_KEY, settings.get("telemetryKey"),
                 settings.get("telemetryKeyExpiresAt"))
