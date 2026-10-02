"""Fichiers locaux de l'extension : écritures atomiques, aucune valeur secrète.

Module sans dépendance UNO, partagé par la coquille (entrypoint.py) et l'add-in
=PROMPT().
"""

import hashlib
import json
import os
import shutil
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


_frozen = False


def freeze():
    global _frozen
    _frozen = True


def is_frozen():
    return _frozen


def unfreeze_for_tests():
    global _frozen
    _frozen = False


def write_json_atomic(path, data):
    """Écrit `data` en JSON sans jamais exposer de fichier tronqué : fichier
    temporaire du même dossier (0600 via mkstemp), fsync, puis os.replace,
    atomique sous POSIX comme sous Windows."""
    if _frozen:
        return
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


def legacy_home_dir():
    """Dossier personnel où les anciennes versions écrivaient log.txt."""
    return os.path.expanduser("~")


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

    def record_install(self, version, package):
        """Compare l'installation courante à la précédente et l'enregistre."""
        path = self._path(INSTALL_STATE_FILE)
        if not path:
            return set()
        current = {"version": str(version or ""), "package": str(package or ""),
                   "transport": self.transport_scope()}
        previous = read_json(path)
        write_json_atomic(path, current)
        if not previous:
            return {"first_run"}
        return {key for key, value in current.items() if previous.get(key) != value}

    def migrate_legacy(self, home_dir):
        """Range l'ancienne disposition dans le dossier de l'extension.

        Idempotent : un rollback vers une version antérieure réécrit config.json,
        la migration suivante le reprend sans écraser les réglages plus récents.
        Rend les actions effectuées, pour le journal. Rien après un effacement
        (gel) : l'add-in =PROMPT() recréerait sinon le dossier."""
        if not self.dir or _frozen:
            return []
        actions = []
        with _migration_lock:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            actions += self._migrate_legacy_config()
            actions += self._move_legacy_files()
            log_path = os.path.join(home_dir, "log.txt") if home_dir else ""
            if log_path and os.path.isfile(log_path) and looks_like_our_log(log_path):
                try:
                    os.remove(log_path)
                    actions.append("~/log.txt : ancien journal supprimé")
                except OSError as exc:
                    actions.append(f"~/log.txt : suppression impossible ({exc})")
        return actions

    def _migrate_legacy_config(self):
        legacy_path = os.path.join(self.user_config_dir, LEGACY_CONFIG_FILE)
        legacy = read_json(legacy_path)
        if not set(legacy) - set(IDENTITY_KEYS):
            if os.path.exists(legacy_path) or not self._write_identity_stub(legacy_path, legacy):
                return []
            return ["config.json : souche d'identité écrite"]
        keep = set(LEGACY_KEEP_KEYS)
        if not _truthy(self.transport().get("enabled", legacy.get("enabled", False))):
            keep |= LEGACY_OFFLINE_KEEP_KEYS
        if RELOAD_FOOTPRINT_KEYS & set(legacy):
            keep.discard("proxy_allow_insecure_ssl")
        kept = {key: value for key, value in legacy.items() if key in keep}
        current = self.settings()
        self.update({key: value for key, value in kept.items()
                     if key not in current or key in LEGACY_FRESHER_KEYS})
        self._write_identity_stub(legacy_path, legacy)
        return [f"config.json : {len(kept)} clé(s) reprise(s), "
                f"{len(legacy) - len(kept)} abandonnée(s)"]

    def _write_identity_stub(self, legacy_path, legacy):
        """Garde l'identité du poste dans config.json pour qu'une version
        antérieure ne régénère pas l'identifiant d'appareil."""
        settings = self.settings()
        stub = {key: settings.get(key, legacy.get(key)) for key in IDENTITY_KEYS
                if settings.get(key, legacy.get(key))}
        if not stub:
            return False
        write_json_atomic(legacy_path, stub)
        return True

    def _move_legacy_files(self):
        # Ce qui réapparaît à l'ancien emplacement après une migration a été
        # écrit plus tard, par une version antérieure (retour arrière du DM) :
        # cet exemplaire remplace celui déjà rangé, comme LEGACY_FRESHER_KEYS.
        actions = []
        for name in LEGACY_MOVED_FILES:
            source = os.path.join(self.user_config_dir, name)
            if not os.path.isfile(source):
                continue
            target = os.path.join(self.dir, name)
            try:
                replaced = os.path.exists(target)
                os.replace(source, target)
                actions.append(f"{name} : " + ("remplacé par l'exemplaire plus récent"
                                               if replaced else "déplacé"))
            except OSError as exc:
                actions.append(f"{name} : échec ({exc})")
        source = os.path.join(self.user_config_dir, LEGACY_PENDING_DIR)
        if os.path.isdir(source):
            target = os.path.join(self.dir, LEGACY_PENDING_DIR)
            try:
                if os.path.exists(target):
                    shutil.rmtree(target)
                shutil.move(source, target)
                actions.append(f"{LEGACY_PENDING_DIR} : rangé")
            except OSError as exc:
                actions.append(f"{LEGACY_PENDING_DIR} : échec ({exc})")
        for name in LEGACY_DELETED_FILES:
            path = os.path.join(self.user_config_dir, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    actions.append(f"{name} : supprimé")
                except OSError as exc:
                    actions.append(f"{name} : échec ({exc})")
        return actions


LEGACY_CONFIG_FILE = "config.json"
IDENTITY_KEYS = ("extensionUUID", "plugin_uuid")
# Ce que config.json porte de propre au poste et que la migration reprend :
# identité, préférences, réglages réseau, et les secrets durables (rangés dans
# le coffre ensuite). Tout le reste vient du DM ou de l'OXT.
LEGACY_KEEP_KEYS = frozenset({
    "extensionUUID", "plugin_uuid", "enrolled",
    "relay_client_id", "relay_client_key", "relay_key_expires_at", "refresh_token",
    "llm_default_models", "llm_tool_mode",
    "proxy_enabled", "proxy_url", "proxy_username", "proxy_password",
    "proxy_allow_insecure_ssl", "ca_bundle_path",
    "config_fetch_timeout_seconds", "keycloak_auth_timeout_seconds",
    "assistant_window_rect", "assistant_append_mode", "assistant_active_tab",
    "edit_dialog_x", "edit_dialog_y", "formula_dialog_x", "formula_dialog_y",
    "calc_input_dialog_x", "calc_input_dialog_y",
})
# Après un retour arrière, la version antérieure se réinscrit et le DM révoque
# l'ancienne paire : ce que config.json porte alors est plus récent que settings.json.
LEGACY_FRESHER_KEYS = frozenset({
    "relay_client_id", "relay_client_key", "relay_key_expires_at", "refresh_token",
    "enrolled",
})
# Palier hors ligne (enabled:false) : l'utilisateur règle lui-même son LLM.
LEGACY_OFFLINE_KEEP_KEYS = frozenset({
    "llm_base_urls", "llm_api_tokens", "authHeaderName", "authHeaderPrefix",
})
# Clés d'une recopie intégrale de la réponse du DM : leur présence dit que
# proxy_allow_insecure_ssl peut venir du gabarit DM et non de l'utilisateur.
RELOAD_FOOTPRINT_KEYS = frozenset({
    "api_type", "is_openwebui", "openai_compatibility", "embdUrl", "llmEndpoint",
})
LEGACY_MOVED_FILES = ("assistant_conversation.json", "prompts_calc.txt", "prompt.txt",
                      "telemetry_queue.json", "secure_bootstrap_state.json")
LEGACY_DELETED_FILES = ("config_cache.json",)
LEGACY_PENDING_DIR = "pending_update"
LEGACY_LOG_MARKERS = ("MainJob.__init__", "DM config fetch", "[llm-auth]", "[palette]",
                      "Config file not found in user profile")

_migration_lock = threading.Lock()


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return value != 0
    return False


def looks_like_our_log(path):
    """~/log.txt est un nom générique : on ne le supprime que s'il porte nos
    marqueurs."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            head = handle.read(65536)
    except OSError:
        return False
    return any(marker in head for marker in LEGACY_LOG_MARKERS)


def wipe(user_config_dir):
    """Efface tout ce que l'extension a écrit dans le profil, puis gèle les
    écritures pour le reste de la session (des minuteries peuvent encore
    tourner après la désinstallation)."""
    freeze()
    if not user_config_dir:
        return
    shutil.rmtree(data_dir(user_config_dir), ignore_errors=True)
    legacy = os.path.join(user_config_dir, LEGACY_CONFIG_FILE)
    if any(key in read_json(legacy) for key in IDENTITY_KEYS):
        try:
            os.remove(legacy)
        except OSError:
            pass
    for name in LEGACY_MOVED_FILES + LEGACY_DELETED_FILES:
        try:
            os.remove(os.path.join(user_config_dir, name))
        except OSError:
            pass
    shutil.rmtree(os.path.join(user_config_dir, LEGACY_PENDING_DIR), ignore_errors=True)
