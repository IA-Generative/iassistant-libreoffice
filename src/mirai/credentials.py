"""Secrets de l'extension — jamais dans un fichier du profil.

Jetons courts (llmToken et telemetryKey du DM, access_token Keycloak) : mémoire
du processus. La coquille et l'add-in =PROMPT() importent ce module sous le même
nom (`src.mirai.credentials`) : ils partagent donc ces jetons.

Secrets durables (paire relais, refresh_token, mot de passe proxy, clé API
saisie) : coffre de l'OS — Gestionnaire d'identification Windows, trousseau
macOS ; ailleurs, mémoire seulement (ré-enrôlement à chaque session).
"""

import base64
import json
import subprocess
import sys
import threading
import time

DM_LLM_TOKEN = "dm_llm_token"
DM_TELEMETRY_KEY = "dm_telemetry_key"

_memory = {}
_memory_lock = threading.Lock()


def _no_log(_message):
    return None


_log = _no_log


def set_log(func):
    """Journal de la coquille (ce module n'importe jamais entrypoint)."""
    global _log
    _log = func or _no_log


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


SERVICE = "MIrAI-LibreOffice"
STORED_KEYS = ("relay_client_id", "relay_client_key", "relay_key_expires_at",
               "refresh_token", "proxy_password", "llm_api_tokens")
# Émis par un DM : liés à l'empreinte du transport (LocalConfig.transport_scope).
SCOPED_KEYS = frozenset(STORED_KEYS[:4])
MEMORY_KEYS = frozenset({"access_token", "access_token_expires_at"})


class MemoryStore:
    persistent = False

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, name):
        with self._lock:
            return self._data.get(name)

    def set(self, name, value):
        with self._lock:
            self._data[name] = value
        return True

    def delete(self, name):
        with self._lock:
            self._data.pop(name, None)


class WindowsCredentialStore:
    """Gestionnaire d'identification Windows (identifiants génériques).

    Persistance ENTERPRISE : l'identifiant suit le profil itinérant, comme le
    profil LibreOffice qui porte l'identifiant d'appareil. Repli LOCAL_MACHINE
    si une stratégie plafonne la persistance."""

    persistent = True
    _CRED_TYPE_GENERIC = 1
    _PERSIST_ORDER = (3, 2)

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._ctypes = ctypes
        self._credential = CREDENTIAL
        self._advapi = advapi

    def _target(self, name):
        return f"{SERVICE}/{name}"

    def get(self, name):
        ctypes = self._ctypes
        pointer = ctypes.POINTER(self._credential)()
        if not self._advapi.CredReadW(self._target(name), self._CRED_TYPE_GENERIC, 0,
                                      ctypes.byref(pointer)):
            return None
        try:
            raw = ctypes.string_at(pointer.contents.CredentialBlob,
                                   pointer.contents.CredentialBlobSize)
        finally:
            self._advapi.CredFree(pointer)
        return raw.decode("utf-8")

    def set(self, name, value):
        raw = value.encode("utf-8")
        return any(self._write(name, raw, persist) for persist in self._PERSIST_ORDER)

    def _write(self, name, raw, persist):
        ctypes = self._ctypes
        blob = (ctypes.c_ubyte * len(raw))(*raw)
        credential = self._credential()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = self._target(name)
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = persist
        credential.UserName = name
        return bool(self._advapi.CredWriteW(ctypes.byref(credential), 0))

    def delete(self, name):
        self._advapi.CredDeleteW(self._target(name), self._CRED_TYPE_GENERIC, 0)


class MacKeychainStore:
    """Trousseau macOS via `security`. Le secret passe par l'entrée standard
    (`security -i`), jamais par une ligne de commande visible de `ps`."""

    persistent = True
    _TIMEOUT_SECONDS = 10

    def get(self, name):
        proc = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                               "-a", name, "-w"], capture_output=True, text=True,
                              timeout=self._TIMEOUT_SECONDS)
        if proc.returncode != 0:
            return None
        try:
            return base64.b64decode(proc.stdout.strip()).decode("utf-8")
        except ValueError:
            return None

    def set(self, name, value):
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        subprocess.run(["security", "-i"], capture_output=True, text=True,
                       input=f"add-generic-password -U -s {SERVICE} -a {name} -w {encoded}\n",
                       timeout=self._TIMEOUT_SECONDS)
        return self.get(name) == value

    def delete(self, name):
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name],
                       capture_output=True, text=True, timeout=self._TIMEOUT_SECONDS)


def default_store():
    try:
        if sys.platform.startswith("win"):
            return WindowsCredentialStore()
        if sys.platform == "darwin":
            return MacKeychainStore()
    except Exception as exc:
        _log(f"[secrets] coffre de l'OS indisponible ({type(exc).__name__}: {exc}) : "
             "secrets en mémoire pour la session")
    return MemoryStore()


_store = None
_store_lock = threading.Lock()


def store():
    global _store
    with _store_lock:
        if _store is None:
            _store = default_store()
        return _store


def use_store(new_store):
    global _store
    with _store_lock:
        _store = new_store


def _safe(method, *args):
    try:
        return method(*args)
    except Exception as exc:
        # Nom de l'opération et de la clé, type d'erreur : jamais la valeur, que
        # le message de l'exception pourrait citer.
        _log(f"[secrets] coffre : {method.__name__}({args[0]}) a échoué "
             f"({type(exc).__name__})")
        return None


def get_secret(name, scope):
    raw = _safe(store().get, name)
    if not raw:
        return ""
    try:
        entry = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(entry, dict):
        return ""
    if name in SCOPED_KEYS and entry.get("scope") != scope:
        delete_secret(name)
        return ""
    return str(entry.get("value") or "")


def set_secret(name, value, scope):
    if value in (None, ""):
        delete_secret(name)
        return True
    entry = {"scope": scope if name in SCOPED_KEYS else None, "value": str(value)}
    return bool(_safe(store().set, name, json.dumps(entry)))


def delete_secret(name):
    _safe(store().delete, name)


def wipe():
    for name in STORED_KEYS:
        delete_secret(name)
    forget_all()
