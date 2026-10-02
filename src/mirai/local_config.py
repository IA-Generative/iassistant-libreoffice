"""Fichiers locaux de l'extension : écritures atomiques, aucune valeur secrète.

Module sans dépendance UNO, partagé par la coquille (entrypoint.py) et l'add-in
=PROMPT().
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.parse

# Clés de la réponse /config qui portent un secret. La valeur est vidée avant
# toute écriture sur disque ; la clé reste, car sa présence est un signal
# (« llmToken » présent = proxy LLM du DM actif, cf. MainJob._llm_proxy_mode).
SECRET_DM_KEYS = frozenset({
    "llmToken", "llm_api_tokens", "embdToken", "telemetryKey", "tokenOWUI",
    "keycloak_client_secret", "keycloakClientSecret", "client_secret", "clientSecret",
    "proxy_password",
})


def redact_dm_config(config_data):
    """Copie de `config_data` où toute valeur de SECRET_DM_KEYS, à toute
    profondeur, est remplacée par une chaîne vide."""
    if isinstance(config_data, dict):
        return {key: ("" if key in SECRET_DM_KEYS else redact_dm_config(value))
                for key, value in config_data.items()}
    if isinstance(config_data, list):
        return [redact_dm_config(item) for item in config_data]
    return config_data


def write_json_atomic(path, data):
    """Écrit `data` en JSON sans jamais exposer de fichier tronqué : fichier
    temporaire du même dossier (0600 via mkstemp), fsync, puis os.replace,
    atomique sous POSIX comme sous Windows."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def read_json(path):
    """Objet JSON de `path`, ou {} (absent, illisible, autre type)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


DATA_DIR_NAME = "mirai"
SETTINGS_FILE = "settings.json"
SNAPSHOT_FILE = "dm_snapshot.json"
INSTALL_STATE_FILE = "install_state.json"
PACKAGE_CONFIG_FILE = "config.default.json"

# Transport : seul le build (config.default.json embarqué) le fixe ; une valeur
# présente dans les réglages ne le remplace jamais.
TRANSPORT_KEYS = ("enabled", "bootstrap_urls", "bootstrap_url",
                  "bootstrap_insecure_urls", "config_path")
# Réglages du poste que le DM ne pilote jamais (réseau et confiance TLS).
LOCAL_ONLY_KEYS = frozenset({
    "proxy_enabled", "proxy_url", "proxy_username", "proxy_password",
    "proxy_allow_insecure_ssl", "ca_bundle_path",
})
_SETTINGS_SECTIONS = ("config", "settings", "parameters", "mirai", "mirai_config", "miraiConfig")

_write_lock = threading.Lock()


def data_dir(user_config_dir):
    return os.path.join(user_config_dir, DATA_DIR_NAME) if user_config_dir else ""


def _dm_base_url(url):
    """scheme://hôte[:port]/chemin en minuscules, sans barre finale. Le chemin
    reste : un DM peut être servi sous un préfixe."""
    parts = urllib.parse.urlsplit(str(url).strip())
    try:
        port = parts.port
    except ValueError:
        port = None
    host = parts.hostname or ""
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme.lower()}://{netloc}{parts.path.rstrip('/')}"


def package_config_candidates(module_dir):
    """config.default.json à côté du module, puis à la racine de l'OXT."""
    return [os.path.join(module_dir, PACKAGE_CONFIG_FILE),
            os.path.abspath(os.path.join(module_dir, "..", "..", PACKAGE_CONFIG_FILE))]


def select_settings(config_data):
    """Premier sous-objet de réglages d'une réponse /config, ou None."""
    if not isinstance(config_data, dict):
        return None
    for key in _SETTINGS_SECTIONS:
        if isinstance(config_data.get(key), dict):
            return config_data[key]
    return None


class LocalConfig:
    """Lecture en couches : transport de l'OXT, réglages de l'utilisateur,
    dernier instantané du DM (sans secrets), défauts de l'OXT."""

    def __init__(self, user_config_dir, package_candidates):
        self.user_config_dir = user_config_dir or ""
        self.dir = data_dir(self.user_config_dir)
        self._package_candidates = list(package_candidates)

    def _path(self, name):
        return os.path.join(self.dir, name) if self.dir else ""

    def package(self):
        for path in self._package_candidates:
            data = read_json(path)
            if data:
                return data
        return {}

    def settings(self):
        path = self._path(SETTINGS_FILE)
        return read_json(path) if path else {}

    def transport(self):
        package = self.package()
        source = package if any(key in package for key in TRANSPORT_KEYS) else self.settings()
        return {key: source[key] for key in TRANSPORT_KEYS if key in source}

    def transport_scope(self):
        """Empreinte de l'environnement visé (ensemble des DM, profil demandé) :
        les secrets liés à un DM n'ont cours que tant qu'elle ne change pas.
        L'écriture des URL, leur ordre, `enabled` et `bootstrap_insecure_urls`
        n'y entrent pas ; ajouter une URL de repli change d'environnement."""
        transport = self.transport()
        urls = transport.get("bootstrap_urls")
        urls = list(urls) if isinstance(urls, (list, tuple)) else []
        urls.append(transport.get("bootstrap_url"))
        dms = sorted({_dm_base_url(url) for url in urls if str(url or "").strip()})
        canonical = json.dumps({"dms": dms, "config_path": transport.get("config_path")},
                               sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def snapshot(self):
        path = self._path(SNAPSHOT_FILE)
        return read_json(path) if path else {}

    def dm_settings(self):
        return select_settings(self.snapshot().get("config_data")) or {}

    def get(self, key, default=None):
        if key in TRANSPORT_KEYS:
            return self.transport().get(key, default)
        settings = self.settings()
        if key in settings:
            return settings[key]
        if key not in LOCAL_ONLY_KEYS and key not in SECRET_DM_KEYS:
            value = self.dm_settings().get(key)
            if value not in (None, ""):
                return value
        package = self.package()
        if key in package:
            return package[key]
        return default

    def set(self, key, value):
        self.update({key: value})

    def update(self, values=None, remove=()):
        path = self._path(SETTINGS_FILE)
        if not path:
            return
        with _write_lock:
            data = self.settings()
            data.update(values or {})
            for key in remove:
                data.pop(key, None)
            write_json_atomic(path, data)

    def save_dm_snapshot(self, config_data, extra_settings=None):
        """Remplace l'instantané par cette réponse (secrets vidés). Une clé que
        le DM n'envoie plus disparaît donc de l'instantané."""
        path = self._path(SNAPSHOT_FILE)
        if not path or not isinstance(config_data, dict):
            return
        redacted = redact_dm_config(config_data)
        if extra_settings:
            section = select_settings(redacted)
            if section is None:
                section = redacted.setdefault("config", {})
            section.update({key: value for key, value in extra_settings.items()
                            if key not in SECRET_DM_KEYS and value not in (None, "")})
        write_json_atomic(path, {"ts": time.time(), "config_data": redacted})

    def delete_snapshot(self):
        path = self._path(SNAPSHOT_FILE)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
