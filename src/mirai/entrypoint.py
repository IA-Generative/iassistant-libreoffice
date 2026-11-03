import unohelper
import json
import urllib.request
import urllib.parse
import urllib.error
import ssl

# Pre-bind the ExtensionManager singleton on the MAIN thread (module load =
# extension registration). The singleton is `ExtensionManager`
# (com.sun.star.deployment.ExtensionManager) — there is no `theExtensionManager`.
# pyuno's `from com.sun.star… import …` hook is NOT available on background
# threads ("No module named 'com'"), so the update worker thread cannot import
# it itself — it reuses this reference. See _run_install_on_main_thread (which
# uses it when the context lookup of the singleton returns nothing).
try:
    from com.sun.star.deployment import ExtensionManager as _EXT_MGR_SINGLETON
except Exception:
    _EXT_MGR_SINGLETON = None

# Extension identifier (matches oxt/description.xml <identifier>).
_EXTENSION_IDENTIFIER = "fr.gouv.interieur.mirai"

# Feed natif LibreOffice (<update-information>) servi par le DM. Le chemin doit
# rester aligné avec scripts/inject_update_feed.py (FEED_PATH), qui le bake dans
# description.xml.
_UPDATE_FEED_PATH = "/catalog/mirai-libreoffice/update.xml"
_UPDATE_FEED_NS = "http://openoffice.org/extensions/update/2006"

# Route native pilotée : refus mémorisé, attente de l'installation par
# LibreOffice, bornes du déclenchement.
_UPDATE_POSTPONE_SECONDS = 24 * 3600
_NATIVE_INSTALL_WAIT_SECONDS = 900
_NATIVE_POLL_SECONDS = 5
_NATIVE_TRIGGER_TIMEOUT_SECONDS = 30
_NATIVE_MAX_ATTEMPTS = 2
_CLOSE_RETRY_SECONDS = 120
_CLOSE_RETRY_INTERVAL_SECONDS = 3
_CLOSE_ATTEMPT_TIMEOUT_SECONDS = 10
_SLOW_PROBE_LOG_MS = 1000
# Niveaux remontés depuis src/mirai/ pour trouver description.xml (racine du paquet)
_PACKAGE_ROOT_SEARCH_LEVELS = 4
_PROMPT_WIZARD_WAIT_SECONDS = 120
_PROMPT_GRACE_SECONDS = 30
_PROMPT_POLL_SECONDS = 1
_MAIN_THREAD_TIMEOUT_AFTER_START = "timeout after start"
_MAIN_THREAD_VETOED = "vetoed"
# Erreurs de PLANIFICATION de _run_on_main_thread (le thread principal n'a pas
# pris le callback), par opposition à une exception remontée par l'action.
_MAIN_THREAD_CALLBACK_UNAVAILABLE = "main-thread callback unavailable"
_MAIN_THREAD_ASYNC_UNAVAILABLE = "AsyncCallback unavailable"
_MAIN_THREAD_SCHEDULE_FAILED = "schedule failed: "
_CLOSE_USER_REFUSAL_SECONDS = 1.0

# Interfaces UNO pré-bindées au chargement du module (= thread principal), pour le
# même motif que _EXT_MGR_SINGLETON : le worker d'update ne peut pas faire de
# `from com.sun.star… import …` lui-même ("No module named 'com'"). Elles servent
# au marshaling vers le main thread (AsyncCallback) et au XCommandEnvironment
# silencieux de l'installation in-process.
try:
    from com.sun.star.awt import XCallback as _XCALLBACK_IFACE
except Exception:
    _XCALLBACK_IFACE = None
try:
    from com.sun.star.ucb import XCommandEnvironment as _XCMDENV_IFACE
    from com.sun.star.task import XInteractionHandler as _XINTERACTION_IFACE
except Exception:
    _XCMDENV_IFACE = None
    _XINTERACTION_IFACE = None

if _XCALLBACK_IFACE is not None:
    class _MainThreadCallback(unohelper.Base, _XCALLBACK_IFACE):
        """Exécute un callable sur le thread PRINCIPAL de LibreOffice, planifié via
        com.sun.star.awt.AsyncCallback. Classe construite au chargement du module :
        le worker d'update n'a qu'à l'instancier (aucun import UNO côté worker)."""

        def __init__(self, fn):
            self._fn = fn

        def notify(self, _data):
            try:
                self._fn()
            except Exception as exc:
                log_to_file(f"_MainThreadCallback: callable raised: {exc}")
else:
    _MainThreadCallback = None

if _XINTERACTION_IFACE is not None and _XCMDENV_IFACE is not None:
    class _SilentInteractionHandler(unohelper.Base, _XINTERACTION_IFACE):
        """Approuve les interactions de déploiement (VersionException lors du
        remplacement d'une extension de même identifiant, licence déjà
        supprimée, …) en sélectionnant une continuation « approve »."""

        def handle(self, request):
            try:
                conts = request.getContinuations()
            except Exception:
                conts = ()
            chosen = None
            for cont in conts or ():
                name = type(cont).__name__.lower()
                if "approve" in name or "retry" in name or "resolved" in name:
                    chosen = cont
                    break
            try:
                (chosen or (conts[0] if conts else None)).select()
            except Exception:
                pass

    class _SilentCommandEnv(unohelper.Base, _XCMDENV_IFACE):
        def __init__(self, handler):
            self._handler = handler

        def getInteractionHandler(self):
            return self._handler

        def getProgressHandler(self):
            return None
else:
    _SilentInteractionHandler = None
    _SilentCommandEnv = None

try:
    from com.sun.star.task import XJobExecutor, XJob
    from com.sun.star.awt import MessageBoxButtons as MSG_BUTTONS
    from com.sun.star.awt import XActionListener, XItemListener, XMouseListener, XTopWindowListener
    from com.sun.star.beans import PropertyValue
except ImportError:
    # Running outside LibreOffice (e.g. unopkg install) — provide safe stubs
    class _S1: pass
    class _S2: pass
    class _S3: pass
    class _S4: pass
    class _S5: pass
    class _S7: pass
    class _S8: pass
    XJobExecutor = _S1
    XJob = _S2
    MSG_BUTTONS = None
    XActionListener = _S3
    XItemListener = _S4
    XMouseListener = _S5
    XTopWindowListener = _S7
    PropertyValue = _S8
import uno
import os
import logging
import re
import uuid
import time
import base64
import hashlib
import html
import threading
import socket
import platform
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import credentials, feed_rewrite, local_config, log_setup
from .i18n import t as _t
from .i18n import (
    get_locale as _i18n_get_locale,
    set_locale as _i18n_set_locale,
)
from .shell import runtime as _shell_runtime
from .shell.open_url import open_url
from .shell.settings_result import apply_settings_result
from .shell.ui_locale import extension_locale as _extension_locale
from .shell.uninstall import UninstallWatch


PLUGIN_NAME = "MIrAI-LibreOffice"
_DEFAULT_USER_AGENT = PLUGIN_NAME
_current_user_agent = _DEFAULT_USER_AGENT


def build_user_agent(plugin_version="", lo_version=""):
    """Build a User-Agent string: MIrAI-LibreOffice/<plugin_ver> LibreOffice/<lo_ver>."""
    parts = [PLUGIN_NAME]
    if plugin_version:
        parts[0] = f"{PLUGIN_NAME}/{plugin_version}"
    if lo_version:
        parts.append(f"LibreOffice/{lo_version}")
    return " ".join(parts)


def set_user_agent(plugin_version="", lo_version=""):
    """Set the module-level User-Agent used by all HTTP helpers."""
    global _current_user_agent
    _current_user_agent = build_user_agent(plugin_version, lo_version)


def get_user_agent():
    """Return the current User-Agent string."""
    return _current_user_agent

_UI = {
    "bg":              0xFFFFFF,   # white background
    "bg_section":      0xF6F6F6,   # light-grey section background
    "bg_input":        0xFCFCFC,   # very light input background
    "text":            0x161616,   # almost-black text
    "text_secondary":  0x666666,   # secondary grey text
    "text_light":      0x929292,   # light hint text
    "border":          0xDDDDDD,   # subtle border grey
    "primary":         0x000091,   # Bleu France
    "primary_hover":   0x1212FF,   # lighter blue
    "success":         0x18753C,   # DSFR success green
    "error":           0xCE0500,   # DSFR error red
    "info":            0x0063CB,   # DSFR info blue
    "status_ok":       0x18753C,   # connected
    "status_warn":     0xB34000,   # anonymous ok
    "status_neutral":  0x929292,   # not tested
    "status_fail":     0xCE0500,   # not accessible
    "btn_primary_bg":  0x000091,   # primary button bg
    "btn_primary_fg":  0xFFFFFF,   # primary button text
    "btn_secondary_bg": 0xF6F6F6,  # secondary button bg
    "btn_secondary_fg": 0x161616,  # secondary button text
    "btn_danger_bg":   0xCE0500,   # danger button bg
    "btn_danger_fg":   0xFFFFFF,   # danger button text
    "separator":       0xE5E5E5,   # separator lines
    "font_title":      14,         # title font size
    "font_section":    11,         # section header font size
    "font_label":      10,         # label font size
    "font_body":       9,          # body text font size
    "font_small":      8,          # small caption font size
}

_PROXY_DISABLED = {
    "enabled": False,
    "proxy_url": "",
    "username": "",
    "password": "",
    "allow_insecure_ssl": False,
}

def _with_user_agent(headers=None):
    result = dict(headers) if headers else {}
    if "User-Agent" not in result:
        result["User-Agent"] = get_user_agent()
    return result

def _redact_header_value(name, value):
    key = str(name or "").strip().lower()
    if key in ("authorization", "x-api-key", "api-key", "proxy-authorization", "x-relay-key"):
        return "<redacted>"
    return value

def _curl_headers_for_log(headers):
    parts = []
    for key, value in (headers or {}).items():
        safe_value = _redact_header_value(key, value)
        parts.append(f"-H '{key}: {safe_value}'")
    return " ".join(parts)

def log_to_file(message):
    """Journalise sans jamais lever : une panne de journalisation (handler
    fermé, disque plein) ne doit pas interrompre un chemin critique."""
    try:
        logging.info(message)
    except Exception:
        pass


credentials.set_log(log_to_file)


def is_main_thread():
    """Vrai si l'appelant est le thread principal du processus."""
    return threading.current_thread() is threading.main_thread()


def pump_events(toolkit):
    """Pompe la file d'événements VCL — UNIQUEMENT depuis le thread principal.

    `processEventsToIdle()` appelé depuis un thread de fond ne « ralentit » pas
    LibreOffice : il l'ABORTE. La séquence observée est toujours la même —
    `DispatchUserEvents` → `std::terminate()` → le gestionnaire de signal tente
    d'ouvrir la boîte de récupération d'urgence, qui réclame le SolarMutex que
    le thread fautif détient encore. Résultat : interblocage total, le thread
    principal reste figé dans `SalYieldMutex::doAcquire` et l'application ne
    répond plus à un seul clic.

    Hors thread principal, on ne pompe donc pas — on trace et on rend la main.
    L'appelant n'a rien à changer : c'est un no-op sûr, jamais un abort.
    """
    if toolkit is None:
        return False
    if not is_main_thread():
        log_to_file(
            "[threading] processEventsToIdle ignoré : appel depuis "
            f"{threading.current_thread().name!r} et non le thread principal"
        )
        return False
    try:
        toolkit.processEventsToIdle()
        return True
    except Exception:
        return False


def generate_trace_id():
    """Generate a random 16-byte trace ID in hexadecimal format."""
    return uuid.uuid4().hex[:32]


def generate_span_id():
    """Generate a random 8-byte span ID in hexadecimal format."""
    return uuid.uuid4().hex[:16]


def otel_attributes(mapping):
    """Convertit un dict d'attributs au format OTLP/JSON, EN CONSERVANT LES TYPES.

    `bool` est testé AVANT `int` : en Python, `True` est un entier, et l'ordre
    inverse enverrait `intValue: "1"` pour un drapeau.
    Les entiers voyagent en chaîne : OTLP/JSON code les int64 ainsi, pour ne pas
    perdre de précision au passage par un flottant JavaScript.
    """
    out = []
    for key, value in (mapping or {}).items():
        if isinstance(value, bool):
            typed = {"boolValue": value}
        elif isinstance(value, int):
            typed = {"intValue": str(value)}
        elif isinstance(value, float):
            typed = {"doubleValue": value}
        else:
            typed = {"stringValue": str(value)}
        out.append({"key": key, "value": typed})
    return out


def send_telemetry_trace_async(config, span_name, attributes=None):
    """
    Send OpenTelemetry trace asynchronously in a separate thread.
    This function returns immediately and does not block the extension execution.

    Args:
        config: Configuration object with telemetry settings
        span_name: Name of the span (e.g., "ExtendSelection", "AnalyzeRange")
        attributes: Optional dictionary of additional attributes
    """
    thread = threading.Thread(
        target=_send_telemetry_trace_impl,
        args=(config, span_name, attributes),
        daemon=True  # Daemon thread won't prevent the program from exiting
    )
    thread.start()
    log_to_file(f"Telemetry trace '{span_name}' scheduled asynchronously")


def _send_telemetry_trace_impl(config, span_name, attributes=None):
    """
    Internal implementation of telemetry trace sending.
    This runs in a separate thread to avoid blocking the extension.

    Args:
        config: MainJob object with get_config() method
        span_name: Name of the span (e.g., "ExtendSelection", "AnalyzeRange")
        attributes: Optional dictionary of additional attributes
    """
    endpoint = "unknown"  # Initialize endpoint for error handling
    try:
        telemetry_enabled = config.get_config("telemetryEnabled", True)
        if not telemetry_enabled:
            log_to_file("Telemetry disabled, skipping trace")
            return

        endpoint = config.get_config("telemetryEndpoint", None)
        auth_type = config.get_config("telemetryAuthorizationType", None)
        auth_key = config.get_config("telemetryKey", None)
        log_json = config.get_config("telemetrylogJson", None)

        # Generate or retrieve extension UUID
        extension_uuid = config.get_config("extensionUUID", "")
        if not extension_uuid:
            extension_uuid = str(uuid.uuid4())
            config.set_config("extensionUUID", extension_uuid)
            log_to_file(f"Generated new extension UUID: {extension_uuid}")

        # Generate trace and span IDs
        trace_id = generate_trace_id()
        span_id = generate_span_id()

        # Get current timestamp in nanoseconds
        timestamp_ns = int(time.time() * 1e9)

        # Build span attributes
        span_attributes = {
            "extension.uuid": extension_uuid,
            "extension.name": "mirai",
            "extension.version": "1.0.0"
        }

        if attributes:
            span_attributes.update(attributes)

        encoded_attributes = otel_attributes(span_attributes)

        # Build OpenTelemetry JSON payload
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "mirai-libreoffice"}},
                            {"key": "service.version", "value": {"stringValue": "1.0.0"}},
                            {"key": "extension.uuid", "value": {"stringValue": extension_uuid}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "mirai-extension",
                                "version": "1.0.0"
                            },
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": span_id,
                                    "name": span_name,
                                    "kind": 1,  # SPAN_KIND_INTERNAL
                                    "startTimeUnixNano": str(timestamp_ns),
                                    "endTimeUnixNano": str(timestamp_ns + 1000000),  # Add 1ms duration
                                    "attributes": encoded_attributes,
                                    "status": {"code": 1}  # STATUS_CODE_OK
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        if log_json:
            log_to_file("=== Telemetry Request ===")
            log_to_file(f"URL: {endpoint}")
            log_to_file("Method: POST")
            log_to_file(f"Span Name: {span_name}")
            log_to_file(f"Trace ID: {trace_id}")
            log_to_file(f"Span ID: {span_id}")
            log_to_file(f"Payload: {json.dumps(payload, indent=2)}")

        # Send the request
        json_data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(endpoint, data=json_data, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', get_user_agent())
        req.add_header('X-Client-UUID', extension_uuid)

        # Add authentication header
        if auth_key:
            if auth_type == "Basic":
                req.add_header('Authorization', f'Basic {auth_key}')
            elif auth_type == "Bearer":
                req.add_header('Authorization', f'Bearer {auth_key}')

        # Log request headers
        if log_json:
            log_to_file("=== Request Headers ===")
            for header_name, header_value in req.headers.items():
                if header_name.lower() == 'authorization':
                    log_to_file(f"{header_name}: <redacted>")
                else:
                    log_to_file(f"{header_name}: {header_value}")
            log_to_file(f"Content-Length: {len(json_data)}")
            log_to_file("===")

        ssl_context = config.get_ssl_context()

        response = config._urlopen(req, context=ssl_context, timeout=5)
        with response as response:
            response_status = response.status
            response_headers = dict(response.headers)
            response_body = response.read().decode('utf-8') if response.readable() else ""

            if log_json:
                log_to_file("=== Telemetry Response ===")
                log_to_file(f"Status: {response_status}")
                log_to_file(f"Headers: {json.dumps(response_headers, indent=2)}")
                log_to_file(f"Body: {response_body if response_body else '(empty)'}")
                log_to_file("=== End Telemetry Response ===")

            log_to_file(f"Telemetry trace sent successfully: {span_name}, status: {response_status}")

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if hasattr(e, 'read') else ""
        log_to_file("=== Telemetry HTTP Error ===")
        log_to_file(f"URL: {endpoint}")
        log_to_file(f"Status: {e.code}")
        log_to_file(f"Reason: {e.reason}")
        log_to_file(f"Headers: {dict(e.headers) if hasattr(e, 'headers') else 'N/A'}")
        log_to_file(f"Body length: {len(error_body)}")
        log_to_file("=== End Telemetry Error ===")
    except Exception as e:
        log_to_file("=== Telemetry Exception ===")
        log_to_file(f"URL: {endpoint}")
        log_to_file(f"Error: {str(e)}")
        log_to_file(f"Type: {type(e).__name__}")
        log_to_file("=== End Telemetry Exception ===")


class _CallbackServer(ThreadingHTTPServer):
    # Sous Windows, SO_REUSEADDR laisse un autre processus écouter sur le même port
    # et recevoir le callback à notre place : le bind doit échouer.
    allow_reuse_address = platform.system() != "Windows"


def _render_callback_page():
    """Page shown in the browser once the OAuth redirect has reached the extension."""
    def text(key):
        return html.escape(_t(key), quote=False)

    return f"""<!doctype html>
<html lang="{_i18n_get_locale()}">
  <head>
    <meta charset="utf-8"/>
    <title>{text("callback.title")}</title>
    <style>
      body {{ font-family: Arial, sans-serif; margin: 28px; color: #222; background: #f7f8fb; }}
      .card {{ background: #fff; border: 1px solid #e3e6ef; border-radius: 10px; padding: 18px 20px; max-width: 560px; box-shadow: 0 2px 10px rgba(0,0,0,0.04); }}
      .muted {{ color: #666; }}
      .ok {{ display: inline-block; margin-top: 6px; padding: 6px 10px; background: #e8f5e9; color: #1b5e20; border-radius: 6px; font-weight: 600; }}
      .small {{ font-size: 12px; color: #778; margin-top: 10px; }}
    </style>
  </head>
  <body>
    <div class="card">
      <h2>{text("callback.heading")}</h2>
      <div class="ok">{text("callback.badge")}</div>
      <p>{text("callback.close_tab")}</p>
      <p class="muted">{text("callback.if_stuck")}</p>
      <div class="small">{text("callback.no_action")}</div>
    </div>
  </body>
</html>
"""


class MainJob(unohelper.Base, XJobExecutor, XJob):
    # Méthodes par lesquelles __init__ lance un thread ou un timer ; la relance
    # de /config part d'un getter (_ensure_extension_uuid → get_config).
    STARTUP_TASKS = (
        "_schedule_config_refresh",
        "_send_telemetry",
        "_ensure_device_management_state_async",
        "_schedule_update_reconciliation",
        "_schedule_native_feed_check",
        "_schedule_feed_rewrite",
        "_schedule_enrollment_check",
    )
    runtime = property(lambda self: _shell_runtime.get())
    _EXTENSION_MANAGER = "/singletons/com.sun.star.deployment.ExtensionManager"
    # Caches dérivés d'une version, d'un modèle ou d'un DM : jamais conservés
    # d'une installation à l'autre.
    _DERIVED_CACHE_KEYS = ("assistant_model_capabilities", "llm_tool_mode_detected",
                           "calc_transform_suggestions_cache", "last_bootstrap_url")
    # Émis par un DM précis : sans valeur pour un autre environnement.
    _ENVIRONMENT_BOUND_KEYS = ("enrolled", "relay_client_id", "relay_client_key",
                               "relay_key_expires_at", "refresh_token",
                               "access_token", "access_token_expires_at")

    def __init__(self, ctx):
        log_to_file("=== MainJob.__init__ called ===")
        self.ctx = ctx
        self.config_cache = None
        self.config_loaded_at = 0
        self.config_ttl = 300
        self._config_last_failure_at = 0
        self._config_failure_backoff = 30
        # Bootstrap DM base URL that last answered (failover winner across bootstrap_urls)
        self._resolved_bootstrap_url = ""
        self._models_cache = None
        self._models_cache_key = None
        self._models_cache_loaded_at = 0
        self._models_cache_ttl = 60
        self._fetching_config = False
        self._config_refresh_lock = threading.RLock()
        self._config_refresh_in_progress = False
        self._config_refresh_last_started_at = 0
        self._config_async_min_interval = 20
        self._auth_prompt_lock = threading.Lock()
        self._auth_prompt_in_progress = False
        self._auth_prompted_at = 0
        # Récupération d'auth LLM : ré-enrôlement de fond (creds relay manquants
        # ou révoqués) et reprise après un 401 du proxy DM. Les deux sont
        # backoffées — un DM volontairement sans relais ne doit pas être matraqué.
        self._relay_recovery_lock = threading.Lock()
        self._relay_recovery_in_progress = False
        self._relay_recovery_last_at = 0
        self._llm_auth_recovery_lock = threading.Lock()
        self._llm_auth_recovery_last_at = 0
        self._trigger_source = "auto"
        self._last_loaded_ca_bundle = None
        self._last_ca_bundle_error = None
        self._last_logged_ca_bundle_error = None
        # Update & feature toggling (schema_version 2)
        self._features_cache = {}
        # handling different situations (inside LibreOffice or other process)
        try:
            self.sm = ctx.getServiceManager()
            self.desktop = XSCRIPTCONTEXT.getDesktop()
            self.document = XSCRIPTCONTEXT.getDocument()
            log_to_file("MainJob initialized with XSCRIPTCONTEXT")
        except NameError:
            self.sm = ctx.ServiceManager
            self.desktop = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            log_to_file("MainJob initialized without XSCRIPTCONTEXT")

        try:
            self._prepare_local_storage()
        except Exception as e:
            log_to_file(f"Local storage preparation failed: {str(e)}")

        # The extension speaks LibreOffice's own UI language, like its menu
        # entries (oxt/Addons.xcu), and English when it does not offer it.
        try:
            resolved_language = _i18n_set_locale(_extension_locale(self.ctx))
            log_to_file(f"UI language set to: {resolved_language}")
        except Exception as e:
            log_to_file(f"Failed to resolve UI language: {str(e)}")

        # Initialise User-Agent with real plugin + LibreOffice versions
        try:
            set_user_agent(self._get_extension_version(), self._get_lo_version())
            log_to_file(f"User-Agent set to: {get_user_agent()}")
        except Exception as e:
            log_to_file(f"Failed to set User-Agent: {str(e)}")

        # Send telemetry trace on extension load
        try:
            self._ensure_extension_uuid()
            self._ensure_plugin_uuid()
            self._send_telemetry("ExtensionLoaded", {
                "event.type": "extension_loaded",
                "extension.context": "libreoffice_writer",
            })
        except Exception as e:
            log_to_file(f"Failed to send extension load telemetry: {str(e)}")

        try:
            self._ensure_device_management_state_async()
        except Exception as e:
            log_to_file(f"Failed to initialize device management: {str(e)}")

        # Clôt la mise à jour du cycle précédent (rapport « installed » véridique
        # au DM une fois la nouvelle version active, purge de pending_update).
        try:
            self._schedule_update_reconciliation()
        except Exception as e:
            log_to_file(f"Failed to schedule update reconciliation: {str(e)}")

        # Diagnostic passif du feed natif : valide proxy/TLS/GPO de la pile HTTP
        # de LibreOffice sur la flotte et détecte un feed DM absent avant
        # d'appuyer le déploiement large dessus.
        try:
            self._schedule_native_feed_check()
        except Exception as e:
            log_to_file(f"Failed to schedule native feed check: {str(e)}")

        # Adresse du feed natif = ?version=<version installée> tant qu'aucune
        # directive ne dit autre chose : un nouvel OXT arrive avec l'adresse nue
        # du build (version générale), à reprendre avant toute vérification.
        try:
            self._schedule_feed_rewrite()
        except Exception as e:
            log_to_file(f"Failed to schedule feed rewrite: {str(e)}")

        # Auto-launch enrollment wizard on first use (deferred to let UI init)
        try:
            self._schedule_enrollment_check()
        except Exception as e:
            log_to_file(f"Failed to schedule enrollment check: {str(e)}")

    def _log(self, message):
        log_to_file(message)

    # Condensed action names for telemetry — appears as plugin.action attribute
    _ACTION_NAMES = {
        "ExtensionLoaded": "launch",
        "ExtensionUpdated": "update",
        # Étapes du flux de MAJ : mesurent le taux de succès PAR ÉTAPE sur la
        # flotte (staged → accepted → installed confirmé).
        "UpdateStaged": "update",
        "UpdateAccepted": "update",
        "UpdatePostponed": "update",
        "UpdateInstalledPendingRestart": "update",
        "UpdateInstallFailed": "update",
        "NativeFeedCheck": "update",
        "FeedRewrite": "update",
        "UpdateNativeDialogShown": "update",
        "UpdateCloseDeferred": "update",
        "ExtendSelection": "extend",
        "ResizeSelection": "resize",
        "SummarizeSelection": "summarize",
        "SimplifySelection": "simplify",
        "TransformToColumn": "transform",
        "AnalyzeRange": "analyze",
        "OpenDocumentation": "docs",
        "OpenSettings": "settings",
        "AboutDialog": "about",
        "EnrollSuccess": "enroll.ok",
        "EnrollFailed": "enroll.fail",
        "LlmRelayError": "llm.error",
        "ConfigWaitAtTrigger": "config.wait",
        "ActionUnhandled": "dispatch.unhandled",
    }

    def _send_telemetry(self, span_name, attributes=None):
        attrs = dict(attributes or {})
        attrs.setdefault("plugin.action", self._ACTION_NAMES.get(span_name, span_name))
        attrs.setdefault("trigger.source", self._trigger_source)
        send_telemetry_trace_async(self, span_name, attrs)

    # Anti-tempête : au plus un événement LlmRelayError par code d'erreur et par
    # fenêtre de 60 s — un utilisateur au quota qui insiste ne doit pas générer
    # une rafale de télémétrie (contrat DM, protocole § 8 bis).
    _LLM_ERROR_DEDUP_SECONDS = 60

    @staticmethod
    def _parse_llm_error(status_code, body, headers=None):
        """Extrait (error_code, retry_after) d'une réponse d'erreur du relais LLM.

        Corps attendu (proxy DM /llm/v1) : {"error": {"code", "type", ...}} avec,
        pour le 429, "retry_after" (secondes) — sinon repli sur l'en-tête
        Retry-After, puis sur un code générique http_<statut>.
        """
        error_code = ""
        retry_after = None
        try:
            data = json.loads(body) if body else {}
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    error_code = str(err.get("code") or err.get("type") or "").strip()
                retry_after = data.get("retry_after")
        except Exception:
            pass
        if retry_after is None and headers is not None:
            try:
                retry_after = headers.get("Retry-After")
            except Exception:
                retry_after = None
        if not error_code:
            error_code = f"http_{int(status_code or 0)}"
        return error_code, retry_after

    def _send_llm_relay_error(self, status_code, error_code, retry_after=None,
                              request_id="", endpoint="chat/completions",
                              will_retry=False):
        """Journalisation fonctionnelle des erreurs du relais LLM (429/401/403/5xx).

        Vue « parc côté client » complémentaire de l'audit serveur du proxy —
        `llm.request_id` (recopie de X-Request-Id) est la clé de corrélation de
        bout en bout. Jamais de contenu de prompt ni de réponse. Contrat :
        device-management, docs/plugin-developer/…-update-features.md § 8 bis.
        """
        try:
            now = time.time()
            key = str(error_code or "unknown")
            if now - self.runtime.llm_error_last_sent.get(key, 0) < self._LLM_ERROR_DEDUP_SECONDS:
                return
            self.runtime.llm_error_last_sent[key] = now
            attrs = {
                "llm.status_code": int(status_code or 0),
                "llm.error_code": key,
                "llm.endpoint": str(endpoint or "chat/completions"),
                "llm.model": str(self.get_config("llm_default_models", "") or ""),
                "llm.will_retry": bool(will_retry),
            }
            if retry_after is not None:
                try:
                    attrs["llm.retry_after_s"] = int(retry_after)
                except (TypeError, ValueError):
                    pass
            if request_id:
                attrs["llm.request_id"] = str(request_id)[:64]
            self._send_telemetry("LlmRelayError", attrs)
        except Exception as e:
            log_to_file(f"Failed to send LlmRelayError telemetry: {str(e)}")

    def _wait_for_config(self, action):
        """Attend une configuration en vol (au plus 15 s) et renvoie la durée
        d'attente en millisecondes (0 = aucune attente) ; émet ConfigWaitAtTrigger."""
        if not (self._fetching_config and not self.config_cache):
            return 0
        log_to_file(f"trigger: waiting for config fetch to complete before {action}")
        started = time.time()
        while self._fetching_config and time.time() - started < 15:
            time.sleep(0.3)
        waited_ms = int((time.time() - started) * 1000)
        available = bool(self.config_cache)
        log_to_file("trigger: config now available" if available
                    else "trigger: config still unavailable after wait")
        self._send_telemetry("ConfigWaitAtTrigger", {
            "config.wait_ms": waited_ms,
            "config.available": available,
            "action": str(action),
        })
        return waited_ms

    def _report_unhandled_action(self, action, model):
        """Émet ActionUnhandled une fois par nom d'action et par session."""
        try:
            if action in self.runtime.unhandled_reported:
                return
            self.runtime.unhandled_reported.add(action)
            self._send_telemetry("ActionUnhandled", {
                "action": str(action),
                "document": type(model).__name__,
            })
        except Exception as exc:
            log_to_file(f"Failed to send ActionUnhandled telemetry: {str(exc)}")

    def _get_user_config_dir(self):
        path_settings = self.sm.createInstanceWithContext('com.sun.star.util.PathSettings', self.ctx)
        user_config_path = getattr(path_settings, "UserConfig", None)
        # PathSettings indisponible (ex. contexte de test mocké, ou état LO
        # dégradé) : pas de chemin exploitable -> on évite d'écrire dans un
        # dossier fantôme (repr de mock) ou dans le cwd.
        if not isinstance(user_config_path, str):
            return ""
        if user_config_path.startswith('file://'):
            user_config_path = str(uno.fileUrlToSystemPath(user_config_path))
        return user_config_path

    def _local_config(self):
        if getattr(self, "_local_cfg", None) is None:
            self._local_cfg = local_config.LocalConfig(
                self._get_user_config_dir(),
                local_config.package_config_candidates(os.path.dirname(os.path.abspath(__file__))),
            )
        return self._local_cfg

    def _data_dir(self):
        return self._local_config().dir

    def _pending_update_dir(self):
        base = self._data_dir()
        return os.path.join(base, "pending_update") if base else ""

    def _prepare_local_storage(self):
        """Une fois par processus, retentée par le MainJob suivant tant qu'elle
        échoue : journal, migration du dossier legacy, secrets hors de
        settings.json, empreinte d'installation, écoute de désinstallation."""
        with self.runtime.storage_lock:
            data_dir = self._data_dir()
            if data_dir and not self.runtime.storage_ready:
                log_setup.install(data_dir)
                for action in self._local_config().migrate_legacy(local_config.legacy_home_dir()):
                    log_to_file(f"[stockage] {action}")
                self._move_secrets_out_of_settings()
                self._local_config().record_install(
                    self._get_extension_version(), self._package_root_dir() or "",
                    self._apply_install_changes)
                self.runtime.storage_ready = True
        try:
            UninstallWatch(self.ctx, self.runtime, _EXTENSION_IDENTIFIER,
                           self._local_config().user_config_dir).register(self.desktop)
        except Exception as exc:
            log_to_file(f"[désinstallation] écoute impossible : {exc}")

    def _credential_scope(self):
        return self._local_config().transport_scope()

    def _store_secret(self, key, value, scope):
        """Coffre de l'OS ; s'il refuse l'écriture (trousseau désynchronisé,
        persistance plafonnée), le secret reste en mémoire pour la session au
        lieu d'être perdu."""
        if credentials.set_secret(key, value, scope):
            credentials.forget(key)
            return True
        credentials.remember(key, value)
        return False

    def _move_secrets_out_of_settings(self):
        """Secrets restés dans settings.json (migration, rollback) → coffre ou
        mémoire ; jetons courts abandonnés. Sans coffre (Linux), ils ne vivent
        plus que le temps de la session.

        Un secret n'y revient que par un écrivain plus récent (version antérieure
        après un retour arrière, reprise par la migration) : il remplace donc
        celui du coffre, une seule fois par processus."""
        cfg = self._local_config()
        settings = cfg.settings()
        scope = self._credential_scope()
        stored = []
        refused = []
        for key in credentials.STORED_KEYS:
            value = settings.get(key)
            if value in (None, "") or self.runtime.secrets_copied:
                continue
            if self._store_secret(key, value, scope):
                stored.append(key)
            else:
                refused.append(key)
        self.runtime.secrets_copied = True
        stale = [key for key in settings
                 if key in credentials.STORED_KEYS or key in credentials.MEMORY_KEYS
                 or key in local_config.SECRET_DM_KEYS or key == "llmTokenExpiresAt"]
        if stale:
            cfg.update(remove=stale)
            where = ("dans le coffre" if getattr(credentials.store(), "persistent", False)
                     else "en mémoire pour la session")
            log_to_file(f"[secrets] {len(stored)} secret(s) rangé(s) {where}, "
                        f"{len(stale)} clé(s) retirée(s) des réglages"
                        + (f" ; refusés par le coffre, gardés en mémoire : {', '.join(refused)}"
                           if refused else ""))

    def _apply_install_changes(self, changes):
        changes = set(changes or ())
        if not changes - {"first_run"}:
            return
        remove = list(self._DERIVED_CACHE_KEYS)
        if "transport" in changes:
            remove += list(self._ENVIRONMENT_BOUND_KEYS)
            for key in credentials.SCOPED_KEYS:
                credentials.delete_secret(key)
                credentials.forget(key)
            credentials.forget("access_token")
        cfg = self._local_config()
        cfg.update(remove=remove)
        cfg.delete_snapshot()
        log_to_file(f"[stockage] installation changée ({', '.join(sorted(changes))}) : "
                    "caches effacés"
                    + (", identifiants de l'ancien environnement effacés"
                       if "transport" in changes else ""))

    def _ensure_extension_uuid(self):
        """Ensure extension has a unique UUID, generate if missing."""
        extension_uuid = self.get_config("extensionUUID", "")
        if not extension_uuid:
            extension_uuid = str(uuid.uuid4())
            self.set_config("extensionUUID", extension_uuid)
            log_to_file(f"Generated new extension UUID: {extension_uuid}")
        return extension_uuid

    def _ensure_plugin_uuid(self):
        plugin_uuid = str(self._get_config_from_file("plugin_uuid", "") or "").strip()
        if plugin_uuid:
            return plugin_uuid
        extension_uuid = str(self._get_config_from_file("extensionUUID", "") or "").strip()
        if not extension_uuid:
            extension_uuid = str(uuid.uuid4())
            self.set_config("extensionUUID", extension_uuid)
        self.set_config("plugin_uuid", extension_uuid)
        return extension_uuid

    def _get_telemetry_defaults(self):
        """Return default values for telemetry configuration."""
        return {
            "telemetryEnabled": True,
            "telemetryEndpoint": "https://traces.cpin.numerique-interieur.com/v1/traces",
            "telemetryAuthorizationType": "Basic",
            "telemetryKey": "",
            "telemetrylogJson": False,
        }

    def _get_config_from_file(self, key, default):
        """Valeur locale de `key` : transport de l'OXT, réglages de l'utilisateur,
        dernier instantané du DM, défauts de l'OXT (cf. LocalConfig.get)."""
        if key in credentials.STORED_KEYS:
            # La mémoire ne porte qu'une écriture refusée par le coffre : plus
            # récente que ce que le coffre garde encore.
            return (credentials.recall(key)
                    or credentials.get_secret(key, self._credential_scope()) or default)
        if key in credentials.MEMORY_KEYS:
            return credentials.recall(key) or default
        return self._local_config().get(key, default)

    def _device_management_enabled(self):
        return self._as_bool(self._get_config_from_file("enabled", False))

    def _schedule_config_refresh(self, force=False, reason="background"):
        if not self._device_management_enabled():
            return False
        now = time.time()
        with self._config_refresh_lock:
            if self._config_refresh_in_progress:
                return False
            if not force and (now - self._config_refresh_last_started_at) < self._config_async_min_interval:
                return False
            self._config_refresh_in_progress = True
            self._config_refresh_last_started_at = now

        def _worker():
            try:
                self._fetch_config(force=force)
            except Exception as exc:
                log_to_file(f"DM config async refresh failed ({reason}): {str(exc)}")
            finally:
                with self._config_refresh_lock:
                    self._config_refresh_in_progress = False

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def _bootstrap_urls(self):
        """Ordered list of DM bootstrap base URLs (failover).

        Reads the `bootstrap_urls` list when present; otherwise falls back to the
        legacy single `bootstrap_url` string. Empty entries are dropped.
        """
        result = []
        urls = self._get_config_from_file("bootstrap_urls", None)
        if isinstance(urls, (list, tuple)):
            for item in urls:
                value = str(item or "").strip()
                if value and value not in result:
                    result.append(value)
        if not result:
            legacy = str(self._get_config_from_file("bootstrap_url", "") or "").strip()
            if legacy:
                result.append(legacy)
        return result

    def _failover_ordered_urls(self):
        """Bootstrap URLs with the DM that last answered tried first.

        Perf: avoids re-hitting a dead earlier URL (e.g. an unreachable DGX from
        an OCP-only host) and paying its full timeout on every config fetch. The
        winner is remembered in-memory (_resolved_bootstrap_url) and persisted
        (last_bootstrap_url) so it survives the per-action re-instantiation of
        MainJob.
        """
        urls = self._bootstrap_urls()
        if len(urls) < 2:
            return urls
        preferred = str(
            self._resolved_bootstrap_url
            or self._get_config_from_file("last_bootstrap_url", "")
            or ""
        ).strip().rstrip("/")
        if not preferred:
            return urls
        front = [u for u in urls if u.rstrip("/") == preferred]
        if not front:
            return urls
        return front + [u for u in urls if u.rstrip("/") != preferred]

    def _active_bootstrap_url(self):
        """The DM base URL that last answered (failover winner), else the last-good
        persisted one, else the first configured. Telemetry / enroll / update must
        target the same DM that served the config, so they read this rather than the
        raw key — otherwise a fresh MainJob (or a config read from cache) would fall
        back to `urls[0]`, which may be an internal-only DM unreachable from here.
        """
        resolved = str(self._resolved_bootstrap_url or "").strip()
        if resolved:
            return resolved
        persisted = str(self._get_config_from_file("last_bootstrap_url", "") or "").strip()
        urls = self._bootstrap_urls()
        if persisted and persisted.rstrip("/") in {url.rstrip("/") for url in urls}:
            return persisted
        return urls[0] if urls else ""

    def _is_insecure_bootstrap_url(self, url):
        """True when `url`'s host is declared in `bootstrap_insecure_urls`.

        This is the per-URL `-k` allowlist: an internal cluster route (e.g. an
        OCP bootstrap behind a private CA) can skip cert verification while the
        public DMs stay verified. Matching is by hostname so it holds whether we
        pass a bare base URL or a full request URL (base + config_path).
        """
        patterns = self._get_config_from_file("bootstrap_insecure_urls", None)
        if not isinstance(patterns, (list, tuple)):
            return False
        try:
            target_host = (urllib.parse.urlsplit(str(url or "").strip()).hostname or "").lower()
        except Exception:
            target_host = ""
        if not target_host:
            return False
        for item in patterns:
            value = str(item or "").strip()
            if not value:
                continue
            host = ""
            try:
                host = urllib.parse.urlsplit(value).hostname or ""
            except Exception:
                host = ""
            if not host:
                # Tolerate bare host entries written without a scheme.
                host = value.split("/")[0]
            if host and host.lower() == target_host:
                return True
        return False

    def _fetch_config(self, force=False):
        if not force and not self._device_management_enabled():
            log_to_file("DM config fetch skipped: device management disabled")
            return None
        if self._fetching_config:
            log_to_file("DM config fetch skipped: recursion guard active")
            return None
        now = time.time()
        self._hydrate_config_cache()
        if not force and self.config_cache and (now - self.config_loaded_at) < self.config_ttl:
            return self.config_cache
        if (
            not force
            and self._config_last_failure_at
            and (now - self._config_last_failure_at) < self._config_failure_backoff
        ):
            if self.config_cache:
                log_to_file("DM config fetch skipped: backoff active, using stale cache")
                return self.config_cache
            log_to_file("DM config fetch skipped: backoff active after recent failure")
            return None

        base_urls = self._failover_ordered_urls()
        if not base_urls:
            log_to_file("DM config fetch skipped: no bootstrap_url(s) configured")
            return None
        config_path = str(self._get_config_from_file("config_path", "/config/config.json"))
        try:
            fetch_timeout = int(self._get_config_from_file("config_fetch_timeout_seconds", 4))
        except Exception:
            fetch_timeout = 4
        if fetch_timeout <= 0:
            fetch_timeout = 4
        log_to_file(f"DM bootstrap URLs (failover order): {base_urls}")

        self._fetching_config = True
        try:
            proxy_enabled = self._as_bool(self._get_config_from_file("proxy_enabled", False))
            attempts = [("direct", False)]
            if proxy_enabled:
                attempts.append(("proxy", True))
            else:
                log_to_file("DM config fetch: proxy disabled, skipping proxy retry")

            last_error = "unknown"
            combos = [
                (base, mode, use_proxy)
                for base in base_urls
                for (mode, use_proxy) in attempts
            ]
            for base_url, mode, use_proxy in combos:
                url = base_url.rstrip("/") + "/" + config_path.lstrip("/")
                try:
                    log_to_file(f"DM config fetch attempt: mode={mode} url={url}")
                    headers = {"Accept": "application/json"}
                    headers.update(self._relay_headers())
                    # Enrich headers for schema_version=2 support
                    plugin_version = self._get_extension_version()
                    headers["X-Plugin-Version"] = plugin_version or "unknown"
                    headers["X-Platform-Type"] = "libreoffice"
                    lo_version = self._get_lo_version()
                    if lo_version:
                        headers["X-Platform-Version"] = lo_version
                    client_uuid = str(self._ensure_plugin_uuid() or "")
                    if client_uuid:
                        headers["X-Client-UUID"] = client_uuid
                    request = urllib.request.Request(url, headers=_with_user_agent(headers))
                    _relay_present = "X-Relay-Client" in headers
                    log_to_file(f"DM config fetch headers: relay={'yes' if _relay_present else 'no'} keys={list(headers.keys())}")
                    with self._urlopen(request, context=self.get_ssl_context(base_url), timeout=fetch_timeout, use_proxy=use_proxy) as response:
                        payload = response.read().decode("utf-8")
                    log_to_file(f"DM bootstrap response ({mode}): {len(payload)} octets")
                    config_data = json.loads(payload)
                    if isinstance(config_data, dict):
                        # Handle EnrichedConfigResponse (schema_version=2)
                        meta = config_data.get("meta") if isinstance(config_data.get("meta"), dict) else {}
                        if meta.get("schema_version") == 2:
                            features = config_data.get("features")
                            if isinstance(features, dict):
                                self._features_cache = features
                                log_to_file(f"Feature flags updated: {list(features.keys())}")
                            update_directive = config_data.get("update")
                            # Feed natif : avant toute décision (y compris « déjà
                            # à la cible » et les reports), l'adresse doit refléter
                            # la cible de CE poste, sinon LibreOffice verrait la
                            # version générale au prochain « Vérifier ».
                            self._rewrite_feed_for_directive(update_directive)
                            if isinstance(update_directive, dict) and update_directive.get("action") in ("update", "rollback"):
                                target_ver = str(update_directive.get("target_version", "")).strip()
                                current_ver = str(self._get_extension_version() or "").strip()
                                if target_ver and current_ver and target_ver == current_ver:
                                    log_to_file(f"Update skipped: already at target version {target_ver}")
                                else:
                                    self._schedule_update(update_directive)
                            else:
                                log_to_file("No update directive in EnrichedConfigResponse")
                        self.config_cache = config_data
                        self.config_loaded_at = now
                        self._config_last_failure_at = 0
                        self._resolved_bootstrap_url = base_url
                        try:
                            if self._get_config_from_file("last_bootstrap_url", "") != base_url:
                                self.set_config("last_bootstrap_url", base_url)
                        except Exception:
                            pass
                        self._persist_bootstrap_config(config_data)
                        self._persist_config_cache(config_data)
                        # Le DM signale ici une auth relais manquante/refusée.
                        # Réagir tout de suite évite de découvrir le problème
                        # seulement au premier 401 sur /llm/v1.
                        try:
                            self._check_relay_auth_notice(config_data)
                        except Exception as exc:
                            log_to_file(f"[ENROLL] auth notice check failed: {str(exc)}")
                        return config_data
                    last_error = f"Invalid JSON root type: {type(config_data).__name__}"
                    log_to_file(f"Failed to fetch device management config ({mode}): {last_error}")
                except urllib.error.HTTPError as e:
                    try:
                        body = e.read().decode("utf-8")
                    except Exception:
                        body = ""
                    last_error = f"HTTP {e.code} {e.reason}"
                    log_to_file(
                        f"Failed to fetch device management config ({mode}): "
                        f"HTTP {e.code} {e.reason} body_len={len(body)}"
                    )
                except urllib.error.URLError as e:
                    last_error = f"URL error {e.reason}"
                    log_to_file(f"Failed to fetch device management config ({mode}): URL error {e.reason}")
                except Exception as e:
                    last_error = str(e)
                    log_to_file(f"Failed to fetch device management config ({mode}): {str(e)}")

            self._config_last_failure_at = now
            log_to_file(f"Failed to fetch device management config: all attempts failed ({last_error})")
        finally:
            self._fetching_config = False
        if self.config_cache:
            log_to_file("DM config fetch failed: using stale cache")
            return self.config_cache
        return None

    def _persist_bootstrap_config(self, config_data):
        """Jetons courts de la réponse /config → mémoire du processus. Les autres
        réglages vivent dans l'instantané (_persist_config_cache), remplacé à
        chaque récupération : une clé abandonnée par le DM disparaît avec lui, ce
        que ne ferait pas une recopie dans les réglages utilisateur."""
        inner = config_data.get("config", {}) if isinstance(config_data, dict) else {}
        credentials.remember_dm_tokens(inner)

    def _persist_config_cache(self, config_data):
        """Instantané de la réponse /config (secrets vidés) : sert de couche DM
        hors ligne et évite un appel bloquant aux instances suivantes de MainJob
        (LibreOffice en crée une par action)."""
        if not isinstance(config_data, dict):
            return
        try:
            self._local_config().save_dm_snapshot(
                config_data, extra_settings=self._keycloak_settings_from(config_data))
        except Exception as exc:
            log_to_file(f"config snapshot persist failed: {str(exc)}")

    def _hydrate_config_cache(self):
        """Recharge l'instantané en cache mémoire s'il a moins de config_ttl."""
        if self.config_cache:
            return
        blob = self._local_config().snapshot()
        data = blob.get("config_data")
        try:
            ts = float(blob.get("ts", 0))
        except (TypeError, ValueError):
            return
        age = time.time() - ts
        if isinstance(data, dict) and 0 <= age < self.config_ttl:
            # Jeton vidé dans l'instantané : un nouveau processus n'a rien en
            # mémoire, le servir bloquerait toute récupération pendant config_ttl.
            settings = local_config.select_settings(data) or {}
            if ("llmToken" in settings or "llm_api_tokens" in settings) \
                    and not credentials.recall(credentials.DM_LLM_TOKEN):
                log_to_file("Instantané DM non rechargé : aucun llmToken en mémoire, "
                            "récupération auprès du DM requise")
                return
            self.config_cache = data
            self.config_loaded_at = ts
            log_to_file(f"config cache hydrated from disk (age {int(age)}s)")

    def _get_extension_version(self):
        """Version installée de l'extension, lue dans le REGISTRE des extensions
        (PackageInformationProvider.getExtensionList : une paire [identifiant,
        version] par extension ; on prend la première paire de notre
        identifiant) — pas dans le description.xml du paquet courant : après
        une mise à jour native, notre propre description.xml décrit encore
        l'ancien paquet (LibreOffice le garde chargé jusqu'au redémarrage) :
        lire le registre, jamais son propre dossier. Repli sur description.xml
        (tests, LibreOffice dégradé)."""
        try:
            pip = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.deployment.PackageInformationProvider", self.ctx
            )
            if pip:
                for pair in pip.getExtensionList() or ():
                    try:
                        ident, version = str(pair[0]), str(pair[1])
                    except Exception:
                        continue
                    if ident == _EXTENSION_IDENTIFIER and version.strip():
                        return version.strip()
        except Exception as exc:
            if not getattr(self, "_registry_read_failure_logged", False):
                log_to_file(f"_get_extension_version: registry read failed: {exc}")
                self._registry_read_failure_logged = True
        # Fallback: parse description.xml from the package directory
        try:
            pkg_dir = self._package_root_dir()
            desc_path = os.path.join(pkg_dir, "description.xml") if pkg_dir else ""
            if os.path.isfile(desc_path):
                with open(desc_path, "r", encoding="utf-8") as f:
                    m = re.search(r'<version\s+value="([^"]+)"', f.read())
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return ""

    def _get_lo_version(self):
        """Return LibreOffice host version string (e.g. '24.8.0')."""
        try:
            cfg_provider = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.configuration.ConfigurationProvider", self.ctx
            )
            prop = PropertyValue()
            prop.Name = "nodepath"
            prop.Value = "/org.openoffice.Setup/Product"
            access = cfg_provider.createInstanceWithArguments(
                "com.sun.star.configuration.ConfigurationUpdateAccess", (prop,)
            )
            raw = access.getByName("ooSetupVersionAboutBox")
            return str(raw).strip() if raw else ""
        except Exception as e:
            log_to_file(f"_get_lo_version error: {e}")
            return ""

    def _schedule_update(self, directive):
        """Start a background daemon thread to perform the plugin update if not already running."""
        urgency = directive.get("urgency", "normal")

        target_version = str(directive.get("target_version") or "").strip()

        if target_version and target_version in self.runtime.update_launch_blocked:
            log_to_file(
                f"Update skipped: install of {target_version} was blocked by the "
                "workstation policy earlier — manual install required, not re-prompting"
            )
            return

        # Refus ou report mémorisé (route native ou dirigée) : ne pas reproposer
        # ni retélécharger à chaque rafraîchissement de config avant l'échéance.
        state = self._load_update_state()
        if target_version and str(state.get("target_version", "")).strip() == target_version:
            try:
                until = float(state.get("postponed_until") or 0)
            except (TypeError, ValueError):
                until = 0.0
            if until > time.time():
                log_to_file(
                    f"Update skipped: {target_version} postponed until "
                    f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}"
                )
                return

        with self.runtime.update_lock:
            if self.runtime.update_in_progress:
                log_to_file("Update already in progress, skipping duplicate schedule")
                return
            self.runtime.update_in_progress = True

        if urgency == "deferred":
            log_to_file(f"Deferred update scheduled: target={directive.get('target_version')} — download only, install on next restart")

        if urgency == "critical":
            log_to_file(f"Critical update initiated: target={directive.get('target_version')}")

        def _worker():
            try:
                # Réconcilier d'abord la mise à jour précédente (la directive
                # suivante peut arriver avant le timer de réconciliation et
                # écraserait l'état persistant). Hors du chemin de fetch config,
                # sous le verrou « en cours » : ni blocage, ni doublon.
                if self._load_update_state():
                    try:
                        self._reconcile_update_state(at_startup=False)
                    except Exception as exc:
                        log_to_file(f"_schedule_update: reconciliation failed: {exc}")
                    previous = self._load_update_state()
                    if previous.get("stage") in ("installed_native", "installed_inprocess"):
                        # Une installation attend déjà un redémarrage : en session,
                        # écraser son état perdrait définitivement son rapport
                        # « installed » au DM.
                        log_to_file(
                            f"Update skipped: mise à jour {previous.get('target_version')} installée, "
                            f"en attente de redémarrage : directive {target_version} ignorée "
                            "jusqu'au redémarrage")
                        return
                self._perform_update(directive)
            finally:
                with self.runtime.update_lock:
                    self.runtime.update_in_progress = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        log_to_file(f"Update thread scheduled: action={directive.get('action')} target={directive.get('target_version')} urgency={urgency}")

    def _perform_update(self, directive):
        """Download, verify checksum and install the artifact via ExtensionManager."""
        action = directive.get("action", "")
        target_version = directive.get("target_version", "")
        artifact_url = directive.get("artifact_url", "")
        expected_checksum = directive.get("checksum", "")
        urgency = directive.get("urgency", "normal")
        campaign_id = directive.get("campaign_id")
        version_before = self._get_extension_version()

        if not artifact_url:
            log_to_file("_perform_update: missing artifact_url")
            return

        # Resolve a RELATIVE artifact_url against every bootstrap base in failover
        # order (last-good first). Otherwise the download is pinned to a single base
        # — often urls[0], an internal-only DGX — and a host that can't reach it
        # (e.g. off-network) fails the whole update instead of falling over to a DM
        # that does answer. An absolute artifact_url is used as-is.
        if artifact_url.startswith("/"):
            bases = [b.rstrip("/") for b in (self._failover_ordered_urls() or []) if b]
            if not bases:
                one = str(self._active_bootstrap_url() or "").strip().rstrip("/")
                bases = [one] if one else []
            candidate_urls = [b + artifact_url for b in bases]
        else:
            candidate_urls = [artifact_url]
        if not candidate_urls:
            log_to_file("_perform_update: no download URL (no bootstrap base configured)")
            return

        tmp_path = None
        try:
            # Route native d'abord : jamais pour une directive
            # différée (elle ne dérange pas l'utilisateur) ni un rollback (LO ne
            # propose que des versions plus récentes) ; bornée en tentatives.
            if action == "update" and urgency != "deferred":
                attempts = self._native_attempts_for(target_version)
                if attempts < _NATIVE_MAX_ATTEMPTS and self._native_feed_offers(target_version):
                    if self._perform_native_update(directive):
                        return
                    log_to_file("_perform_update: native route unavailable, falling back to directed route")

            # Download with failover across bootstrap DMs (2 passes), per-URL TLS.
            binary = None
            full_url = candidate_urls[0]
            last_err = ""
            for dl_pass in range(2):
                for full_url in candidate_urls:
                    log_to_file(f"_perform_update: downloading {full_url} (action={action} target={target_version} urgency={urgency})")
                    try:
                        request = urllib.request.Request(full_url, headers=_with_user_agent({}))
                        with self._urlopen(request, context=self.get_ssl_context(full_url), timeout=60) as response:
                            binary = response.read()
                        break
                    except Exception as dl_err:
                        last_err = str(dl_err)
                        log_to_file(f"_perform_update: download from {full_url} failed: {dl_err}")
                if binary is not None:
                    break
                if dl_pass == 0:
                    time.sleep(2)
            if binary is None:
                self._report_update_status(campaign_id, "download_error", version_before, "", f"all download attempts failed: {last_err}")
                return

            # Verify checksum
            if expected_checksum and expected_checksum.startswith("sha256:"):
                expected_hex = expected_checksum[len("sha256:"):]
                actual_hex = hashlib.sha256(binary).hexdigest()
                if actual_hex != expected_hex:
                    log_to_file(f"_perform_update: checksum mismatch expected={expected_hex} actual={actual_hex}")
                    self._report_update_status(campaign_id, "checksum_error", version_before, "", "checksum mismatch")
                    return
                log_to_file("_perform_update: checksum OK")

            # Write to temp file
            with tempfile.NamedTemporaryFile(suffix=".oxt", delete=False) as tmp:
                tmp.write(binary)
                tmp_path = tmp.name

            # Deferred urgency: save artifact for next restart, skip install
            if urgency == "deferred":
                log_to_file(f"_perform_update: deferred update saved to {tmp_path} for next restart")
                self._report_update_status(campaign_id, "deferred", version_before, "", "")
                return

            # Stage the artifact only: copy it to a stable path. The real install
            # happens ONCE, in-process on the MAIN thread, when the user accepts
            # the restart (see _install_and_restart_in_process). Installing here
            # (from the update worker thread) AND at restart would double-install
            # and can clobber the running instance.
            if local_config.is_frozen():
                log_to_file("_perform_update: extension désinstallée, mise en place abandonnée")
                return
            try:
                stable_dir = self._pending_update_dir()
                if not stable_dir:
                    raise OSError("dossier de l'extension indisponible")
                os.makedirs(stable_dir, exist_ok=True)
            except Exception:
                stable_dir = os.path.dirname(tmp_path)
            stable_oxt = os.path.join(stable_dir, "mirai_update.oxt")
            shutil.copy2(tmp_path, stable_oxt)
            self._pending_install_oxt = stable_oxt
            log_to_file(f"_perform_update: OXT copied to {stable_oxt}")
            # Persiste l'état de campagne : la réconciliation au prochain
            # démarrage rapporte l'issue RÉELLE au DM (_reconcile_update_state).
            self._save_update_state(directive, "staged")
            self._send_telemetry("UpdateStaged", {
                "route": "directed",
                "version_after": target_version,
                "campaign_id": str(campaign_id) if campaign_id is not None else "",
                "urgency": urgency,
            })

            log_to_file(f"_perform_update: install staged for version={target_version}")
            # « deferred » = artefact prêt, installation à suivre (acceptation
            # utilisateur + redémarrage). « installed » n'est rapporté qu'une fois
            # la nouvelle version réellement active (_reconcile_update_state au
            # démarrage suivant) : rapporter « installed » dès le staging
            # compterait comme réussies des installations jamais abouties.
            self._report_update_status(campaign_id, "deferred", version_before, target_version)

            self._wait_before_prompting()
            log_to_file("_perform_update: showing update dialog to user")

            # Ask the user before installing and restarting
            user_wants_restart = False
            try:
                desktop = self.ctx.getServiceManager().createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx
                )
                active_frame = desktop.getCurrentFrame() if desktop else None
                if active_frame:
                    toolkit = self.ctx.getServiceManager().createInstance("com.sun.star.awt.Toolkit")
                    parent = active_frame.getContainerWindow()
                    if urgency == "critical":
                        msg_text = _t("update.prompt_critical", version=target_version)
                    else:
                        msg_text = _t("update.prompt", version=target_version)
                    msgbox = toolkit.createMessageBox(
                        parent,
                        4,  # MessageBoxType.QUERYBOX
                        MSG_BUTTONS.BUTTONS_YES_NO,
                        _t("update.title"),
                        msg_text
                    )
                    answer = msgbox.execute()
                    user_wants_restart = (answer == 2)  # YES
            except Exception as notify_err:
                log_to_file(f"_perform_update: notification error (non-fatal): {notify_err}")

            if user_wants_restart:
                log_to_file("_perform_update: user accepted restart")
                self._save_update_state(directive, "user_accepted", route="directed")
                self._send_telemetry("UpdateAccepted", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "urgency": urgency,
                    "route": "directed",
                })
                # In-process (ExtensionManager sur le main thread) : la seule voie
                # automatique — aucun processus enfant (WinError 5-immune).
                if self._install_and_restart_in_process(
                        self._pending_install_oxt, target_version, campaign_id):
                    log_to_file("_perform_update: installed in-process, closing for restart")
                    self._save_update_state(directive, "installed_inprocess")
                    # « installed » (rapport DM) n'arrive qu'à la réconciliation,
                    # quand la nouvelle version est réellement active.
                    self._send_telemetry("UpdateInstalledPendingRestart", {
                        "version_after": target_version,
                        "campaign_id": str(campaign_id) if campaign_id is not None else "",
                        "route": "directed",
                    })
                    return
                # addExtension n'a pas rendu la main dans le budget du thread
                # principal : l'installation est peut-être en train d'aboutir.
                # Ni échec rapporté, ni cible bannie, ni message manuel (qui
                # inviterait à une seconde installation concurrente) — la
                # réconciliation au prochain démarrage tranche.
                if getattr(self, "_main_thread_install_in_flight", False):
                    log_to_file(
                        "_perform_update: installation encore en cours sur le thread principal, "
                        "issue tranchée à la réconciliation")
                    self._save_update_state(directive, "installed_inprocess", route="directed")
                    return
                # L'install auto n'a pas abouti : anti-boucle (ne pas re-prompter
                # ce target), rapport DM, puis message manuel (voie validée).
                if target_version:
                    self.runtime.update_launch_blocked.add(target_version)
                try:
                    self._report_update_status(
                        campaign_id, "failed", version_before, target_version,
                        "in-process install failed; manual fallback offered"
                    )
                except Exception:
                    pass
                self._send_telemetry("UpdateInstallFailed", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "fallback": "manual",
                    "route": "directed",
                })
                self._notify_update_blocked(target_version)
            else:
                log_to_file("_perform_update: user postponed restart")
                self._save_update_state(directive, "postponed", route="directed",
                                        postponed_until=time.time() + _UPDATE_POSTPONE_SECONDS)
                self._send_telemetry("UpdatePostponed", {
                    "version_after": target_version,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                    "urgency": urgency,
                    "route": "directed",
                })

        except Exception as e:
            log_to_file(f"_perform_update: error: {e}")
            self._report_update_status(campaign_id, "failed", version_before, "", str(e))
        finally:
            # Clean up temp file (unless deferred — kept for restart)
            if tmp_path and urgency != "deferred":
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _notify_update_blocked(self, target_version, oxt_path=""):
        """Inform the user once that the automatic update could not be installed.

        We stop the re-prompt loop and point to the ready-to-install package so
        an admin can finish manually: `oxt_path` when given, the pending_update
        folder otherwise.
        Uses the same message-box path as the update prompt (known to work from
        this worker thread).
        """
        try:
            oxt = oxt_path
            # Dossier à proposer à l'ouverture : celui du .oxt téléchargé, sinon
            # le dossier pending_update du profil. Sert au bouton « Ouvrir le
            # dossier » (ouverture native, sans cmd.exe — cf. _open_folder_native).
            folder = ""
            try:
                if oxt and os.path.isfile(oxt):
                    folder = os.path.dirname(oxt)
                else:
                    cand = self._pending_update_dir()
                    if os.path.isdir(cand):
                        folder = cand
            except Exception:
                folder = ""
            desktop = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx
            )
            active_frame = desktop.getCurrentFrame() if desktop else None
            if not active_frame:
                return
            toolkit = self.ctx.getServiceManager().createInstance("com.sun.star.awt.Toolkit")
            parent = active_frame.getContainerWindow()
            oxt_line = oxt or _t("update.blocked_pending_folder")
            msg = _t("update.blocked_body", version=target_version, oxt=oxt_line)
            # Quand on connaît le dossier du fichier téléchargé, on propose de
            # l'ouvrir directement (Oui = ouvrir l'explorateur, sans cmd.exe).
            open_folder_offered = bool(folder)
            if open_folder_offered:
                msg = msg + (
                    "\n\n──────────────────────────────────────────────\n"
                    + _t("update.blocked_open_folder")
                )
                buttons = MSG_BUTTONS.BUTTONS_YES_NO
            else:
                buttons = MSG_BUTTONS.BUTTONS_OK
            # IMPORTANT : un INFOBOX (type 1) n'affiche QU'UN bouton OK et ignore
            # BUTTONS_YES_NO → le bouton « Oui » n'apparaissait pas. Pour un vrai
            # Oui/Non il faut un QUERYBOX (type 4), comme le prompt de MAJ.
            box_type = 4 if open_folder_offered else 1  # QUERYBOX sinon INFOBOX
            box = toolkit.createMessageBox(
                parent,
                box_type,
                buttons,
                _t("update.blocked_title"),
                msg,
            )
            result = box.execute()
            try:
                box.dispose()
            except Exception:
                pass
            # MessageBoxResults.YES == 2 → ouvrir le dossier en natif (sans cmd.exe).
            if open_folder_offered and result == 2:
                self._open_folder_native(folder)
        except Exception as exc:
            log_to_file(f"_notify_update_blocked: {str(exc)}")

    def _open_folder_native(self, folder_path):
        """Ouvre un dossier dans l'explorateur de l'OS SANS lancer de processus
        enfant (pas de cmd.exe / explorer.exe via subprocess).

        Passe par le service UNO SystemShellExecute (ShellExecute sous le capot).
        Sur un poste durci où la GPO « bloquer les processus enfants d'Office »
        refuse cmd.exe (WinError 5), ShellExecute vers un Explorer déjà lancé est
        le moyen « sans invite de commande » de révéler le paquet téléchargé.
        Best-effort : renvoie True si l'ouverture a été demandée, False sinon
        (ne lève jamais).
        """
        try:
            if not folder_path or not os.path.isdir(folder_path):
                return False
            url = uno.systemPathToFileUrl(folder_path)
            shell = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.system.SystemShellExecute", self.ctx
            )
            if shell is None:
                return False
            # NO_SYSTEM_ERROR_MESSAGE = 1 : pas de popup système bloquante en cas
            # d'échec (on est en best-effort, éventuellement sur un thread de fond).
            shell.execute(url, "", 1)
            log_to_file(f"_open_folder_native: opened {folder_path}")
            return True
        except Exception as exc:
            log_to_file(f"_open_folder_native: {str(exc)}")
            return False

    def _make_silent_command_env(self):
        """XCommandEnvironment silencieux pour l'API de déploiement (approuve la
        VersionException du remplacement même-identifiant, licence déjà
        supprimée). Classes pré-bindées au chargement du module — utilisable
        depuis le worker d'update sans import UNO."""
        if _SilentCommandEnv is not None and _SilentInteractionHandler is not None:
            return _SilentCommandEnv(_SilentInteractionHandler())
        return None

    def _run_on_main_thread(self, action, timeout, label):
        """Exécute `action()` sur le thread PRINCIPAL de LibreOffice, planifié via
        com.sun.star.awt.AsyncCallback + _MainThreadCallback, et attend au plus
        `timeout` s. Renvoie (ok, err). Après un timeout : si le callback n'a pas
        démarré il devient no-op (garde `cancelled`) ; s'il a démarré, `action()`
        tourne peut-être encore sur le thread principal et l'appelant ne doit pas
        lancer un second flux (err == _MAIN_THREAD_TIMEOUT_AFTER_START).
        La durée de l'action elle-même est exposée dans `_last_main_thread_action_s`
        (0.0 si elle n'a pas tourné)."""
        if _MainThreadCallback is None:
            self._last_main_thread_action_s = 0.0
            return False, _MAIN_THREAD_CALLBACK_UNAVAILABLE
        holder = {"ok": False, "err": "", "cancelled": False, "started": False, "action_s": 0.0}
        done = threading.Event()
        # Verrou partagé : sans lui, le callback peut lire cancelled à faux, être
        # préempté avant de poser started, et le worker conclure « jamais démarré »
        # juste avant que l'action s'exécute — deux flux d'installation concurrents.
        guard = threading.Lock()

        def _run():
            with guard:
                if holder["cancelled"]:
                    return
                holder["started"] = True
            action_start = None
            try:
                action_start = time.time()
                action()
                holder["ok"] = True
            except Exception as exc:
                holder["err"] = str(exc)
            finally:
                if action_start is not None:
                    holder["action_s"] = time.time() - action_start
                done.set()

        try:
            async_cb = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.awt.AsyncCallback", self.ctx)
            if async_cb is None:
                self._last_main_thread_action_s = float(holder.get("action_s") or 0.0)
                return False, _MAIN_THREAD_ASYNC_UNAVAILABLE
            async_cb.addCallback(_MainThreadCallback(_run), None)
        except Exception as exc:
            log_to_file(f"{label}: schedule failed: {exc}")
            self._last_main_thread_action_s = float(holder.get("action_s") or 0.0)
            return False, _MAIN_THREAD_SCHEDULE_FAILED + str(exc)
        if not done.wait(timeout):
            with guard:
                holder["cancelled"] = True
                started = holder["started"]
            if started:
                log_to_file(f"{label}: timeout, action still running on main thread")
                self._last_main_thread_action_s = float(holder.get("action_s") or 0.0)
                return False, _MAIN_THREAD_TIMEOUT_AFTER_START
            log_to_file(f"{label}: timeout waiting for main thread")
            self._last_main_thread_action_s = float(holder.get("action_s") or 0.0)
            return False, "timeout"
        self._last_main_thread_action_s = float(holder.get("action_s") or 0.0)
        return holder["ok"], holder["err"]

    def _run_install_on_main_thread(self, oxt_url, props, cmd_env, timeout=90):
        """Installe l'OXT via ExtensionManager.addExtension sur le thread PRINCIPAL.

        C'est l'appel exact que le Gestionnaire des extensions (et l'updater natif
        de LibreOffice) exécute pour une installation manuelle — la voie validée
        sur le terrain comme fiable. Le main thread garde la base d'extensions et
        registrymodifications.xcu cohérents ; les cycles removePackage/addPackage
        répétés depuis le thread worker corrompent le registre. Pas de
        remove-avant-add : addExtension remplace atomiquement une extension de
        même identifiant (VersionException approuvée par le handler silencieux).
        Aucun processus enfant (immunisé WinError 5).

        Retourne True sur succès confirmé ; False sur échec ou timeout (l'appelant
        dégrade). Après un timeout, le callback éventuel devient no-op (garde
        `cancelled`) pour interdire une double installation concurrente.
        """
        ctx = self.ctx

        def _install():
            mgr = None
            try:
                mgr = ctx.getValueByName(self._EXTENSION_MANAGER)
            except Exception as exc:
                log_to_file(f"_run_install_on_main_thread: getValueByName(ExtensionManager): {exc}")
            if mgr is None and _EXT_MGR_SINGLETON is not None:
                try:
                    mgr = _EXT_MGR_SINGLETON.get(ctx)
                except Exception as exc:
                    log_to_file(f"_run_install_on_main_thread: ExtensionManager.get: {exc}")
            if mgr is None:
                raise RuntimeError("ExtensionManager unavailable")
            mgr.addExtension(oxt_url, props, "user", None, cmd_env)

        ok, err = self._run_on_main_thread(_install, timeout, "_run_install_on_main_thread")
        self._main_thread_install_in_flight = (err == _MAIN_THREAD_TIMEOUT_AFTER_START)
        if ok:
            log_to_file("_run_install_on_main_thread: addExtension OK (main thread)")
            return True
        log_to_file(f"_run_install_on_main_thread: install failed: {err}")
        return False

    def _install_and_restart_in_process(self, oxt_path, version_after="", campaign_id=None):
        """Install the update via LibreOffice's own deployment API — entirely
        inside the soffice process — then close LibreOffice cleanly.

        This is the key path for locked-down postes: it spawns **no** child
        process (no cmd.exe / soffice.exe), so it is not affected by an
        AppLocker / Defender-ASR policy that denies child processes (WinError
        5). L'installation passe par ExtensionManager.addExtension sur le MAIN
        thread (voie du Gestionnaire des extensions — remplace proprement, pas
        de corruption du registre).

        Returns True on success; any failure returns False so the caller falls
        back to the manual-install message. UNO usage is lazy so the module
        imports under test stubs.
        """
        self.runtime.self_update_in_flight = True
        try:
            if not oxt_path or not os.path.isfile(oxt_path):
                return False
            cmd_env = self._make_silent_command_env()
            oxt_url = uno.systemPathToFileUrl(oxt_path)

            props = ()
            try:
                nv = uno.createUnoStruct("com.sun.star.beans.NamedValue")
                nv.Name = "SUPPRESS_LICENSE"
                nv.Value = "1"
                props = (nv,)
            except Exception:
                props = ()

            if not self._run_install_on_main_thread(oxt_url, props, cmd_env):
                return False
            log_to_file("_perform_update: in-process install succeeded")

            # Close LibreOffice cleanly so the user reopens it with the new version
            # active. We deliberately do NOT re-exec.
            if not self._close_after_inprocess_update():
                self._send_telemetry("UpdateCloseDeferred", {
                    "route": "directed",
                    "version_after": version_after,
                    "campaign_id": str(campaign_id) if campaign_id is not None else "",
                })
            return True
        except Exception as exc:
            log_to_file(f"_perform_update: in-process install failed, falling back: {exc}")
            return False
        finally:
            self.runtime.self_update_in_flight = False

    def _has_modified_documents(self):
        """Vrai si au moins un document ouvert porte des modifications non
        enregistrées. La sonde tourne sur le thread PRINCIPAL (accès UNO) ;
        best-effort : sonde indisponible, Desktop absent ou composant muet →
        Faux, l'appelant retombe sur son heuristique."""
        try:
            ctx = self.ctx
            smgr = ctx.getServiceManager()
        except Exception as exc:
            log_to_file(f"_has_modified_documents: contexte indisponible ({exc})")
            return False
        found = {"modified": False}

        def _probe():
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            components = desktop.getComponents() if desktop else None
            enumeration = components.createEnumeration() if components else None
            while enumeration is not None and enumeration.hasMoreElements():
                component = enumeration.nextElement()
                try:
                    if component.isModified():
                        found["modified"] = True
                        return
                except Exception:
                    continue

        ok, err = self._run_on_main_thread(
            _probe, _CLOSE_ATTEMPT_TIMEOUT_SECONDS, "_has_modified_documents")
        if not ok:
            log_to_file(f"_has_modified_documents: sonde indisponible ({err})")
            return False
        return found["modified"]

    def _notify_update_activates_at_restart(self):
        """Informe l'utilisateur que la mise à jour installée s'activera au
        prochain démarrage : la boîte précédente lui a promis une fermeture qui
        n'a pas eu lieu. Sur le thread principal, best-effort, jamais bloquant."""
        try:
            ctx = self.ctx
            smgr = ctx.getServiceManager()
        except Exception as exc:
            log_to_file(f"_notify_update_activates_at_restart: contexte indisponible ({exc})")
            return

        def _show():
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            frame = desktop.getCurrentFrame() if desktop else None
            if frame is None:
                return
            toolkit = smgr.createInstance("com.sun.star.awt.Toolkit")
            box = toolkit.createMessageBox(
                frame.getContainerWindow(), 1, MSG_BUTTONS.BUTTONS_OK,
                _t("update.title"), _t("update.activates_at_restart"))
            box.execute()
            try:
                box.dispose()
            except Exception:
                pass

        try:
            self._run_on_main_thread(
                _show, _CLOSE_ATTEMPT_TIMEOUT_SECONDS, "_notify_update_activates_at_restart")
        except Exception as exc:
            log_to_file(f"_notify_update_activates_at_restart: {exc}")

    def _close_after_inprocess_update(self):
        """After an in-process install, CLOSE LibreOffice cleanly so the user reopens
        it with the new version active.

        We deliberately do **not** re-exec (OfficeRestartManager.requestRestart): on
        macOS that relaunches soffice into a windowless zombie, and relaunching *with*
        a window would need a child process (`open -a` / `soffice.exe`) which the
        locked-down-poste GPO denies (WinError 5). Closing + manual reopen is reliable
        on every platform and spawns nothing.

        `Desktop.terminate()` must run on the **main thread** (from this update-worker
        thread it corrupts the macOS layout engine), via `_run_on_main_thread`. Sur la
        route native, LibreOffice refuse la fermeture (veto) tant qu'une de ses
        fenêtres modales — la progression de la mise à jour — est encore ouverte, ou
        si le thread principal est occupé (timeout) : on retente périodiquement
        jusqu'à acceptation ou expiration du délai. Un veto qui a mis du temps à
        arriver, ou un veto alors qu'un document porte des modifications non
        enregistrées, est traité comme un refus humain (« Enregistrer ? » →
        Annuler) et respecté sans nouvel essai. Chaque abandon sans SIGTERM
        informe l'utilisateur que la mise à jour s'activera au prochain démarrage
        (la boîte précédente lui a promis une fermeture qui n'a pas eu lieu). SIGTERM n'intervient que si la PLANIFICATION sur
        le thread principal échoue (callback ou AsyncCallback indisponible,
        addCallback en échec) : le thread principal est alors injoignable. Une
        exception remontée par l'action (Desktop indisponible, service en cours de
        disposition) prouve au contraire qu'il répond — on rend la main sans tuer le
        processus, la mise à jour s'activera au prochain démarrage.

        Retourne True si la fermeture a été acceptée, False sinon (veto persistant,
        refus humain, ou SIGTERM déclenché).
        """
        # Best-effort: tell the user before closing.
        try:
            desktop0 = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx
            )
            active_frame = desktop0.getCurrentFrame() if desktop0 else None
            if active_frame:
                toolkit = self.ctx.getServiceManager().createInstance("com.sun.star.awt.Toolkit")
                parent = active_frame.getContainerWindow()
                box = toolkit.createMessageBox(
                    parent, 1, MSG_BUTTONS.BUTTONS_OK, _t("update.title"),
                    _t("update.installed_closing")
                )
                box.execute()
                try:
                    box.dispose()
                except Exception:
                    pass
        except Exception as msg_err:
            log_to_file(f"_close_after_inprocess_update: message failed: {msg_err}")

        # Clean shutdown on the MAIN thread — NO requestRestart (no windowless re-exec).
        ctx = self.ctx
        smgr = self.ctx.getServiceManager()

        def _terminate():
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            if desktop is None:
                raise RuntimeError("Desktop unavailable")
            if not desktop.terminate():
                raise RuntimeError(_MAIN_THREAD_VETOED)

        deadline = time.time() + _CLOSE_RETRY_SECONDS
        vetoed_logged = False
        while True:
            ok, err = self._run_on_main_thread(
                _terminate, _CLOSE_ATTEMPT_TIMEOUT_SECONDS, "_close_after_inprocess_update")
            action_s = getattr(self, "_last_main_thread_action_s", 0.0)
            if ok or err == _MAIN_THREAD_TIMEOUT_AFTER_START:
                log_to_file("_close_after_inprocess_update: terminate accepted on main thread")
                return True
            if err == _MAIN_THREAD_VETOED and self._has_modified_documents():
                # Un document a des modifications non enregistrées : le veto vient
                # du dialogue « Enregistrer les modifications ? », donc de
                # l'utilisateur. Réessayer, c'est le lui réimposer toutes les 3 s.
                log_to_file("_close_after_inprocess_update: fermeture refusée par l'utilisateur (document modifié ouvert), abandon")
                self._notify_update_activates_at_restart()
                return False
            if err == _MAIN_THREAD_VETOED and action_s >= _CLOSE_USER_REFUSAL_SECONDS:
                # Un veto après une action longue = réponse humaine (dialogue
                # « Enregistrer ? ») ; la latence de planification sur le thread
                # principal ne compte pas.
                log_to_file(f"_close_after_inprocess_update: fermeture refusée par l'utilisateur ({action_s:.1f} s dans terminate()), abandon")
                self._notify_update_activates_at_restart()
                return False
            if err in (_MAIN_THREAD_VETOED, "timeout"):
                # Veto immédiat = dialogue modal de LibreOffice encore ouvert (fenêtre de
                # progression de la MAJ) ; timeout = thread principal occupé. Dans les
                # deux cas on réessaie jusqu'à l'échéance — jamais de SIGTERM ici.
                if not vetoed_logged:
                    log_to_file(f"_close_after_inprocess_update: terminate {err} (dialogue ouvert ou thread principal occupé), nouvel essai périodique")
                    vetoed_logged = True
                if time.time() < deadline:
                    time.sleep(_CLOSE_RETRY_INTERVAL_SECONDS)
                    continue
                log_to_file("_close_after_inprocess_update: veto persistant, abandon — la MAJ s'active au prochain démarrage")
                self._notify_update_activates_at_restart()
                return False
            if err in (_MAIN_THREAD_CALLBACK_UNAVAILABLE, _MAIN_THREAD_ASYNC_UNAVAILABLE) \
                    or str(err).startswith(_MAIN_THREAD_SCHEDULE_FAILED):
                # Le thread principal est injoignable (planification impossible) :
                # seul cas où SIGTERM reste justifié.
                log_to_file(f"_close_after_inprocess_update: main thread unreachable ({err}), SIGTERM fallback")
                self._terminate_on_main_thread()
                return False
            # Exception de l'action : le thread principal répond, aucune raison de
            # tuer le processus (documents non enregistrés).
            log_to_file(f"_close_after_inprocess_update: fermeture impossible ({err}), abandon — la MAJ s'active au prochain démarrage")
            self._notify_update_activates_at_restart()
            return False

    def _terminate_on_main_thread(self):
        """Quit LibreOffice without calling desktop.terminate() from a background thread.

        desktop.terminate() from a non-main thread corrupts the macOS
        autolayout engine (NSISEngine assertion).  Instead, we send SIGTERM
        to the soffice process — macOS delivers the signal on the main thread,
        which triggers a clean shutdown identical to Cmd+Q.
        On Windows we fall back to desktop.terminate() (no autolayout issue).
        """
        if platform.system() in ("Darwin", "Linux"):
            try:
                import signal
                os.kill(os.getpid(), signal.SIGTERM)
                log_to_file("_terminate_on_main_thread: SIGTERM sent to self")
            except Exception as e:
                log_to_file(f"_terminate_on_main_thread: SIGTERM failed ({e}), falling back to terminate()")
                try:
                    desktop = self.ctx.getServiceManager().createInstanceWithContext(
                        "com.sun.star.frame.Desktop", self.ctx)
                    if desktop:
                        desktop.terminate()
                except Exception:
                    pass
        else:
            # Windows: no autolayout issue, direct terminate is fine
            try:
                desktop = self.ctx.getServiceManager().createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx)
                if desktop:
                    desktop.terminate()
                log_to_file("_terminate_on_main_thread: terminate() called (Windows)")
            except Exception as e:
                log_to_file(f"_terminate_on_main_thread: terminate error: {e}")

    # Le résultat réel d'une mise à jour n'est connu qu'au redémarrage suivant
    # (l'installation remplace l'extension qui exécute ce code). On persiste
    # donc l'état de campagne à côté de l'artefact stagé, et au démarrage on
    # compare la version active au target : rapport « installed » véridique,
    # purge idempotente de pending_update, levée de l'anti-boucle.

    def _update_state_path(self):
        base = self._pending_update_dir()
        return os.path.join(base, "update_state.json") if base else ""

    def _load_update_state(self):
        """État persistant de la MAJ en cours, ou {} (fichier absent ou illisible).
        La réconciliation garde sa propre lecture, qui supprime un fichier corrompu."""
        path = self._update_state_path()
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            return {}
        return state if isinstance(state, dict) else {}

    def _save_update_state(self, directive, stage, route=None, postponed_until=None,
                           native_attempts=None):
        """Persiste l'état de la campagne en cours (best-effort, jamais bloquant).

        Pour une même cible, les champs non fournis (route, postponed_until,
        native_attempts) et version_before sont conservés depuis l'état
        précédent : après l'installation, la version active est déjà la cible,
        et version_before doit rester celle d'avant pour la réconciliation.
        """
        path = self._update_state_path()
        if not path or local_config.is_frozen():
            return
        try:
            previous = self._load_update_state()
            target = str(directive.get("target_version", "")).strip()
            same = bool(previous) and str(previous.get("target_version", "")) == target
            version_before = str(previous.get("version_before") or "") if same else ""
            if not version_before:
                version_before = str(self._get_extension_version() or "")
            if route is None:
                route = str(previous.get("route") or "") if same else ""
            if postponed_until is None:
                postponed_until = float(previous.get("postponed_until") or 0) if same else 0.0
            if native_attempts is None:
                native_attempts = int(previous.get("native_attempts") or 0) if same else 0
            state = {
                "campaign_id": directive.get("campaign_id"),
                "target_version": target,
                "version_before": version_before,
                "stage": stage,
                "ts": time.time(),
                "route": route,
                "postponed_until": float(postponed_until),
                "native_attempts": int(native_attempts),
            }
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        except Exception as exc:
            log_to_file(f"_save_update_state: {exc}")

    def _purge_pending_update_dir(self):
        folder = self._pending_update_dir()
        if not folder:
            return
        try:
            shutil.rmtree(folder, ignore_errors=True)
            log_to_file("_purge_pending_update_dir: pending_update purged")
        except Exception as exc:
            log_to_file(f"_purge_pending_update_dir: {exc}")

    def _reconcile_update_state(self, at_startup=False):
        """Clôt la mise à jour précédente de façon idempotente.

        - version active == target → rapport « installed » au DM + télémétrie,
          purge de pending_update (OXT stagé, état), retrait du target
          de l'anti-boucle runtime.update_launch_blocked ;
        - `at_startup` et étape installed_* dont la cible n'est pas active,
          confirmé par deux lectures espacées du registre → l'installation a
          été annulée (rollback de LibreOffice) : rapport
          « failed » au DM + télémétrie, purge. En session (`at_startup` faux)
          le registre garde l'ancienne version jusqu'au redémarrage : on ne
          conclut rien ;
        - état illisible ou périmé (> 14 jours) → purge silencieuse ;
        - sinon (mise à jour encore en attente) → no-op, l'état est conservé.
        """
        path = self._update_state_path()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            state = None
        if not isinstance(state, dict):
            try:
                os.unlink(path)
            except Exception:
                pass
            return
        target = str(state.get("target_version", ""))
        stage = str(state.get("stage") or "")
        pending_restart = stage in ("installed_native", "installed_inprocess")
        current = str(self._get_extension_version() or "")
        if at_startup and pending_restart and target and current and current != target:
            # Juste après le démarrage, le registre peut encore se consolider :
            # seconde lecture espacée avant de conclure à un échec.
            time.sleep(_NATIVE_POLL_SECONDS)
            current = str(self._get_extension_version() or "")
        if target and current == target:
            log_to_file(f"_reconcile_update_state: update to {target} confirmed active")
            try:
                self._report_update_status(
                    state.get("campaign_id"), "installed",
                    str(state.get("version_before", "")), target)
            except Exception as exc:
                log_to_file(f"_reconcile_update_state: status report failed: {exc}")
            try:
                self._send_telemetry("ExtensionUpdated", {
                    "version_after": target,
                    "campaign_id": str(state.get("campaign_id") or ""),
                    "confirmed": "true",
                    "route": str(state.get("route") or ""),
                })
            except Exception:
                pass
            self.runtime.update_launch_blocked.discard(target)
            self._purge_pending_update_dir()
        elif at_startup and pending_restart and target and current:
            log_to_file(
                f"_reconcile_update_state: {target} installée mais inactive au redémarrage "
                f"(version active {current}), échec rapporté")
            try:
                self._report_update_status(
                    state.get("campaign_id"), "failed",
                    str(state.get("version_before", "")), current,
                    "installation non active au redémarrage")
            except Exception as exc:
                log_to_file(f"_reconcile_update_state: status report failed: {exc}")
            try:
                self._send_telemetry("UpdateInstallFailed", {
                    "version_after": target,
                    "campaign_id": str(state.get("campaign_id") or ""),
                    "route": str(state.get("route") or ""),
                })
            except Exception:
                pass
            self._purge_pending_update_dir()
        else:
            age = time.time() - float(state.get("ts", 0) or 0)
            if age > 14 * 24 * 3600:
                log_to_file("_reconcile_update_state: stale update state, purging")
                self._purge_pending_update_dir()

    def _schedule_update_reconciliation(self):
        """Lance la réconciliation en fond, une fois par process (réseau possible)."""
        if self.runtime.update_reconcile_started:
            return
        self.runtime.update_reconcile_started = True

        def _safe_reconcile():
            try:
                self._reconcile_update_state(at_startup=True)
            except Exception as exc:
                log_to_file(f"_reconcile_update_state: {exc}")

        timer = threading.Timer(5.0, _safe_reconcile)
        timer.daemon = True
        timer.start()

    # Route native pilotée : le DM décide (directive update), LibreOffice
    # installe (dialogue « Mise à jour des extensions »). Le plugin ne télécharge
    # rien : il vérifie que le feed <update-information> de l'extension installée
    # annonce exactement la cible, ouvre le dialogue natif sur le thread
    # principal, puis surveille la version installée et ferme LibreOffice
    # proprement.

    def _native_feed_offers(self, target_version):
        """Vrai si le feed de l'extension INSTALLÉE annonce exactement
        target_version. Interrogé par LibreOffice lui-même
        (PackageInformationProvider.isUpdateAvailable, pile HTTP de LO — le
        même chemin que son contrôle périodique). Faux si bloc feed absent,
        feed injoignable, version divergente, ou erreur : la route dirigée prend
        alors le relais. Singleton obtenu sans import (thread worker)."""
        target = str(target_version or "").strip()
        if not target:
            return False
        try:
            provider = self.ctx.getValueByName(
                "/singletons/com.sun.star.deployment.PackageInformationProvider")
        except Exception as exc:
            log_to_file(f"_native_feed_offers: provider unavailable: {exc}")
            return False
        if provider is None:
            log_to_file("_native_feed_offers: PackageInformationProvider unavailable")
            return False
        try:
            pairs = provider.isUpdateAvailable(_EXTENSION_IDENTIFIER)
        except Exception as exc:
            log_to_file(f"_native_feed_offers: isUpdateAvailable failed: {exc}")
            return False
        announced = ""
        for pair in pairs or ():
            try:
                ident, version = str(pair[0]), str(pair[1])
            except Exception:
                continue
            if ident == _EXTENSION_IDENTIFIER:
                announced = version
                break
        offers = announced == target
        log_to_file(
            f"_native_feed_offers: target={target} announced={announced or '-'} offers={offers}")
        return offers

    def _trigger_native_update_dialog(self, timeout=_NATIVE_TRIGGER_TIMEOUT_SECONDS):
        """Ouvre, sur le thread PRINCIPAL, le dialogue natif « Mise à jour des
        extensions » : PackageManagerDialog.trigger("SHOW_UPDATE_DIALOG"), l'appel
        exact de la bulle de notification de LibreOffice (updatecheck.cxx). LO
        interroge le feed, télécharge et installe lui-même ; ses dialogues tournent
        sur son thread de commandes, l'appel rend la main aussitôt.
        Vrai si le déclenchement s'est exécuté sans exception avant `timeout`.
        Après un timeout, le rappel éventuel devient no-op (voir _run_on_main_thread).
        Un timeout après démarrage compte comme déclenché : l'effet est en cours,
        la surveillance ou le report qui suivent sont l'issue sûre."""
        ctx = self.ctx

        def _trigger():
            dialog = ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.deployment.ui.PackageManagerDialog", ctx)
            if dialog is None:
                raise RuntimeError("PackageManagerDialog unavailable")
            dialog.trigger("SHOW_UPDATE_DIALOG")

        ok, err = self._run_on_main_thread(_trigger, timeout, "_trigger_native_update_dialog")
        if ok or err == _MAIN_THREAD_TIMEOUT_AFTER_START:
            suffix = "" if ok else " (still running on main thread)"
            log_to_file(f"_trigger_native_update_dialog: SHOW_UPDATE_DIALOG triggered{suffix}")
            return True
        log_to_file(f"_trigger_native_update_dialog: failed: {err}")
        return False

    def _wait_before_prompting(self):
        """Laisse l'assistant d'enrôlement se terminer (budget
        _PROMPT_WIZARD_WAIT_SECONDS), puis un délai de grâce, puis revérifie :
        l'assistant peut s'être ouvert pendant la grâce (auto-check à T+3 s) et le
        dialogue de mise à jour ne doit pas se superposer à lui."""
        deadline = time.time() + _PROMPT_WIZARD_WAIT_SECONDS
        self._wait_wizard_closed(deadline)
        time.sleep(_PROMPT_GRACE_SECONDS)
        self._wait_wizard_closed(deadline + _PROMPT_GRACE_SECONDS)

    def _wait_wizard_closed(self, deadline):
        while time.time() < deadline:
            with self.runtime.enrollment_wizard_lock:
                if not self.runtime.enrollment_wizard_active:
                    return
            time.sleep(_PROMPT_POLL_SECONDS)

    def _native_attempts_for(self, target_version):
        """Tentatives natives déjà faites pour cette cible (état persistant) ;
        0 si autre cible, état absent ou champ illisible. Cible et état sont
        comparés normalisés (strip) : c'est la même normalisation que
        _save_update_state et _schedule_update."""
        target = str(target_version or "").strip()
        previous = self._load_update_state()
        if not target or str(previous.get("target_version", "")).strip() != target:
            return 0
        try:
            return int(previous.get("native_attempts") or 0)
        except (TypeError, ValueError):
            return 0

    def _package_cache_dir(self):
        """Dossier du cache des paquets utilisateur
        (<profil>/user/uno_packages/cache/uno_packages) : on remonte depuis ce
        module jusqu'à la racine du paquet — le dossier qui contient
        description.xml — puis de deux niveaux (<paquet>.oxt → <lu…> → cache).
        Repli : cinq niveaux au-dessus de ce module (disposition standard de
        uno_packages/cache) si aucun description.xml n'est trouvé dans les
        niveaux inspectés. Calculé une fois par instance."""
        cached = getattr(self, "_package_cache_dir_value", None)
        if cached:
            return cached
        root = self._package_root_dir()
        result = os.path.dirname(os.path.dirname(root)) if root else None
        if result is None:
            log_to_file("_package_cache_dir: description.xml introuvable, repli sur la profondeur fixe")
            result = os.path.abspath(__file__)
            for _ in range(5):
                result = os.path.dirname(result)
        self._package_cache_dir_value = result
        return result

    def _cached_package_versions(self):
        """Entrées (dossier <lu…>, version) de NOTRE extension présentes dans le
        cache des paquets sur disque. En session, LibreOffice garde l'ancien
        paquet enregistré jusqu'au redémarrage : le registre (getExtensionList)
        ne voit jamais la nouvelle version, mais son dossier existe déjà dans le
        cache — c'est le signal fiable d'une installation native aboutie. Le nom
        du dossier <lu…> est gardé (pas seulement la version) pour distinguer un
        dossier apparu pendant l'attente d'un dossier résiduel d'une tentative
        antérieure sur la même cible. Best-effort, jamais d'exception."""
        entries = set()
        try:
            cache = self._package_cache_dir()
            for lu in os.listdir(cache):
                lu_dir = os.path.join(cache, lu)
                if not os.path.isdir(lu_dir):
                    continue
                for pkg in os.listdir(lu_dir):
                    desc = os.path.join(lu_dir, pkg, "description.xml")
                    if not os.path.isfile(desc):
                        continue
                    with open(desc, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    if not re.search(r'<identifier\s+value="' + re.escape(_EXTENSION_IDENTIFIER) + r'"', text):
                        continue
                    m = re.search(r'<version\s+value="([^"]+)"', text)
                    if m:
                        entries.add((lu, m.group(1).strip()))
        except Exception as exc:
            log_to_file(f"_cached_package_versions: {exc}")
        return entries

    @staticmethod
    def _versions_of(entries):
        return {v for (_d, v) in entries}

    def _perform_native_update(self, directive):
        """Route native pilotée : le DM a décidé (directive), LibreOffice installe.

        Renvoie True si le dialogue a été montré et l'issue traitée — installée
        (fermeture propre) ou reportée (cooldown) ; False si le déclenchement a
        échoué, sans rien rapporter ni persister : l'appelant bascule en route
        dirigée. Après l'installation native, LibreOffice garde l'ancien paquet
        chargé jusqu'au redémarrage et le nouveau dossier apparaît à côté : d'ici
        la fermeture, aucun import de module du plugin.

        Détection de l'installation : combine le registre
        (`_get_extension_version`, via `PackageInformationProvider`) et le cache
        des paquets sur disque (`_cached_package_versions`) — en session,
        LibreOffice garde l'ancien paquet enregistré jusqu'au redémarrage, donc
        le registre seul reste bloqué sur l'ancienne version même après une
        installation native aboutie. Détection confirmée sur deux lectures
        consécutives, et uniquement pour un dossier apparu depuis l'instantané
        pris avant l'attente et l'ouverture du dialogue.
        """
        target_version = str(directive.get("target_version", "")).strip()
        campaign_id = directive.get("campaign_id")
        campaign_attr = str(campaign_id) if campaign_id is not None else ""
        urgency = directive.get("urgency", "normal")
        version_before = str(self._get_extension_version() or "")
        attempts = self._native_attempts_for(target_version)

        cached_before = self._cached_package_versions()
        self._wait_before_prompting()
        log_to_file(f"_perform_native_update: opening native update dialog for {target_version}")
        if not self._trigger_native_update_dialog():
            return False

        self._report_update_status(campaign_id, "deferred", version_before, target_version)
        self._save_update_state(directive, "native_dialog", route="native",
                                native_attempts=attempts + 1)
        self._send_telemetry("UpdateNativeDialogShown", {
            "version_after": target_version,
            "campaign_id": campaign_attr,
            "route": "native",
            "attempt": str(attempts + 1),
            "urgency": urgency,
        })

        deadline = time.time() + _NATIVE_INSTALL_WAIT_SECONDS
        last_seen = None
        polls = 0
        hits = 0
        while time.time() < deadline:
            time.sleep(_NATIVE_POLL_SECONDS)
            polls += 1
            probe_start = time.time()
            seen = str(self._get_extension_version() or "")
            cached = self._cached_package_versions()
            new_entries = cached - cached_before
            if target_version in self._versions_of(new_entries):
                seen = target_version
            probe_ms = int((time.time() - probe_start) * 1000)
            hits = hits + 1 if seen == target_version and target_version else 0
            # Journal de diagnostic : valeur vue à chaque changement (et à la
            # première lecture), ou lecture anormalement lente.
            if seen != last_seen or probe_ms > _SLOW_PROBE_LOG_MS:
                log_to_file(
                    f"_perform_native_update: poll #{polls} installed={seen or '-'} "
                    f"cache={sorted(self._versions_of(cached)) or '-'} target={target_version} "
                    f"hits={hits} ({probe_ms} ms)")
                last_seen = seen
            if hits >= 2:
                log_to_file(f"_perform_native_update: {target_version} installed natively, closing for restart")
                self._save_update_state(directive, "installed_native", route="native")
                self._send_telemetry("UpdateInstalledPendingRestart", {
                    "version_after": target_version,
                    "campaign_id": campaign_attr,
                    "route": "native",
                })
                closed = False
                try:
                    closed = self._close_after_inprocess_update()
                except Exception as exc:
                    log_to_file(f"_perform_native_update: close failed (update is installed): {exc}")
                if not closed:
                    self._send_telemetry("UpdateCloseDeferred", {
                        "version_after": target_version,
                        "campaign_id": campaign_attr,
                        "route": "native",
                    })
                return True

        log_to_file(f"_perform_native_update: no install detected after {polls} polls (last seen {last_seen or '-'}), postponed")
        self._save_update_state(directive, "postponed", route="native",
                                postponed_until=time.time() + _UPDATE_POSTPONE_SECONDS)
        self._send_telemetry("UpdatePostponed", {
            "version_after": target_version,
            "campaign_id": campaign_attr,
            "route": "native",
            "urgency": urgency,
        })
        return True

    # Diagnostic passif du feed natif : LibreOffice récupère le feed avec SA pile
    # HTTP (proxy/TLS/GPO propres), pas celle du plugin. Ce check headless valide
    # donc, sans aucune action utilisateur et à l'échelle de la flotte, que la
    # route native est viable sur les postes durcis — et détecte un feed DM
    # absent ou mal formé AVANT d'appuyer le déploiement large dessus.

    def _update_feed_urls(self):
        """URLs du feed natif, dérivées des bootstrap configurés (failover d'abord).
        Même convention que le bake au build (scripts/inject_update_feed.py),
        avec le ?version= que la réécriture pose sur description.xml."""
        urls = [
            base.rstrip("/") + _UPDATE_FEED_PATH
            for base in (self._failover_ordered_urls() or [])
            if isinstance(base, str) and base.strip()
        ]
        # Seulement si la réécriture a réussi : sinon (offline, installation non
        # inscriptible) LibreOffice lit l'adresse nue, le diagnostic aussi.
        version = str(self.runtime.feed_rewrite_last or "").strip()
        return [feed_rewrite.with_version(u, version) for u in urls] if version else urls

    # Réécriture de l'adresse du feed natif : le DM ne sert sur l'adresse nue que
    # la « version générale ». Le plugin écrit dans le description.xml de SON
    # installation `?version=<cible>` (directive update) ou `?version=<installée>`
    # (sinon) : LibreOffice relit ce fichier à chaque vérification, et chaque
    # poste ne voit que sa propre cible.

    def _package_root_dir(self):
        """Racine du paquet installé (dossier contenant description.xml), ou None."""
        here = os.path.dirname(os.path.abspath(__file__))
        for _ in range(_PACKAGE_ROOT_SEARCH_LEVELS):
            if os.path.isfile(os.path.join(here, "description.xml")):
                return here
            here = os.path.dirname(here)
        return None

    def _feed_target_for(self, directive):
        """Version à inscrire dans l'adresse du feed : la cible d'une directive
        `update`, sinon la version installée. Un rollback garde la version
        installée : LibreOffice ne propose jamais une version plus ancienne, et
        l'adresse ne doit pas non plus reproposer celle qu'on retire."""
        if isinstance(directive, dict) and directive.get("action") == "update":
            target = str(directive.get("target_version") or "").strip()
            if target:
                return target
        return str(self._get_extension_version() or "").strip()

    def _rewrite_feed_for_directive(self, directive):
        try:
            self._rewrite_feed_url(self._feed_target_for(directive))
        except Exception as exc:
            log_to_file(f"_rewrite_feed_url: {exc}")

    def _rewrite_feed_url(self, version, unless_already_set=False):
        """Pose ?version=<version> sur l'adresse du feed du description.xml
        installé. Sans effet si le bloc est absent (profil offline) ou si rien ne
        change ; installation non inscriptible (couche partagée) → journalisé,
        la route dirigée reste le repli (_native_feed_offers ne verra pas la
        cible). Avec `unless_already_set`, n'écrit rien si une adresse a déjà été
        posée dans ce process. Renvoie le résultat de feed_rewrite (None si rien
        n'a été tenté)."""
        if not version:
            return feed_rewrite.ERROR
        root = self._package_root_dir()
        if not root:
            log_to_file("_rewrite_feed_url: description.xml introuvable")
            return feed_rewrite.ERROR
        path = os.path.join(root, "description.xml")
        with self.runtime.feed_rewrite_lock:
            if unless_already_set and self.runtime.feed_rewrite_last_result is not None:
                return None
            result, detail = feed_rewrite.rewrite_description_file(path, version)
            # Journal et télémétrie une fois par changement d'état, pas à chaque
            # lecture de /config.
            changed = (result, version) != self.runtime.feed_rewrite_last_result
            self.runtime.feed_rewrite_last_result = (result, version)
            if result in (feed_rewrite.WRITTEN, feed_rewrite.UNCHANGED):
                self.runtime.feed_rewrite_last = version
        if changed and result != feed_rewrite.UNCHANGED:
            log_to_file(f"_rewrite_feed_url: {result} version={version} {detail}".rstrip())
            self._send_telemetry("FeedRewrite", {
                "feed.target": str(version),
                "feed.result": result,
                "feed.error": str(detail)[:200],
            })
        return result

    def _schedule_feed_rewrite(self):
        """Réécriture au démarrage, une fois par process, avant le diagnostic
        du feed (45 s) et avant toute directive : cible = version installée."""
        if self.runtime.feed_rewrite_started:
            return
        self.runtime.feed_rewrite_started = True

        def _safe_rewrite():
            try:
                # Une directive lue entre-temps a déjà posé la bonne adresse : ne
                # pas l'écraser avec la version installée.
                self._rewrite_feed_url(str(self._get_extension_version() or "").strip(),
                                       unless_already_set=True)
            except Exception as exc:
                log_to_file(f"_rewrite_feed_url (démarrage): {exc}")

        timer = threading.Timer(2.0, _safe_rewrite)
        timer.daemon = True
        timer.start()

    def _check_native_feed(self):
        """Interroge le feed via com.sun.star.deployment.UpdateInformationProvider
        (la machinerie exacte du bouton « Vérifier les mises à jour »), et rapporte
        le résultat en log + télémétrie NativeFeedCheck. N'installe rien, aucune
        UI ; best-effort — toute erreur est non-fatale. Retourne la version
        annoncée par le feed, ou None."""
        urls = self._update_feed_urls()
        if not urls:
            log_to_file("_check_native_feed: no bootstrap configured, skipped")
            return None
        provider = None
        try:
            provider = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.deployment.UpdateInformationProvider", self.ctx)
        except Exception as exc:
            log_to_file(f"_check_native_feed: provider unavailable: {exc}")
        if provider is None:
            return None
        announced = ""
        error = ""
        try:
            infos = provider.getUpdateInformation(tuple(urls), _EXTENSION_IDENTIFIER)
            for element in infos or ():
                # Le feed conforme expose <version value="…"/> dans le namespace
                # update/2006 ; on tolère aussi un document sans namespace.
                for getter in ("getElementsByTagNameNS", "getElementsByTagName"):
                    try:
                        if getter == "getElementsByTagNameNS":
                            nodes = element.getElementsByTagNameNS(_UPDATE_FEED_NS, "version")
                        else:
                            nodes = element.getElementsByTagName("version")
                        node = nodes.item(0) if nodes is not None and nodes.getLength() > 0 else None
                        value = str(node.getAttribute("value")) if node is not None else ""
                    except Exception:
                        value = ""
                    if value:
                        announced = value
                        break
                if announced:
                    break
            if not announced:
                error = "feed reachable but no <version value> found"
        except Exception as exc:
            error = str(exc)
        current = str(self._get_extension_version() or "")
        ok = bool(announced)
        log_to_file(
            f"_check_native_feed: ok={ok} announced={announced or '-'} "
            f"current={current or '-'} urls={len(urls)}"
            + (f" error={error}" if error else "")
        )
        try:
            self._send_telemetry("NativeFeedCheck", {
                "feed.ok": "true" if ok else "false",
                "feed.announced_version": announced,
                "feed.error": error[:200],
                "version_current": current,
            })
        except Exception:
            pass
        return announced or None

    def _schedule_native_feed_check(self):
        """Lance le diagnostic du feed en fond, une fois par process, après que
        l'enrollment/config a eu le temps de se poser (réseau via la pile de LO)."""
        if self.runtime.feed_check_started:
            return
        self.runtime.feed_check_started = True

        def _safe_check():
            try:
                self._check_native_feed()
            except Exception as exc:
                log_to_file(f"_check_native_feed: {exc}")

        timer = threading.Timer(45.0, _safe_check)
        timer.daemon = True
        timer.start()

    def _report_update_status(self, campaign_id, status, version_before, version_after, error_detail=""):
        """Report update status back to device-management server."""
        base_url = str(self._active_bootstrap_url() or "").strip().rstrip("/")
        if not base_url:
            return
        endpoint = base_url + "/update/status"
        client_uuid = str(self._ensure_plugin_uuid() or "")

        payload = {
            "campaign_id": campaign_id,
            "client_uuid": client_uuid,
            "status": status,
            "version_before": version_before,
            "version_after": version_after,
            "error_detail": error_detail,
        }

        # Retry 3 times with backoff
        for attempt in range(3):
            try:
                data = json.dumps(payload).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                # /update/status requires relay credentials (DM VULN-007), same
                # as /config and telemetry. Without these headers the DM returns
                # 401 and the campaign never records this device's outcome.
                headers.update(self._relay_headers())
                access_token = str(self._get_config_from_file("access_token", "") or "")
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
                req = urllib.request.Request(endpoint, data=data, headers=_with_user_agent(headers))
                with self._urlopen(req, context=self.get_ssl_context(), timeout=10) as resp:
                    resp.read()
                log_to_file(f"Update status reported: {status} campaign={campaign_id}")
                return
            except Exception as e:
                log_to_file(f"Update status report attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))  # 2s, 4s

    def _get_setting(self, key):
        now = time.time()
        cache_fresh = bool(self.config_cache and (now - self.config_loaded_at) < self.config_ttl)
        if not cache_fresh:
            self._schedule_config_refresh(force=not bool(self.config_cache), reason=f"get_setting:{key}")
        config_data = self.config_cache if isinstance(self.config_cache, dict) else None
        if not config_data:
            return None
        settings = local_config.select_settings(config_data)
        if isinstance(settings, dict) and key in settings:
            return settings.get(key)
        return None

    def get_config(self, key, default):
        # Check for telemetry defaults first
        telemetry_defaults = self._get_telemetry_defaults()
        if key in telemetry_defaults and default is None:
            default = telemetry_defaults[key]

        if key == "llm_base_urls":
            config_value = self._get_setting("llm_base_urls")
            if config_value is not None:
                if len(str(config_value)) >= 6:
                    return config_value
            return self._get_config_from_file("llm_base_urls", default)

        if key == "llm_api_tokens":
            return self._resolve_llm_token(default)

        if key == "llm_default_models":
            local_model = str(self._local_config().settings().get("llm_default_models", "") or "").strip()
            config_model = self._get_setting("llm_default_models")
            config_model = str(config_model).strip() if config_model is not None else ""
            if config_model and len(config_model) < 6:
                config_model = ""
            if not config_model:
                config_model = str(
                    self._get_config_from_file("llm_default_models", "") or "").strip()

            endpoint = self.get_config("llm_base_urls", "http://127.0.0.1:5000")
            api_key = self.get_config("llm_api_tokens", "")
            is_openwebui = True

            models = self._get_cached_models(str(endpoint), str(api_key), is_openwebui)

            if local_model:
                if not models or local_model in models:
                    log_to_file(f"Model selection (local): {local_model}")
                    return local_model
                log_to_file(f"Model not found in list (local): {local_model}")

            if config_model:
                if not models or config_model in models:
                    log_to_file(f"Model selection (dm): {config_model}")
                    return config_model
                log_to_file(f"Model not found in list (dm): {config_model}")

            if models:
                log_to_file(f"Model selection (fallback first): {models[0]}")
                return models[0]

            fallback = local_model or config_model or default
            if fallback:
                log_to_file(f"Model selection (fallback): {fallback}")
            return fallback

        config_value = self._get_setting(key)
        # Valeur vidée avant l'écriture sur disque (cache réhydraté) : ce n'est
        # pas la valeur du DM, on retombe sur la lecture locale.
        if key in local_config.SECRET_DM_KEYS and config_value == "":
            config_value = None
        if key == "telemetryKey" and not config_value:
            config_value = credentials.recall(credentials.DM_TELEMETRY_KEY) or None
        if config_value is not None:
            return config_value

        return self._get_config_from_file(key, default)

    def set_config(self, key, value):
        if key in credentials.STORED_KEYS:
            if not self._store_secret(key, value, self._credential_scope()):
                log_to_file(f"[secrets] {key} refusé par le coffre : gardé en mémoire "
                            "pour la session")
            return
        if key in credentials.MEMORY_KEYS:
            credentials.remember(key, value)
            return
        try:
            self._local_config().set(key, value)
        except OSError as e:
            log_to_file(f"Error writing setting {key}: {e}")
            return
        if key == "llm_default_models":
            log_to_file(f"Model saved (local): {value}")

    def _jwt_payload(self, token):
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            payload = parts[1]
            padding = "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            return {}

    def _token_is_expired(self, token, skew_seconds=60):
        payload = self._jwt_payload(token)
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return False
        return time.time() >= (exp - skew_seconds)

    def _show_message(self, title, message):
        try:
            toolkit = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.awt.Toolkit", self.ctx
            )
            frame = self.desktop.getCurrentFrame() if self.desktop else None
            window = frame.getContainerWindow() if frame else None
            if not window:
                return
            from com.sun.star.awt.MessageBoxType import MESSAGEBOX
            try:
                box = toolkit.createMessageBox(
                    window,
                    uno.createUnoStruct("com.sun.star.awt.Rectangle"),
                    MESSAGEBOX,
                    MSG_BUTTONS.BUTTONS_OK,
                    str(title),
                    str(message)
                )
            except Exception:
                box = toolkit.createMessageBox(
                    window,
                    MESSAGEBOX,
                    MSG_BUTTONS.BUTTONS_OK,
                    str(title),
                    str(message)
                )
            box.execute()
            box.dispose()
        except Exception as e:
            log_to_file(f"Failed to show message box: {str(e)}")

    def _confirm_message(self, title, message):
        try:
            toolkit = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.awt.Toolkit", self.ctx
            )
            frame = self.desktop.getCurrentFrame() if self.desktop else None
            window = frame.getContainerWindow() if frame else None
            if not window:
                return False
            from com.sun.star.awt.MessageBoxType import MESSAGEBOX
            try:
                box = toolkit.createMessageBox(
                    window,
                    uno.createUnoStruct("com.sun.star.awt.Rectangle"),
                    MESSAGEBOX,
                    MSG_BUTTONS.BUTTONS_OK_CANCEL,
                    str(title),
                    str(message)
                )
            except Exception:
                box = toolkit.createMessageBox(
                    window,
                    MESSAGEBOX,
                    MSG_BUTTONS.BUTTONS_OK_CANCEL,
                    str(title),
                    str(message)
                )
            result = box.execute()
            box.dispose()
            return result == 1
        except Exception as e:
            log_to_file(f"Failed to show confirm box: {str(e)}")
        return False

    def _show_enrollment_wizard(self):
        """Multi-step enrollment wizard (steps 1-3 clickable, 4-5 automatic).

        Returns (proceed, dialog, toolkit, update_fn, state) so the caller can
        keep the dialog alive for the auth-wait and enrollment phases.
        On cancellation or error falls back to (False, None, None, None, None).
        """
        try:
            from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
            ctx = uno.getComponentContext()
            create = ctx.getServiceManager().createInstanceWithContext

            WIDTH = 560
            HEIGHT = 500
            MARGIN = 24
            IMG_SIZE = 80
            BTN_W = 175
            BTN_H = 32
            TOTAL_STEPS = 5  # 3 clickable + 2 automatic

            wizard_steps = [
                {
                    "title": _t("enroll.welcome"),
                    "text": _t("enroll.step1_text"),
                    "btn_next": _t("enroll.start"),
                    "btn_cancel": _t("enroll.later"),
                    "step_label": _t("enroll.step1_label"),
                },
                {
                    "title": _t("enroll.step2_title"),
                    "text": _t("enroll.step2_text"),
                    "btn_next": _t("enroll.open_browser"),
                    "btn_cancel": _t("common.cancel"),
                    "step_label": _t("enroll.step2_label"),
                },
            ]

            result = {"proceed": False, "step": 0, "cancelled": False}

            dialog = create("com.sun.star.awt.UnoControlDialog", ctx)
            dialog_model = create("com.sun.star.awt.UnoControlDialogModel", ctx)
            dialog.setModel(dialog_model)
            dialog.setVisible(False)
            dialog.setTitle(_t("enroll.welcome"))
            dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)

            def add_control(name, ctrl_type, x, y, w, h, props):
                try:
                    model = dialog_model.createInstance(
                        "com.sun.star.awt.UnoControl" + ctrl_type + "Model")
                    dialog_model.insertByName(name, model)
                    ctrl = dialog.getControl(name)
                    ctrl.setPosSize(x, y, w, h, POSSIZE)
                    for k, v in props.items():
                        try:
                            setattr(model, k, v)
                        except Exception:
                            pass
                    return ctrl
                except Exception as e:
                    log_to_file(f"Wizard control error: {name} {str(e)}")
                    return None

            # Mascot image (centred, top)
            logo_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png")
            if os.path.exists(logo_path):
                logo_url = uno.systemPathToFileUrl(os.path.abspath(logo_path))
                img_x = (WIDTH - IMG_SIZE) // 2
                add_control("wiz_logo", "ImageControl", img_x, 8,
                            IMG_SIZE, IMG_SIZE, {
                                "ImageURL": logo_url,
                                "Border": 0,
                                "ScaleImage": True
                            })

            # Title (centred, just below image)
            title_y = 8 + IMG_SIZE + 6
            add_control("wiz_title", "FixedText", MARGIN, title_y,
                        WIDTH - MARGIN * 2, 22, {
                            "Label": wizard_steps[0]["title"],
                            "Align": 1,
                            "NoLabel": True,
                            "FontHeight": 13,
                            "FontWeight": 150,
                        })

            # Bottom controls zone: step + bar + buttons = ~40px above dialog bottom
            bottom_zone_h = 16 + 18 + 4 + 16 + BTN_H + 20  # step + gap + bar + gap + btn + margin
            bottom_start_y = HEIGHT - bottom_zone_h

            # Body text — centred vertically between title and bottom controls
            text_top = title_y + 26
            text_h = bottom_start_y - text_top - 6
            add_control("wiz_text", "FixedText", MARGIN + 10, text_top,
                        WIDTH - MARGIN * 2 - 20, text_h, {
                            "Label": wizard_steps[0]["text"],
                            "MultiLine": True,
                            "NoLabel": True,
                            "FontHeight": 10,
                        })

            # Step indicator
            step_y = bottom_start_y
            add_control("wiz_step", "FixedText", MARGIN, step_y,
                        WIDTH - MARGIN * 2, 16, {
                            "Label": wizard_steps[0]["step_label"],
                            "Align": 1,
                            "NoLabel": True,
                            "TextColor": 0x888888,
                            "FontHeight": 9,
                        })

            # Progress bar
            bar_y = step_y + 18
            bar_w = WIDTH - MARGIN * 2
            add_control("wiz_bar_bg", "FixedText", MARGIN, bar_y,
                        bar_w, 4, {"Label": "", "BackgroundColor": 0xE0E0E0, "NoLabel": True})
            progress_w = bar_w // TOTAL_STEPS
            add_control("wiz_bar_fill", "FixedText", MARGIN, bar_y,
                        progress_w, 4, {"Label": "", "BackgroundColor": 0x2255AA, "NoLabel": True})

            # Buttons
            btn_y = bar_y + 16
            btn_cancel_x = WIDTH // 2 - BTN_W - 10
            btn_next_x = WIDTH // 2 + 10

            add_control("wiz_btn_cancel", "Button", btn_cancel_x, btn_y,
                        BTN_W, BTN_H, {
                            "Label": wizard_steps[0]["btn_cancel"],
                            "Name": "wiz_cancel",
                        })
            add_control("wiz_btn_next", "Button", btn_next_x, btn_y,
                        BTN_W, BTN_H, {
                            "Label": wizard_steps[0]["btn_next"],
                            "Name": "wiz_next",
                            "DefaultButton": True,
                        })

            dialog.setPosSize(0, 0, WIDTH, btn_y + BTN_H + 20, SIZE)

            frame = create("com.sun.star.frame.Desktop", ctx).getCurrentFrame()
            window = frame.getContainerWindow() if frame else None
            toolkit = create("com.sun.star.awt.Toolkit", ctx)
            dialog.createPeer(toolkit, window)
            if window:
                ps = window.getPosSize()
                _x = ps.Width // 2 - WIDTH // 2
                _y = ps.Height // 2 - HEIGHT // 2
                dialog.setPosSize(_x, _y, 0, 0, POS)

            def _update_step(step_idx):
                step = wizard_steps[step_idx]
                try:
                    dialog.getControl("wiz_title").getModel().Label = step["title"]
                    dialog.getControl("wiz_title").getModel().TextColor = _UI["text"]
                    dialog.getControl("wiz_text").getModel().Label = step["text"]
                    dialog.getControl("wiz_step").getModel().Label = step["step_label"]
                    dialog.getControl("wiz_btn_next").getModel().Label = step["btn_next"]
                    dialog.getControl("wiz_btn_next").getModel().Enabled = True
                    dialog.getControl("wiz_btn_cancel").getModel().Label = step["btn_cancel"]
                    dialog.getControl("wiz_btn_cancel").getModel().Enabled = True
                    fill_w = (WIDTH - MARGIN * 2) * (step_idx + 1) // TOTAL_STEPS
                    dialog.getControl("wiz_bar_fill").setPosSize(
                        MARGIN, 0, fill_w, 4, SIZE)
                    pump_events(toolkit)
                except Exception as e:
                    log_to_file(f"Wizard update step error: {str(e)}")

            def _update_custom(title, text, step_label, step_num,
                               btn_next=None, btn_cancel=None, title_color=None):
                """Update wizard to an automatic step (steps 4-5).

                setVisible() is unreliable after execute() has returned in UNO,
                so visibility is controlled via Enabled only.
                """
                try:
                    dialog.getControl("wiz_title").getModel().Label = title
                    dialog.getControl("wiz_title").getModel().TextColor = (
                        title_color if title_color is not None else _UI["text"]
                    )
                    dialog.getControl("wiz_text").getModel().Label = text
                    dialog.getControl("wiz_step").getModel().Label = step_label
                    fill_w = (WIDTH - MARGIN * 2) * step_num // TOTAL_STEPS
                    dialog.getControl("wiz_bar_fill").setPosSize(MARGIN, 0, fill_w, 4, SIZE)
                    dialog.getControl("wiz_btn_next").getModel().Label = btn_next if btn_next else ""
                    dialog.getControl("wiz_btn_next").getModel().Enabled = bool(btn_next)
                    dialog.getControl("wiz_btn_cancel").getModel().Label = btn_cancel if btn_cancel else ""
                    dialog.getControl("wiz_btn_cancel").getModel().Enabled = bool(btn_cancel)
                    # Center next button when cancel is hidden; push cancel off-screen
                    try:
                        if btn_cancel:
                            dialog.getControl("wiz_btn_cancel").setPosSize(
                                btn_cancel_x, btn_y, BTN_W, BTN_H, POSSIZE)
                            dialog.getControl("wiz_btn_next").setPosSize(
                                btn_next_x, btn_y, BTN_W, BTN_H, POSSIZE)
                        else:
                            # Move cancel off-screen so it doesn't overlap
                            dialog.getControl("wiz_btn_cancel").setPosSize(
                                -BTN_W - 10, btn_y, BTN_W, BTN_H, POSSIZE)
                            dialog.getControl("wiz_btn_next").setPosSize(
                                (WIDTH - BTN_W) // 2, btn_y, BTN_W, BTN_H, POSSIZE)
                    except Exception:
                        pass
                    pump_events(toolkit)
                except Exception as e:
                    log_to_file(f"Wizard custom step error: {str(e)}")

            class WizardNextListener(unohelper.Base, XActionListener):
                def actionPerformed(self, event):
                    result["step"] += 1
                    if result["step"] < len(wizard_steps):
                        _update_step(result["step"])
                    else:
                        # All clickable steps done — end modal loop, keep dialog alive
                        result["proceed"] = True
                        try:
                            dialog.endExecute()
                        except Exception:
                            pass

                def disposing(self, event):
                    pass

            class WizardCancelListener(unohelper.Base, XActionListener):
                def actionPerformed(self, event):
                    result["proceed"] = False
                    result["cancelled"] = True
                    try:
                        dialog.endExecute()
                    except Exception:
                        pass

                def disposing(self, event):
                    pass

            btn_next = dialog.getControl("wiz_btn_next")
            btn_cancel = dialog.getControl("wiz_btn_cancel")
            if btn_next:
                btn_next.addActionListener(WizardNextListener())
            if btn_cancel:
                btn_cancel.addActionListener(WizardCancelListener())

            dialog.setVisible(True)
            dialog.execute()
            # dialog.execute() returned — dialog is still alive, just the modal loop exited

            if result.get("cancelled") or not result["proceed"]:
                try:
                    dialog.setVisible(False)
                    dialog.dispose()
                except Exception:
                    pass
                log_to_file("Enrollment wizard cancelled by user")
                return False, None, None, None, None

            log_to_file("Enrollment wizard steps 1-3 completed, keeping dialog for automatic steps")
            # After execute() returns, UNO hides the dialog — re-show it for steps 4-5
            try:
                dialog.setVisible(True)
            except Exception:
                pass
            return True, dialog, toolkit, _update_custom, result

        except Exception as e:
            log_to_file(f"Enrollment wizard failed, falling back to confirm: {str(e)}")
            proceed = self._confirm_message(
                _t("msg.connection_required_title"),
                _t("msg.connection_redirect_short")
            )
            return proceed, None, None, None, None

    def _keycloak_config(self, config_data):
        if not isinstance(config_data, dict):
            return {}

        def _flat_keycloak(source):
            if not isinstance(source, dict):
                return None
            flat = {
                "issuerUrl": (
                    source.get("keycloakIssuerUrl")
                    or source.get("issuerUrl")
                    or source.get("issuerURL")
                    or source.get("issuer_url")
                    or source.get("keycloak_base_url")
                    or source.get("issuer")
                ),
                "realm": (
                    source.get("keycloakRealm")
                    or source.get("keycloak_realm")
                    or source.get("realm")
                ),
                "clientId": (
                    source.get("keycloakClientId")
                    or source.get("keycloak_client_id")
                    or source.get("clientId")
                    or source.get("client_id")
                ),
                "clientSecret": (
                    source.get("keycloak_client_secret")
                    or source.get("clientSecret")
                    or source.get("client_secret")
                ),
                "authorization_endpoint": (
                    source.get("authorization_endpoint")
                    or source.get("authorizationEndpoint")
                    or source.get("keycloakAuthorizationEndpoint")
                    or source.get("keycloak_authorization_endpoint")
                    or source.get("auth_endpoint")
                    or source.get("authEndpoint")
                    or source.get("auth_url")
                    or source.get("authUrl")
                    or source.get("auth")
                ),
                "token_endpoint": (
                    source.get("token_endpoint")
                    or source.get("tokenEndpoint")
                    or source.get("keycloakTokenEndpoint")
                    or source.get("keycloak_token_endpoint")
                    or source.get("token_url")
                    or source.get("tokenUrl")
                    or source.get("token")
                ),
                "userinfo_endpoint": (
                    source.get("userinfo_endpoint")
                    or source.get("userinfoEndpoint")
                    or source.get("keycloakUserinfoEndpoint")
                    or source.get("keycloak_userinfo_endpoint")
                    or source.get("user_info_endpoint")
                    or source.get("userInfoEndpoint")
                    or source.get("userinfo")
                ),
            }
            if any(v is not None and str(v).strip() for v in flat.values()):
                return flat
            return None

        settings = local_config.select_settings(config_data)
        if isinstance(settings, dict):
            if isinstance(settings.get("keycloak"), dict):
                return settings.get("keycloak")
            settings_endpoints = settings.get("endpoints", {})
            if isinstance(settings_endpoints, dict) and isinstance(settings_endpoints.get("keycloak"), dict):
                return settings_endpoints.get("keycloak")
            flat_settings = _flat_keycloak(settings)
            if flat_settings:
                return flat_settings

        endpoints = config_data.get("endpoints", {})
        if not isinstance(endpoints, dict):
            endpoints = {}
        keycloak = config_data.get("keycloak") or endpoints.get("keycloak") or {}
        if isinstance(keycloak, dict):
            return keycloak

        flat_top_level = _flat_keycloak(config_data)
        if flat_top_level:
            return flat_top_level
        return {}

    def _keycloak_endpoint(self, keycloak_config, *names):
        for name in names:
            value = keycloak_config.get(name)
            if value:
                return value
        return ""

    def _normalize_keycloak_realm_base(self, base_url, realm):
        base_url = (base_url or "").strip()
        if not base_url:
            return ""
        base = base_url.rstrip("/")
        if "/realms/" in base:
            return base
        if realm:
            realm_value = str(realm).strip().strip("/")
            if realm_value:
                return f"{base}/realms/{realm_value}"
        return base

    def _keycloak_endpoints(self, config_data):
        keycloak = self._keycloak_config(config_data)

        # Auth endpoint : toujours issuer + realm
        # The PKCE authorization step opens the browser — it must navigate
        # to the real Keycloak SSO, never to the relay proxy.
        auth_endpoint = ""
        base_url = (
            self._get_config_from_file("keycloakIssuerUrl", "")
            or self._get_config_from_file("keycloak_base_url", "")
        )
        realm = (
            self._get_config_from_file("keycloakRealm", "")
            or self._get_config_from_file("keycloak_realm", "")
        )
        realm_base = self._normalize_keycloak_realm_base(base_url, realm)
        if realm_base:
            auth_endpoint = f"{realm_base}/protocol/openid-connect/auth"

        # Token endpoint : explicite d'abord (peut viser le relais)
        # Token exchange and refresh are programmatic HTTP calls from the
        # plugin — they can go through the relay proxy when configured.
        token_endpoint = self._keycloak_endpoint(
            keycloak,
            "token_endpoint",
            "tokenEndpoint",
            "token_url",
            "tokenUrl",
            "token"
        )
        if not token_endpoint:
            token_endpoint = (
                self._get_config_from_file("keycloakTokenEndpoint", "")
                or self._get_config_from_file("keycloak_token_endpoint", "")
                or self._get_config_from_file("token_endpoint", "")
                or self._get_config_from_file("tokenEndpoint", "")
            )
        if not token_endpoint and realm_base:
            token_endpoint = f"{realm_base}/protocol/openid-connect/token"

        return auth_endpoint, token_endpoint

    def _request_token(self, token_endpoint, data):
        if not token_endpoint:
            return None
        try:
            log_to_file(
                "Keycloak token request: "
                f"url={token_endpoint} "
                f"grant_type={data.get('grant_type','')} "
                f"client_id={data.get('client_id','')} "
                f"redirect_uri={data.get('redirect_uri','')}"
            )
            encoded = urllib.parse.urlencode(data).encode("utf-8")

            # WAF-safe mode: if token_endpoint is the /auth/token proxy,
            # wrap the form payload in a JSON envelope with base64 encoding.
            # This avoids the WAF blocking POST to URLs containing
            # /openid-connect/token.
            if token_endpoint.rstrip("/").endswith("/auth/token"):
                envelope = json.dumps({
                    "p": base64.b64encode(encoded).decode("ascii")
                }).encode("utf-8")
                request = urllib.request.Request(
                    token_endpoint,
                    data=envelope,
                    headers=_with_user_agent({"Content-Type": "application/json"})
                )
                log_to_file("Using WAF-safe /auth/token envelope")
            else:
                request = urllib.request.Request(
                    token_endpoint,
                    data=encoded,
                    headers=_with_user_agent({"Content-Type": "application/x-www-form-urlencoded"})
                )

            with self._urlopen(request, context=self.get_ssl_context(), timeout=20) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except Exception as e:
            log_to_file(f"Token request failed: {str(e)}")
            return None

    def _store_tokens(self, token_response):
        if not isinstance(token_response, dict):
            return
        access_token = token_response.get("access_token", "")
        refresh_token = token_response.get("refresh_token", "")
        if access_token:
            self.set_config("access_token", access_token)
        if refresh_token:
            self.set_config("refresh_token", refresh_token)

    def _clear_tokens(self):
        try:
            self.set_config("access_token", "")
            self.set_config("refresh_token", "")
            log_to_file("Keycloak tokens cleared")
        except Exception:
            pass

    def _token_email(self, access_token, userinfo_endpoint=None, allow_network=True):
        payload = self._jwt_payload(access_token)
        email = payload.get("email") or payload.get("preferred_username")
        verified = payload.get("email_verified", payload.get("emailVerified"))
        if email and (verified is None or verified is True):
            return email
        if userinfo_endpoint and allow_network:
            try:
                request = urllib.request.Request(
                    userinfo_endpoint,
                    headers=_with_user_agent({"Authorization": f"Bearer {access_token}"})
                )
                with self._urlopen(request, context=self.get_ssl_context(), timeout=10) as response:
                    payload = response.read().decode("utf-8")
                info = json.loads(payload)
                email = info.get("email") or info.get("preferred_username")
                verified = info.get("email_verified", info.get("emailVerified"))
                if email and (verified is None or verified is True):
                    return email
            except Exception as e:
                log_to_file(f"Userinfo request failed: {str(e)}")
        return None

    def _pkce_code_verifier(self):
        # RFC 7636 recommends 32-96 bytes of entropy; 96 bytes → 128-char base64url verifier
        raw = base64.urlsafe_b64encode(os.urandom(96)).decode("utf-8")
        return raw.rstrip("=")

    def _pkce_code_challenge(self, verifier):
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    def _wait_for_auth_code(self, redirect_uri, expected_state, timeout_seconds=120, tick=None, cancel_event=None, on_listening=None):
        try:
            parsed = urllib.parse.urlparse(redirect_uri)
            if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
                return None, "redirect_uri_invalid"
            port = parsed.port or 80
            path = parsed.path or "/"
        except Exception:
            return None, "redirect_uri_invalid"

        if cancel_event is None:
            cancel_event = threading.Event()

        done_event = threading.Event()
        result = {"code": None, "error": None}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                parsed_path = urllib.parse.urlparse(self.path)
                request_path = parsed_path.path or "/"
                expected_path = path or "/"
                log_to_file(f"PKCE callback received: path={request_path}")
                if request_path.rstrip("/") != expected_path.rstrip("/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed_path.query)
                # Sans le state de cette autorisation, le callback est forgé (CSRF).
                if params.get("state", [None])[0] != expected_state:
                    log_to_file("PKCE callback refused: state mismatch")
                    self.send_response(400)
                    self.end_headers()
                    return
                code = params.get("code", [None])[0]
                error = params.get("error", [None])[0]
                log_to_file(f"PKCE callback parsed: code={'set' if code else 'none'} error={error or 'none'}")
                result["code"] = code
                result["error"] = error
                if code or error:
                    done_event.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_render_callback_page().encode("utf-8"))

        # Boucle locale seulement : le callback porte le code d'autorisation.
        bind_host = "127.0.0.1"
        try:
            httpd = _CallbackServer((bind_host, port), Handler)
        except Exception as e:
            log_to_file(f"Failed to start local callback server: {str(e)}")
            return None, "callback_server_error"
        log_to_file(f"Local callback server listening on http://{bind_host}:{port}{path}")

        server_thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True
        )
        server_thread.start()

        try:
            if on_listening and not on_listening():
                return None, "browser_unavailable"
            start = time.time()
            while (
                time.time() - start < timeout_seconds
                and not done_event.is_set()
                and not cancel_event.is_set()
            ):
                if tick:
                    try:
                        tick()
                    except Exception:
                        pass
                time.sleep(0.1)
        finally:
            try:
                httpd.shutdown()
            except Exception:
                pass
            httpd.server_close()
            if server_thread.is_alive():
                try:
                    server_thread.join(timeout=1)
                except Exception:
                    pass

        if cancel_event.is_set() and not result["code"] and not result["error"]:
            return None, "cancelled_by_user"
        if result["error"]:
            return None, result["error"]
        if not result["code"]:
            return None, "timeout"
        return result["code"], None

    def _validate_redirect_uri(self, redirect_uri):
        try:
            parsed = urllib.parse.urlparse(redirect_uri)
            if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
                return redirect_uri
            host = parsed.hostname
            port = parsed.port or 80
            path = parsed.path or "/"
        except Exception:
            return redirect_uri
        return f"http://{host}:{port}{path}"

    def _select_redirect_uri(self, config_data=None):
        inner = None
        if isinstance(config_data, dict):
            inner = config_data.get("config", {}) if isinstance(config_data.get("config"), dict) else config_data
        redirect_uri = self._get_config_from_file("keycloak_redirect_uri", "")
        if not redirect_uri and inner is not None:
            redirect_uri = (
                inner.get("keycloak_redirect_uri")
                or inner.get("redirect_uri")
                or inner.get("redirectUri")
                or ""
            )
            if redirect_uri:
                log_to_file(f"redirect_uri resolved from DM config_data: {redirect_uri}")
        if not redirect_uri:
            return None
        allowed = self._get_config_from_file("keycloak_allowed_redirect_uri", [])
        if not allowed and inner is not None:
            allowed = (
                inner.get("keycloak_allowed_redirect_uri")
                or inner.get("allowed_redirect_uri")
                or inner.get("allowedRedirectUri")
                or []
            )
        if isinstance(allowed, str):
            allowed = [u.strip() for u in allowed.split(",") if u.strip()]
        if isinstance(allowed, list) and allowed:
            if redirect_uri not in allowed:
                self._show_message(
                    _t("msg.kc_invalid_title"),
                    _t("msg.kc_invalid_body")
                )
                return None
        valid = self._validate_redirect_uri(redirect_uri)
        log_to_file(f"Keycloak redirect_uri selected: {valid}")
        return valid

    def _authorization_code_flow(self, config_data):
        auth_endpoint, token_endpoint = self._keycloak_endpoints(config_data)
        if not auth_endpoint or not token_endpoint:
            log_to_file("Keycloak auth endpoints missing; cannot open browser")
            self._show_message(
                _t("msg.kc_incomplete_title"),
                _t("msg.kc_endpoints_missing")
            )
            return None

        client_id = self._get_config_from_file("keycloakClientId", "")
        if not client_id:
            log_to_file("Keycloak client_id missing; cannot open browser")
            self._show_message(
                _t("msg.kc_incomplete_title"),
                _t("msg.kc_client_id_missing")
            )
            return None

        redirect_uri = self._select_redirect_uri(config_data)
        if not redirect_uri:
            log_to_file("Keycloak redirect_uri missing; cannot open browser")
            self._show_message(
                _t("msg.kc_incomplete_title"),
                _t("msg.kc_redirect_missing")
            )
            return None

        is_first_enrollment = not self._as_bool(self._get_config_from_file("enrolled", False))
        wiz_dialog = wiz_toolkit = wiz_update = wiz_state = None
        if is_first_enrollment:
            proceed, wiz_dialog, wiz_toolkit, wiz_update, wiz_state = self._show_enrollment_wizard()
        else:
            proceed = self._confirm_message(
                _t("msg.connection_required_title"),
                _t("msg.connection_required_body")
            )
        if not proceed:
            log_to_file("Keycloak auth canceled by user before browser open")
            return None

        code_verifier = self._pkce_code_verifier()
        code_challenge = self._pkce_code_challenge(code_verifier)
        state = uuid.uuid4().hex

        query = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state
        })
        auth_url = f"{auth_endpoint}?{query}"
        log_to_file(
            "Keycloak auth URL built: "
            f"auth_endpoint={auth_endpoint} client_id={client_id} redirect_uri={redirect_uri}"
        )
        auth_cancel_event = threading.Event()

        # Étape 4/5 : attente du callback Keycloak
        # Si le wizard est actif, on l'utilise comme dialog d'attente.
        # Sinon on crée un dialog séparé (fallback re-login).
        wait_dialog = None

        if wiz_dialog and wiz_update and wiz_toolkit:
            wiz_update(
                _t("enroll.auth_wait_title"),
                _t("enroll.auth_wait_text"),
                _t("enroll.step4_label"),
                4,
                btn_cancel=_t("common.cancel"),
            )

            class _WizAuthCancelListener(unohelper.Base, XActionListener):
                def actionPerformed(self, event):
                    auth_cancel_event.set()
                def disposing(self, event):
                    return

            try:
                wiz_dialog.getControl("wiz_btn_cancel").addActionListener(
                    _WizAuthCancelListener()
                )
            except Exception:
                pass

            tick_state = {"i": 0}

            def _tick():
                tick_state["i"] += 1
                dots = "." * ((tick_state["i"] % 3) + 1)
                try:
                    wiz_dialog.getControl("wiz_text").getModel().Label = _t(
                        "enroll.auth_waiting", dots=dots
                    )
                    pump_events(wiz_toolkit)
                except Exception:
                    pass

        else:
            # Fallback : dialog séparé (re-login sans wizard)
            def _show_auth_wait_dialog():
                try:
                    from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
                    ctx = uno.getComponentContext()
                    create = ctx.getServiceManager().createInstanceWithContext
                    dlg = create("com.sun.star.awt.UnoControlDialog")
                    dlg_model = create("com.sun.star.awt.UnoControlDialogModel")
                    dlg.setModel(dlg_model)
                    dlg.setVisible(False)
                    dlg.setTitle("")
                    dlg.setPosSize(0, 0, 300, 120, SIZE)
                    lbl_m = dlg_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
                    dlg_model.insertByName("auth_wait_label", lbl_m)
                    lbl_m.Label = _t("enroll.auth_progress", dots="...")
                    lbl_m.NoLabel = True
                    lbl = dlg.getControl("auth_wait_label")
                    lbl.setPosSize(10, 24, 280, 20, POSSIZE)
                    btn_m = dlg_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
                    dlg_model.insertByName("auth_wait_cancel", btn_m)
                    btn_m.Label = _t("common.cancel")
                    btn = dlg.getControl("auth_wait_cancel")
                    btn.setPosSize(100, 72, 100, 26, POSSIZE)
                    frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
                    window = frame.getContainerWindow() if frame else None
                    tk = create("com.sun.star.awt.Toolkit")
                    dlg.createPeer(tk, window)
                    if window:
                        ps = window.getPosSize()
                        dlg.setPosSize(ps.Width / 2 - 150, ps.Height / 2 - 60, 0, 0, POS)
                    dlg.setVisible(True)
                    return dlg, lbl, btn, tk
                except Exception:
                    return None, None, None, None

            class CancelAuthListener(unohelper.Base, XActionListener):
                def actionPerformed(self, event):
                    auth_cancel_event.set()
                def disposing(self, event):
                    return

            wait_dialog, wait_label, wait_cancel_btn, wait_toolkit = _show_auth_wait_dialog()
            if wait_cancel_btn:
                try:
                    wait_cancel_btn.addActionListener(CancelAuthListener())
                except Exception:
                    pass
            tick_state = {"i": 0}

            def _tick():
                if not wait_label or not wait_toolkit:
                    return
                tick_state["i"] += 1
                dots = "." * ((tick_state["i"] % 3) + 1)
                try:
                    if auth_cancel_event.is_set():
                        wait_label.getModel().Label = _t("enroll.cancelling")
                    else:
                        wait_label.getModel().Label = _t("enroll.auth_progress", dots=dots)
                    pump_events(wait_toolkit)
                except Exception:
                    pass

        auth_timeout_seconds = 180
        try:
            auth_timeout_seconds = int(self._get_config_from_file("keycloak_auth_timeout_seconds", 180))
        except Exception:
            auth_timeout_seconds = 180
        if auth_timeout_seconds < 60:
            auth_timeout_seconds = 60

        # Ouvert une fois le serveur de callback à l'écoute : avec une session SSO
        # déjà ouverte, Keycloak redirige aussitôt vers le callback.
        code, error = self._wait_for_auth_code(
            redirect_uri, state,
            timeout_seconds=auth_timeout_seconds,
            tick=_tick,
            cancel_event=auth_cancel_event,
            on_listening=lambda: open_url(self.ctx, auth_url),
        )

        if wait_dialog:
            try:
                wait_dialog.setVisible(False)
                wait_dialog.dispose()
            except Exception:
                pass

        def _wiz_dispose():
            try:
                wiz_dialog.setVisible(False)
                wiz_dialog.dispose()
            except Exception:
                pass

        def _wiz_show_error_and_wait(title, text):
            """Affiche une erreur dans le wizard, attend Fermer, ferme le dialog."""
            if not wiz_dialog or not wiz_update or not wiz_toolkit:
                return
            wiz_update(title, text, _t("enroll.step4_label"), 4, btn_next=_t("common.close"))
            wiz_state["cancelled"] = False
            step_snap = wiz_state["step"]
            while wiz_state["step"] == step_snap:
                pump_events(wiz_toolkit)
                time.sleep(0.1)
            _wiz_dispose()

        if error == "cancelled_by_user":
            log_to_file("Authorization code flow cancelled by user")
            _wiz_show_error_and_wait(
                _t("enroll.cancelled_title"),
                _t("enroll.cancelled_text"),
            )
            return None
        if not code:
            log_to_file(f"Authorization code flow failed: {error}")
            err_txt = (
                _t("enroll.timeout_text")
                if error == "timeout"
                else _t("msg.no_browser")
                if error == "browser_unavailable"
                else _t("enroll.error_config_text", error=error or _t("enroll.unknown_error"))
            )
            if wiz_dialog:
                _wiz_show_error_and_wait(_t("enroll.failed_conn_title"), err_txt)
            elif error == "timeout":
                self._show_message(
                    _t("msg.kc_expired_title"),
                    _t("msg.kc_expired_body", redirect_uri=redirect_uri),
                )
            else:
                self._show_message(_t("enroll.failed_conn_title"), err_txt)
            return None
        log_to_file("Authorization code received, exchanging for token")

        token_payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier
        }
        token_response = self._request_token(token_endpoint, token_payload)
        if isinstance(token_response, dict) and token_response.get("access_token"):
            self._store_tokens(token_response)
            access_token = token_response.get("access_token")
            try:
                if wiz_dialog and wiz_update and wiz_toolkit and wiz_state:
                    wiz_update(
                        _t("enroll.enrolling_title"),
                        _t("enroll.enrolling_text"),
                        _t("enroll.step5_enroll_label"),
                        5,
                    )
                    enroll_result = {"done": False, "success": False, "error": ""}

                    def _enroll_worker():
                        try:
                            self._ensure_device_management_state()
                            enroll_result["success"] = self._relay_credentials_valid()
                            if not enroll_result["success"]:
                                enroll_result["error"] = _t("enroll.not_confirmed")
                        except Exception as exc:
                            enroll_result["success"] = False
                            enroll_result["error"] = str(exc)
                        finally:
                            enroll_result["done"] = True

                    threading.Thread(target=_enroll_worker, daemon=True).start()

                    tick_i = [0]
                    while not enroll_result["done"]:
                        tick_i[0] += 1
                        dots = "." * ((tick_i[0] % 3) + 1)
                        try:
                            wiz_dialog.getControl("wiz_text").getModel().Label = _t(
                                "enroll.enrolling_progress", dots=dots
                            )
                            pump_events(wiz_toolkit)
                        except Exception:
                            pass
                        time.sleep(0.4)

                    wiz_state["cancelled"] = False
                    step_snap = wiz_state["step"]
                    if enroll_result["success"]:
                        wiz_update(
                            _t("enroll.done_title"),
                            _t("enroll.done_text"),
                            _t("enroll.step5_done_label"),
                            5,
                            btn_next=_t("enroll.done_button"),
                            title_color=_UI["success"],
                        )
                    else:
                        error_msg = enroll_result["error"] or _t("enroll.unknown_error")
                        wiz_update(
                            _t("enroll.failed_title"),
                            _t("enroll.failed_text", reason=error_msg),
                            _t("enroll.step5_error_label"),
                            5,
                            btn_next=_t("common.close"),
                            title_color=_UI["error"],
                        )

                    while wiz_state["step"] == step_snap:
                        pump_events(wiz_toolkit)
                        time.sleep(0.1)

                    _wiz_dispose()

                else:
                    self._ensure_device_management_state_async()
            except Exception as exc:
                log_to_file(f"Post-SSO enroll scheduling failed: {str(exc)}")
            return access_token
        return None

    def _ensure_access_token(self, config_data, interactive=True):
        access_token = str(self._get_config_from_file("access_token", "")).strip()
        if access_token and not self._token_is_expired(access_token):
            return access_token

        refresh_token = str(self._get_config_from_file("refresh_token", "")).strip()
        keycloak = self._keycloak_config(config_data)
        _, token_endpoint = self._keycloak_endpoints(config_data)
        client_id = (
            keycloak.get("client_id")
            or keycloak.get("clientId")
            or self._get_config_from_file("keycloakClientId", "")
            or self._get_config_from_file("keycloak_client_id", "")
            or self._get_config_from_file("client_id", "")
        )
        client_secret = (
            keycloak.get("client_secret")
            or keycloak.get("clientSecret")
            or self._get_config_from_file("keycloak_client_secret", "")
            or self._get_config_from_file("client_secret", "")
        )

        if refresh_token:
            refresh_payload = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id
            }
            if client_secret:
                refresh_payload["client_secret"] = client_secret
            token_response = self._request_token(token_endpoint, refresh_payload)
            if isinstance(token_response, dict) and token_response.get("access_token"):
                self._store_tokens(token_response)
                return token_response.get("access_token")

        if not interactive:
            log_to_file("Access token unavailable (interactive login disabled for this flow)")
            return None

        now = time.time()
        with self._auth_prompt_lock:
            if self._auth_prompt_in_progress or (now - self._auth_prompted_at) < 30:
                log_to_file("Auth prompt suppressed (already shown recently)")
                return None
            self._auth_prompt_in_progress = True
            self._auth_prompted_at = now
        try:
            auth_code_token = self._authorization_code_flow(config_data)
        finally:
            with self._auth_prompt_lock:
                self._auth_prompt_in_progress = False
        if auth_code_token:
            return auth_code_token

        log_to_file("Authentication aborted: no token obtained from browser SSO flow")
        return None

    def _ensure_device_management_state_async(self):
        def _worker():
            try:
                self._ensure_device_management_state()
            except Exception as exc:
                log_to_file(f"Failed to initialize device management (async): {str(exc)}")

        threading.Thread(target=_worker, daemon=True).start()

    def _ensure_device_management_state(self, force_enroll=False):
        """Synchronise l'état DM et enrôle le poste si nécessaire.

        `force_enroll` outrepasse le court-circuit « déjà enrôlé » : utilisé par
        la récupération d'auth, quand le DM nous a explicitement signalé que nos
        creds relay sont absents ou refusés.
        """
        if not self._device_management_enabled():
            return
        config_data = self._fetch_config()
        if not config_data:
            return

        access_token = self._ensure_access_token(config_data, interactive=False)
        keycloak = self._keycloak_config(config_data)
        userinfo_endpoint = self._keycloak_endpoint(
            keycloak,
            "userinfo_endpoint",
            "userinfoEndpoint",
            "user_info_endpoint",
            "userInfoEndpoint",
            "userinfo"
        )
        email = self._token_email(access_token, userinfo_endpoint) if access_token else None
        if not email:
            log_to_file("Device management token email verification failed")
            return

        bootstrap_url = str(self._active_bootstrap_url() or "").strip().rstrip("/")
        settings = local_config.select_settings(config_data) if isinstance(config_data, dict) else {}

        enroll_endpoint = ""
        sources = []
        if isinstance(settings, dict):
            sources.append(settings)
        if isinstance(config_data, dict):
            sources.append(config_data)

        for source in sources:
            endpoints = source.get("endpoints", {})
            if isinstance(endpoints, dict):
                enroll_endpoint = str(
                    endpoints.get("enroll")
                    or endpoints.get("enroll_endpoint")
                    or endpoints.get("enrollEndpoint")
                    or ""
                ).strip()
            if enroll_endpoint:
                break
            enroll_endpoint = str(
                source.get("enroll")
                or source.get("enroll_endpoint")
                or source.get("enrollEndpoint")
                or ""
            ).strip()
            if enroll_endpoint:
                break

        if enroll_endpoint.startswith("/") and bootstrap_url:
            enroll_endpoint = bootstrap_url + enroll_endpoint
        if not enroll_endpoint and bootstrap_url:
            enroll_endpoint = bootstrap_url + "/enroll"
            log_to_file(f"Device management enroll endpoint fallback applied: {enroll_endpoint}")

        if not enroll_endpoint:
            return

        # Court-circuit sur les CREDS RELAY, pas sur le drapeau `enrolled`, que
        # les versions précédentes posaient sans creds relay : un poste marqué
        # enrôlé mais sans creds relay est dans un état absorbant — le DM
        # ne minte alors jamais de llmToken et tout /llm/v1 tombe en 401, sans
        # aucun chemin de sortie. On re-tente donc l'enrôlement (POST /enroll est
        # idempotent côté DM : il ré-émet une paire).
        if self._relay_credentials_valid() and not force_enroll:
            if not self._as_bool(self._get_config_from_file("enrolled", False)):
                self.set_config("enrolled", True)
            return
        if self._as_bool(self._get_config_from_file("enrolled", False)):
            log_to_file(
                "[ENROLL] enrolled=True mais creds relay absents/expirés"
                f"{' (ré-enrôlement forcé)' if force_enroll else ''} — nouvel enrôlement"
            )

        inner = config_data.get("config", {}) if isinstance(config_data, dict) else {}
        device_name = (
            config_data.get("device_name")
            or config_data.get("deviceName")
            or inner.get("device_name")
            or inner.get("deviceName")
            or self._get_config_from_file("device_name", "")
        )
        plugin_uuid = self._ensure_extension_uuid()

        enroll_payload = {
            "device_name": device_name,
            "plugin_uuid": plugin_uuid,
            "email": email
        }
        log_to_file(f"Device management enroll payload: device_name={device_name} plugin_uuid={plugin_uuid} has_email={bool(email)} has_token={bool(access_token)} endpoint={enroll_endpoint}")
        try:
            json_data = json.dumps(enroll_payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            request = urllib.request.Request(enroll_endpoint, data=json_data, headers=_with_user_agent(headers))
            with self._urlopen(request, context=self.get_ssl_context(), timeout=10) as response:
                raw = response.read().decode("utf-8", errors="ignore")
            relay_client_id = ""
            relay_client_key = ""
            relay_expires_at = 0
            try:
                payload = json.loads(raw) if raw else {}
                if isinstance(payload, dict):
                    relay = payload.get("relay") if isinstance(payload.get("relay"), dict) else {}
                    relay_client_id = str(
                        payload.get("relayClientId")
                        or relay.get("client_id")
                        or ""
                    ).strip()
                    relay_client_key = str(
                        payload.get("relayClientKey")
                        or relay.get("client_key")
                        or ""
                    ).strip()
                    relay_expires = payload.get("relayKeyExpiresAt") or relay.get("expires_at") or 0
                    try:
                        relay_expires_at = int(relay_expires)
                    except Exception:
                        relay_expires_at = 0
            except Exception:
                pass
            if relay_client_id and relay_client_key:
                self.set_config("relay_client_id", relay_client_id)
                self.set_config("relay_client_key", relay_client_key)
                if relay_expires_at > 0:
                    self.set_config("relay_key_expires_at", relay_expires_at)
                self.set_config("enrolled", True)
                log_to_file("Device management enroll succeeded with relay credentials")
                # Immediately fetch config with new relay creds to sync LLM token
                try:
                    self._fetch_config(force=True)
                except Exception as _e:
                    log_to_file(f"Post-enroll config refresh failed: {_e}")
                token_after = str(self.get_config("llm_api_tokens", "") or "").strip()
                log_to_file(
                    f"[ENROLL] post-enroll llmToken={'obtenu' if token_after else 'TOUJOURS ABSENT'}"
                )
            else:
                # Enrôlement à moitié : accepté par le DM mais sans creds relay ;
                # tracé explicitement, le DM ne mintera aucun llmToken tant qu'il dure.
                log_to_file(
                    "Device management enroll succeeded WITHOUT relay credentials — "
                    "le DM ne pourra minter aucun llmToken (relais désactivé côté "
                    "serveur ?) ; l'enrôlement sera re-tenté"
                )
        except Exception as e:
            error_body = ""
            if hasattr(e, "read"):
                try:
                    error_body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
            log_to_file(f"Device management enroll failed: {str(e)} body_len={len(error_body)}")

    def _get_openwebui_access_token(self):
        if not self._device_management_enabled():
            return ""
        config_data = self._fetch_config() or {}
        token = self._ensure_access_token(config_data, interactive=False) or ""
        if token:
            return token
        fallback_token = str(self._get_config_from_file("access_token", "")).strip()
        if fallback_token and not self._token_is_expired(fallback_token):
            log_to_file("Using local cached access_token (DM config unavailable)")
            return fallback_token
        return ""

    def _effective_api_token(self, preferred_token=""):
        token = str(preferred_token or "").strip()
        if token:
            return token
        if self._llm_proxy_mode():
            # Le proxy DM /llm/v1 n'accepte QUE le llmToken HMAC qu'il a minté
            # (app/llm/tokens.py : format payload_b64.sig_b64). Un access_token
            # Keycloak est un JWT à 3 segments : la vérification de signature
            # échoue et le 401 renvoyé accuse le token au lieu de l'enrôlement.
            # On refuse donc ce repli, qui ne peut structurellement pas marcher.
            log_to_file(
                "[llm-auth] aucun llmToken et proxy DM actif — repli sur "
                "l'access_token Keycloak refusé (format incompatible)"
            )
            return ""
        return str(self._get_openwebui_access_token() or "").strip()

    @staticmethod
    def _token_expired_at(raw_expires_at, skew_seconds=60):
        """True si l'horodatage d'expiration (epoch) est atteint.

        Absent ou <= 0 = expiration inconnue → False : on ne périme jamais un
        credential sur une absence d'information, c'est le serveur qui tranche.
        """
        try:
            expires_at = int(raw_expires_at or 0)
        except (TypeError, ValueError):
            return False
        if expires_at <= 0:
            return False
        return time.time() >= (expires_at - skew_seconds)

    def _llm_proxy_mode(self):
        """True quand le DM annonce SON proxy /llm/v1 comme endpoint LLM.

        Deux signaux, du plus fiable au plus robuste : la clé `llmToken` que le
        DM ne pose que dans ce mode (app/main.py _apply_llm_proxy_overrides), et
        à défaut la forme de l'endpoint (<bootstrap>/llm/v1) quand le cache DM
        est froid.
        """
        settings = local_config.select_settings(self.config_cache)
        if isinstance(settings, dict) and "llmToken" in settings:
            return True
        endpoint = str(self._get_config_from_file("llm_base_urls", "") or "").strip().rstrip("/")
        if not endpoint.endswith("/llm/v1"):
            return False
        bootstrap = str(self._active_bootstrap_url() or "").strip().rstrip("/")
        return bool(bootstrap) and endpoint.startswith(bootstrap)

    def _relay_credentials_valid(self, skew_seconds=300):
        """True si le poste a des credentials relay exploitables.

        C'est LA source de vérité de l'enrôlement effectif : le drapeau
        `enrolled` n'est posé qu'avec eux. Sans ces creds,
        /config repart sans X-Relay-*, le DM ne mint aucun llmToken, et tous les
        appels /llm/v1 finissent en 401.
        """
        client_id = str(self._get_config_from_file("relay_client_id", "") or "").strip()
        client_key = str(self._get_config_from_file("relay_client_key", "") or "").strip()
        if not client_id or not client_key:
            return False
        return not self._token_expired_at(
            self._get_config_from_file("relay_key_expires_at", 0), skew_seconds
        )

    def _resolve_llm_token(self, default):
        """Résout le llmToken en gardant token et expiration SOLIDAIRES.

        Le llmToken est court (TTL DM 3600 s par défaut) alors que le cache de
        config vit 300 s : le servir sans vérifier son expiration produit un 401
        `invalid_api_key` que rien ne rattrape. Token et `llmTokenExpiresAt`
        sont donc lus depuis la même source, cache DM d'abord puis disque.
        """
        cached = self._get_setting("llm_api_tokens")
        settings = local_config.select_settings(self.config_cache) or {}
        if cached and len(str(cached)) >= 6:
            if not self._token_expired_at(settings.get("llmTokenExpiresAt")):
                return cached
            log_to_file("[llm-auth] llmToken du cache DM expiré — refresh forcé")
            self._schedule_config_refresh(force=True, reason="llm_token_expired")
        remembered = credentials.recall(credentials.DM_LLM_TOKEN)
        if remembered:
            return remembered
        if credentials.expires_at(credentials.DM_LLM_TOKEN):
            log_to_file("[llm-auth] llmToken mémorisé expiré — refresh forcé")
            self._schedule_config_refresh(force=True, reason="llm_token_expired")
        return self._get_config_from_file("llm_api_tokens", default)

    def _llm_auth_debug(self):
        """Une ligne sans ambiguïté sur le credential retenu pour l'appel LLM.

        Distingue les trois cas que les logs confondaient : jeton présent,
        aucun jeton faute d'enrôlement relais, et mode direct hors proxy DM.
        """
        proxy = self._llm_proxy_mode()
        relay = "yes" if self._relay_credentials_valid() else "no"
        token = str(self.get_config("llm_api_tokens", "") or "").strip()
        if not token:
            vector = "none"
            detail = ""
        elif proxy:
            vector = "llmToken"
            expires_at = self._get_config_from_file("llmTokenExpiresAt", 0)
            try:
                remaining = int(expires_at or 0) - int(time.time())
            except (TypeError, ValueError):
                remaining = 0
            detail = f" expires_in={remaining}s" if remaining else ""
        else:
            vector = "api_key"
            detail = ""
        return (f"vector={vector}{detail} proxy_mode={proxy} relay_creds={relay} "
                f"enrolled={self._as_bool(self._get_config_from_file('enrolled', False))}")

    def _check_relay_auth_notice(self, config_data):
        """Réagit au signal d'auth manquante que le DM place dans /config.

        Le DM répond `_auth_notice` (+ `llmToken:""`) quand la requête /config
        n'a pas présenté de paire X-Relay-Client/Key valide. C'est le diagnostic
        le plus fiable dont dispose le plugin : sans creds relay, aucun llmToken
        ne sera jamais minté et TOUS les appels /llm/v1 finiront en 401. Ignorer
        ce signal, c'est rester bloqué indéfiniment.
        """
        if not self._device_management_enabled():
            return False
        inner = config_data.get("config", {}) if isinstance(config_data, dict) else {}
        if not isinstance(inner, dict):
            return False
        notice = str(inner.get("_auth_notice", "") or "").strip()
        proxy_mode = "llmToken" in inner
        minted = str(inner.get("llmToken", "") or "").strip()
        if not notice and not (proxy_mode and not minted):
            return False
        if self._relay_credentials_valid():
            # Deux causes possibles, indiscernables côté client : creds révoqués
            # côté serveur, ou DM_LLM_TOKEN_SIGNING_KEY absente côté DM (le mint
            # rend alors "" sans erreur). Le ré-enrôlement traite la première ;
            # la seconde se voit dans les logs DM.
            log_to_file(
                "[ENROLL] le DM n'a minté aucun llmToken malgré des creds relay "
                "valides — creds révoqués, ou clé de signature absente côté DM ; "
                "ré-enrôlement"
            )
        else:
            log_to_file(
                "[ENROLL] aucun llmToken minté et aucun cred relay valide — "
                "ré-enrôlement planifié"
            )
        return self._schedule_relay_recovery()

    def _schedule_relay_recovery(self, min_interval_seconds=900):
        """Relance un enrôlement en tâche de fond pour récupérer des creds relay.

        Jamais sur le thread appelant (/config tourne déjà dans un worker, et le
        POST /enroll est bloquant). Backoff long : un DM délibérément sans relais
        renverra toujours le même signal, on ne le matraque pas.
        """
        now = time.time()
        with self._relay_recovery_lock:
            if self._relay_recovery_in_progress:
                return False
            if now - self._relay_recovery_last_at < min_interval_seconds:
                log_to_file("[ENROLL] relay recovery ignorée (backoff)")
                return False
            self._relay_recovery_in_progress = True
            self._relay_recovery_last_at = now

        def _worker():
            try:
                self._ensure_device_management_state(force_enroll=True)
            except Exception as exc:
                log_to_file(f"[ENROLL] relay recovery échouée: {str(exc)}")
            finally:
                self._relay_recovery_in_progress = False

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def _recover_llm_auth(self, min_interval_seconds=30):
        """Restaure un credential LLM exploitable après un 401 du proxy DM.

        APPELÉ DEPUIS LE THREAD RÉSEAU du pump SSE, jamais depuis le thread
        principal : les deux étapes font du réseau bloquant et gèleraient
        LibreOffice (cf. core/sse_pump.py).

        1. /config forcé — suffit quand les creds relay sont bons : le DM mint un
           llmToken frais à chaque réponse /config authentifiée.
        2. ré-enrôlement synchrone — nécessaire quand les creds relay manquent ou
           ont été révoqués, sans quoi l'étape 1 ne rendra jamais de token.
        """
        now = time.time()
        with self._llm_auth_recovery_lock:
            if now - self._llm_auth_recovery_last_at < min_interval_seconds:
                log_to_file("[llm-auth] recovery ignorée (backoff)")
                return bool(str(self.get_config("llm_api_tokens", "") or "").strip())
            self._llm_auth_recovery_last_at = now

        try:
            self._fetch_config(force=True)
        except Exception as exc:
            log_to_file(f"[llm-auth] recovery: refresh /config échoué: {str(exc)}")
        if str(self.get_config("llm_api_tokens", "") or "").strip():
            log_to_file("[llm-auth] recovery: llmToken renouvelé via /config")
            return True

        if not self._relay_credentials_valid():
            log_to_file("[llm-auth] recovery: creds relay absents/expirés — ré-enrôlement")
            try:
                self._ensure_device_management_state(force_enroll=True)
            except Exception as exc:
                log_to_file(f"[llm-auth] recovery: ré-enrôlement échoué: {str(exc)}")
                return False

        ok = bool(str(self.get_config("llm_api_tokens", "") or "").strip())
        log_to_file(f"[llm-auth] recovery {'réussie' if ok else 'échouée'} — {self._llm_auth_debug()}")
        return ok

    def _auth_header(self):
        name = str(self.get_config("authHeaderName", "Authorization")).strip() or "Authorization"
        prefix = str(self.get_config("authHeaderPrefix", "Bearer ")).strip() or "Bearer"
        if prefix and not prefix.endswith(" "):
            prefix = prefix + " "
        return name, prefix

    def _relay_headers(self):
        relay_client_id = str(self._get_config_from_file("relay_client_id", "") or "").strip()
        relay_client_key = str(self._get_config_from_file("relay_client_key", "") or "").strip()
        if not relay_client_id or not relay_client_key:
            log_to_file(f"[RELAY] no relay creds: id={'yes' if relay_client_id else 'no'} key={'yes' if relay_client_key else 'no'}")
            return {}
        if self._token_expired_at(self._get_config_from_file("relay_key_expires_at", 0)):
            # On envoie quand même : le serveur reste l'autorité sur la validité.
            # La trace sert à distinguer « creds absents » de « creds périmés ».
            log_to_file("[RELAY] relay creds expirés d'après relay_key_expires_at")
        log_to_file(f"[RELAY] injecting relay headers: id={relay_client_id[:12]}...")
        return {
            "X-Relay-Client": relay_client_id,
            "X-Relay-Key": relay_client_key,
        }

    def _as_bool(self, value):
        return local_config.truthy(value)

    def _get_proxy_config(self):
        """Proxy coupé, identifiant et mot de passe ne sont pas lus : pas de
        lecture du coffre de l'OS à chaque requête HTTP."""
        enabled = self._as_bool(self._get_config_from_file("proxy_enabled", False))
        proxy_url = str(self._get_config_from_file("proxy_url", "")).strip()
        username = password = ""
        if enabled:
            username = str(self._get_config_from_file("proxy_username", "")).strip()
            password = str(self._get_config_from_file("proxy_password", ""))
        allow_insecure = self._as_bool(self._get_config_from_file("proxy_allow_insecure_ssl", False))
        return {
            "enabled": enabled,
            "proxy_url": proxy_url,
            "username": username,
            "password": password,
            "allow_insecure_ssl": allow_insecure,
        }

    def _normalize_proxy_url(self, proxy_url):
        proxy_url = (proxy_url or "").strip()
        if not proxy_url:
            return ""
        if "://" not in proxy_url:
            proxy_url = "http://" + proxy_url
        try:
            parsed = urllib.parse.urlparse(proxy_url)
            host = parsed.hostname or ""
            port = parsed.port
            if not host:
                return ""
            if port:
                return f"{parsed.scheme}://{host}:{port}"
            return f"{parsed.scheme}://{host}"
        except Exception:
            return proxy_url

    def _build_proxy_opener(self, proxy_cfg, context=None):
        handlers = []
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        if not proxy_cfg.get("enabled"):
            log_to_file("[PROXY] disabled")
            return urllib.request.build_opener(*handlers)
        proxy_url = self._normalize_proxy_url(proxy_cfg.get("proxy_url", ""))
        if not proxy_url:
            log_to_file("[PROXY] enabled but proxy_url is empty/invalid")
            return urllib.request.build_opener(*handlers)
        proxy_map = {"http": proxy_url, "https": proxy_url}
        handlers.append(urllib.request.ProxyHandler(proxy_map))
        username = proxy_cfg.get("username", "")
        password = proxy_cfg.get("password", "")
        if username and password:
            try:
                pwd_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
                pwd_mgr.add_password(None, proxy_url, username, password)
                handlers.append(urllib.request.ProxyBasicAuthHandler(pwd_mgr))
                handlers.append(urllib.request.ProxyDigestAuthHandler(pwd_mgr))
                log_to_file("[PROXY] auth enabled (username+password)")
            except Exception:
                pass
        else:
            log_to_file("[PROXY] auth disabled (empty username or password)")
        try:
            if username and password:
                parsed = urllib.parse.urlparse(proxy_url)
                host = parsed.hostname or ""
                port = f":{parsed.port}" if parsed.port else ""
                proxy_url_auth = f"{parsed.scheme}://{username}:{password}@{host}{port}"
                proxy_map = {"http": proxy_url_auth, "https": proxy_url_auth}
                handlers[-1] = urllib.request.ProxyHandler(proxy_map)
        except Exception:
            pass
        log_to_file(f"[PROXY] using {proxy_url}")
        return urllib.request.build_opener(*handlers)

    def _urlopen(self, request, context=None, timeout=None, use_proxy=True):
        req_url = ""
        try:
            req_url = str(getattr(request, "full_url", "") or "")
            # NE PAS étendre cette règle à /llm/v1 : le trafic LLM s'authentifie
            # avec le llmToken SEUL (scopé "llm", TTL 1 h). Surface d'attaque :
            # la paire relay est le credential maître (config + télémétrie + LLM)
            # et vit 30 jours. De plus, côté DM, la présence de X-Relay-Client
            # engage la branche relais qui échoue en 401 SANS repli vers le
            # Bearer : les en-têtes masqueraient donc un llmToken valide.
            if "/relay-assistant/" in req_url:
                for header_name, header_value in self._relay_headers().items():
                    try:
                        request.add_header(header_name, header_value)
                    except Exception:
                        pass
        except Exception:
            pass
        proxy_cfg = self._get_proxy_config() if use_proxy else _PROXY_DISABLED
        allow_insecure = bool(proxy_cfg.get("allow_insecure_ssl"))
        if proxy_cfg.get("enabled"):
            username = proxy_cfg.get("username", "")
            password = proxy_cfg.get("password", "")
            if username and password:
                try:
                    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
                    if not request.has_header("Proxy-Authorization"):
                        request.add_header("Proxy-Authorization", f"Basic {token}")
                except Exception:
                    pass
        if context is None:
            context = self.get_ssl_context()
        opener = self._build_proxy_opener(proxy_cfg, context=context)
        log_to_file(
            f"[PROXY] request url={req_url} enabled={proxy_cfg.get('enabled')} "
            f"insecure_ssl={allow_insecure} use_proxy={use_proxy}"
        )
        if timeout is None:
            return opener.open(request)
        return opener.open(request, timeout=timeout)

    def _schedule_enrollment_check(self):
        """Deferred enrollment check — fires ~3s after init to let UI start."""
        def _deferred_enrollment():
            try:
                if self.runtime.enrollment_dismissed:
                    return
                if not self._needs_first_enrollment():
                    log_to_file("[ENROLL] Auto-check: already enrolled, skipping wizard")
                    return
                with self.runtime.enrollment_wizard_lock:
                    if self.runtime.enrollment_wizard_active:
                        log_to_file("[ENROLL] Auto-check: wizard already running, skipping")
                        return
                    self.runtime.enrollment_wizard_active = True
                try:
                    log_to_file("[ENROLL] Auto-check: first enrollment needed, launching wizard")
                    if not self._run_first_enrollment():
                        self.runtime.enrollment_dismissed = True
                        log_to_file("[ENROLL] Auto-check: wizard cancelled by user")
                    else:
                        log_to_file("[ENROLL] Auto-check: enrollment succeeded")
                finally:
                    self.runtime.enrollment_wizard_active = False
            except Exception as e:
                log_to_file(f"[ENROLL] Auto-check failed: {str(e)}")

        timer = threading.Timer(3.0, _deferred_enrollment)
        timer.daemon = True
        timer.start()

    def _split_endpoint_api_path(self, endpoint, is_openwebui):
        endpoint = (endpoint or "").rstrip("/")
        if endpoint.endswith("/api") or endpoint.endswith("/v1"):
            return endpoint, ""
        api_path = "/api" if is_openwebui else "/v1"
        return endpoint, api_path

    def _build_auth_headers(self, api_key):
        """Build standard JSON + auth headers for API calls."""
        headers = {"Content-Type": "application/json"}
        if api_key:
            header_name, header_prefix = self._auth_header()
            headers[header_name] = f"{header_prefix}{api_key}"
        return headers

    def _fetch_models(self, endpoint, api_key, is_openwebui, include_info=False):
        """
        Fetch models from the API endpoint.

        Returns a list of model IDs when include_info=False,
        or a (list, dict) tuple of (model_ids, descriptions) when include_info=True.
        """
        endpoint, api_path = self._split_endpoint_api_path(endpoint, is_openwebui)
        api_key = self._effective_api_token(api_key)
        url = endpoint + api_path + "/models" if api_path else endpoint + "/models"
        headers = self._build_auth_headers(api_key)

        try:
            log_to_file(f"Models fetch curl: curl -i {_curl_headers_for_log(headers)} '{url}'")
        except Exception:
            pass

        try:
            request = urllib.request.Request(url, headers=_with_user_agent(headers))
            with self._urlopen(request, context=self.get_ssl_context(), timeout=10) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
        except Exception as e:
            log_to_file(f"Failed to fetch models: {str(e)}")
            return ([], {}) if include_info else []

        if include_info:
            log_to_file(f"Models API raw response: {payload[:2000]}")

        models = []
        descriptions = {}

        def _add_model(item):
            if not isinstance(item, dict):
                return
            model_id = item.get("id") or item.get("model") or item.get("name")
            if not model_id:
                return
            model_id = str(model_id)
            models.append(model_id)
            if include_info:
                info = item.get("info") or {}
                meta = info.get("meta") or {}
                description = (
                    meta.get("description")
                    or info.get("description")
                    or item.get("description")
                    or item.get("summary")
                    or item.get("name")
                    or item.get("owned_by")
                )
                if description:
                    descriptions[model_id] = str(description)

        items = []
        if isinstance(data, dict):
            items = data.get("data") or data.get("models") or []
        elif isinstance(data, list):
            items = data

        for item in items:
            if isinstance(item, str) and not include_info:
                models.append(item)
            else:
                _add_model(item)

        return (models, descriptions) if include_info else models

    def _refresh_config_to_local(self, cancel_flag=None):
        """« Recharger la configuration » : force une récupération auprès du DM et
        rend une copie de ses réglages pour affichage.

        La récupération persiste elle-même ce qu'il faut (liste fermée) : recopier
        toutes les clés du DM écrirait aussi les jetons et `proxy_allow_insecure_ssl`,
        qui coupe la vérification TLS de tous les appels."""
        if cancel_flag and cancel_flag.get("cancel"):
            log_to_file("Reload config: canceled before fetch")
            return {}
        config_data = self._fetch_config(force=True)
        if not config_data:
            log_to_file("Reload config: failed to fetch config_data")
            return {}
        if cancel_flag and cancel_flag.get("cancel"):
            log_to_file("Reload config: canceled after fetch")
            return {}
        config_obj = config_data.get("config") if isinstance(config_data, dict) else None
        settings = config_obj if isinstance(config_obj, dict) else local_config.select_settings(config_data)
        if not isinstance(settings, dict):
            if not isinstance(config_data, dict):
                log_to_file(f"Reload config: no settings dict found (type={type(config_data).__name__})")
                return {}
            settings = config_data
        log_to_file(f"Reload config: keys={sorted(settings.keys())}")
        return dict(settings)

    def _keycloak_settings_from(self, config_data):
        settings = local_config.select_settings(config_data) or {}
        keycloak_src = None

        def _flat_keycloak(source):
            if not isinstance(source, dict):
                return None
            flat = {
                "issuerUrl": (
                    source.get("keycloakIssuerUrl")
                    or source.get("issuerUrl")
                    or source.get("issuerURL")
                    or source.get("issuer_url")
                    or source.get("keycloak_base_url")
                    or source.get("issuer")
                ),
                "realm": (
                    source.get("keycloakRealm")
                    or source.get("keycloak_realm")
                    or source.get("realm")
                ),
                "clientId": (
                    source.get("keycloakClientId")
                    or source.get("keycloak_client_id")
                    or source.get("clientId")
                    or source.get("client_id")
                ),
                "clientSecret": (
                    source.get("keycloak_client_secret")
                    or source.get("clientSecret")
                    or source.get("client_secret")
                ),
                "authorization_endpoint": (
                    source.get("keycloakAuthorizationEndpoint")
                    or source.get("keycloak_authorization_endpoint")
                    or source.get("authorization_endpoint")
                    or source.get("authorizationEndpoint")
                    or source.get("auth_endpoint")
                    or source.get("authEndpoint")
                    or source.get("auth_url")
                    or source.get("authUrl")
                    or source.get("auth")
                ),
                "token_endpoint": (
                    source.get("keycloakTokenEndpoint")
                    or source.get("keycloak_token_endpoint")
                    or source.get("token_endpoint")
                    or source.get("tokenEndpoint")
                    or source.get("token_url")
                    or source.get("tokenUrl")
                    or source.get("token")
                ),
                "userinfo_endpoint": (
                    source.get("keycloakUserinfoEndpoint")
                    or source.get("keycloak_userinfo_endpoint")
                    or source.get("userinfo_endpoint")
                    or source.get("userinfoEndpoint")
                    or source.get("user_info_endpoint")
                    or source.get("userInfoEndpoint")
                    or source.get("userinfo")
                ),
                "redirect_uri": (
                    source.get("keycloak_redirect_uri")
                    or source.get("redirect_uri")
                    or source.get("redirectUri")
                ),
                "allowed_redirect_uri": (
                    source.get("keycloak_allowed_redirect_uri")
                    or source.get("allowed_redirect_uri")
                    or source.get("allowedRedirectUri")
                ),
            }
            if any(v is not None and str(v).strip() for v in flat.values()):
                return flat
            return None

        if isinstance(settings, dict):
            if isinstance(settings.get("keycloak"), dict):
                keycloak_src = settings.get("keycloak")
            elif isinstance(settings.get("endpoints"), dict) and isinstance(settings.get("endpoints", {}).get("keycloak"), dict):
                keycloak_src = settings.get("endpoints", {}).get("keycloak")
            if keycloak_src is None:
                keycloak_src = _flat_keycloak(settings)
        if keycloak_src is None and isinstance(config_data, dict):
            candidate = config_data.get("keycloak")
            if not isinstance(candidate, dict):
                endpoints = config_data.get("endpoints", {}) if isinstance(config_data.get("endpoints", {}), dict) else {}
                candidate = endpoints.get("keycloak")
            if isinstance(candidate, dict):
                keycloak_src = candidate
            if keycloak_src is None:
                keycloak_src = _flat_keycloak(config_data)
        if not isinstance(keycloak_src, dict):
            return {}
        keycloak_map = {
            "keycloakIssuerUrl": (
                keycloak_src.get("issuerUrl")
                or keycloak_src.get("issuerURL")
                or keycloak_src.get("keycloakIssuerUrl")
                or keycloak_src.get("issuer_url")
                or keycloak_src.get("issuerUri")
                or keycloak_src.get("issuerURI")
                or keycloak_src.get("issuer")
                or keycloak_src.get("baseUrl")
                or keycloak_src.get("base_url")
                or keycloak_src.get("url")
            ),
            "keycloakRealm": (
                keycloak_src.get("realm")
                or keycloak_src.get("keycloakRealm")
                or keycloak_src.get("keycloak_realm")
            ),
            "keycloakClientId": (
                keycloak_src.get("client_id")
                or keycloak_src.get("clientId")
                or keycloak_src.get("clientID")
                or keycloak_src.get("keycloakClientId")
            ),
            "keycloak_client_secret": (
                keycloak_src.get("client_secret")
                or keycloak_src.get("clientSecret")
                or keycloak_src.get("keycloakClientSecret")
            ),
            "keycloakAuthorizationEndpoint": (
                keycloak_src.get("authorization_endpoint")
                or keycloak_src.get("authorizationEndpoint")
                or keycloak_src.get("keycloakAuthorizationEndpoint")
                or keycloak_src.get("auth_endpoint")
                or keycloak_src.get("authEndpoint")
                or keycloak_src.get("auth_url")
                or keycloak_src.get("authUrl")
                or keycloak_src.get("auth")
            ),
            "keycloakTokenEndpoint": (
                keycloak_src.get("token_endpoint")
                or keycloak_src.get("tokenEndpoint")
                or keycloak_src.get("keycloakTokenEndpoint")
                or keycloak_src.get("token_url")
                or keycloak_src.get("tokenUrl")
                or keycloak_src.get("token")
            ),
            "keycloakUserinfoEndpoint": (
                keycloak_src.get("userinfo_endpoint")
                or keycloak_src.get("userinfoEndpoint")
                or keycloak_src.get("keycloakUserinfoEndpoint")
                or keycloak_src.get("user_info_endpoint")
                or keycloak_src.get("userInfoEndpoint")
                or keycloak_src.get("userinfo")
            ),
            "keycloak_redirect_uri": (
                keycloak_src.get("redirect_uri")
                or keycloak_src.get("redirectUri")
                or keycloak_src.get("keycloak_redirect_uri")
            ),
            "keycloak_allowed_redirect_uri": (
                keycloak_src.get("allowed_redirect_uri")
                or keycloak_src.get("allowedRedirectUri")
                or keycloak_src.get("keycloak_allowed_redirect_uri")
            ),
        }
        return {key: str(value).strip() for key, value in keycloak_map.items()
                if value is not None and str(value).strip()}

    def _get_cached_models(self, endpoint, api_key, is_openwebui):
        key = (endpoint, api_key, bool(is_openwebui))
        now = time.time()
        if self._models_cache and self._models_cache_key == key:
            if (now - self._models_cache_loaded_at) < self._models_cache_ttl:
                return self._models_cache
        models = self._fetch_models(endpoint, api_key, is_openwebui)
        self._models_cache = models
        self._models_cache_key = key
        self._models_cache_loaded_at = now
        return models


    def _api_probe(self, endpoint, headers, path):
        endpoint = (endpoint or "").rstrip("/")
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            if path.startswith("/"):
                url = endpoint + path
            else:
                url = endpoint + "/" + path
        try:
            request = urllib.request.Request(url, headers=_with_user_agent(headers))
            with self._urlopen(request, context=self.get_ssl_context(), timeout=5) as response:
                response.read()
                status = getattr(response, "status", None)
            return True, {"url": url, "status": status, "error": ""}
        except urllib.error.HTTPError as e:
            return True, {"url": url, "status": e.code, "error": f"http_{e.code}"}
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            return False, {"url": url, "status": None, "error": str(reason)}
        except socket.timeout:
            return False, {"url": url, "status": None, "error": "timeout"}
        except Exception as e:
            return False, {"url": url, "status": None, "error": str(e)}

    def _endpoint_connectivity_status(self, endpoint, is_openwebui):
        headers = {"Content-Type": "application/json"}
        endpoint_base, api_path = self._split_endpoint_api_path(endpoint, is_openwebui)
        checks = []
        if is_openwebui:
            checks.append("/health")
        models_path = (api_path + "/models") if api_path else "/models"
        checks.append(models_path)
        checks.append("/")
        last_detail = {"url": endpoint_base, "status": None, "error": "unknown"}
        for path in checks:
            ok, detail = self._api_probe(endpoint_base, headers, path)
            if ok:
                return True, detail
            last_detail = detail
        return False, last_detail

    def _api_status(self, endpoint, api_key, is_openwebui):
        anon_ok, _ = self._endpoint_connectivity_status(endpoint, is_openwebui)

        api_key = self._effective_api_token(api_key)
        auth_headers = self._build_auth_headers(api_key)

        auth_ok = False
        if api_key:
            endpoint_base, api_path = self._split_endpoint_api_path(endpoint, is_openwebui)
            models_path = (api_path + "/models") if api_path else "/models"
            _, detail = self._api_probe(endpoint_base, auth_headers, models_path)
            status = detail.get("status")
            if status in (401, 403):
                auth_ok = False
                log_to_file(f"API auth probe rejected: status={status} url={detail.get('url')}")
            elif status is not None:
                # Accept non-auth errors (e.g. 404) as "auth reachable":
                # some providers do not expose /models despite valid credentials.
                auth_ok = True
            else:
                auth_ok = False

        return anon_ok, auth_ok

    def make_chat_request(self, messages, max_tokens=2000):
        """Build a streaming chat request from a full messages[] array.

        The messages list is forwarded as-is: the caller manages the system
        prompt and the history.
        """
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 2000

        endpoint = str(self.get_config("llm_base_urls", "http://127.0.0.1:5000")).rstrip("/")
        api_key = self._effective_api_token(self.get_config("llm_api_tokens", ""))
        model = str(self.get_config("llm_default_models", ""))

        headers = self._build_auth_headers(api_key)
        endpoint, api_path = self._split_endpoint_api_path(endpoint, True)
        log_to_file(f"[llm-auth] {self._llm_auth_debug()}")

        url = endpoint + api_path + "/chat/completions"
        data = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": True,
        }
        if model:
            data["model"] = model

        json_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=json_data, headers=_with_user_agent(headers))
        return request

    def get_ssl_context(self, target_url=None):
        """
        Create an SSL context for HTTP calls.
        If available, load the bundled CA chain used by bootstrap endpoints.

        Cert verification is skipped when the global `proxy_allow_insecure_ssl`
        flag is set, OR when the target URL's host is in `bootstrap_insecure_urls`
        (per-URL `-k`). `target_url` defaults to the active bootstrap URL, so
        enroll/telemetry/update inherit the per-URL decision from whichever DM
        served the config.
        """
        allow_insecure = self._as_bool(self._get_config_from_file("proxy_allow_insecure_ssl", False))
        if not allow_insecure:
            check_url = str(target_url or self._active_bootstrap_url() or "").strip()
            if check_url and self._is_insecure_bootstrap_url(check_url):
                allow_insecure = True
        ssl_context = ssl.create_default_context()
        if allow_insecure:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context

        loaded_bundle = None
        configured_bundle = str(self._get_config_from_file("ca_bundle_path", "") or "").strip()
        candidate_paths = []
        if configured_bundle:
            if configured_bundle.startswith("file://"):
                try:
                    configured_bundle = str(uno.fileUrlToSystemPath(configured_bundle))
                except Exception:
                    pass
            if os.path.isabs(configured_bundle):
                candidate_paths.append(configured_bundle)
            else:
                candidate_paths.append(os.path.join(self._get_user_config_dir(), configured_bundle))
                candidate_paths.append(os.path.join(os.path.dirname(__file__), configured_bundle))

        candidate_paths.append(
            os.path.join(
                os.path.dirname(__file__),
                "CAbundle",
                "scaleway-bootstrap-ca-chain.pem",
            )
        )

        seen = set()
        for path in candidate_paths:
            candidate = str(path or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if not os.path.isfile(candidate):
                continue
            try:
                ssl_context.load_verify_locations(cafile=candidate)
                loaded_bundle = candidate
                break
            except Exception as exc:
                self._last_ca_bundle_error = str(exc)

        if loaded_bundle and loaded_bundle != self._last_loaded_ca_bundle:
            self._last_loaded_ca_bundle = loaded_bundle
            self._last_ca_bundle_error = None
            self._last_logged_ca_bundle_error = None
            log_to_file(f"SSL CA bundle loaded: {loaded_bundle}")
        elif not loaded_bundle and self._last_ca_bundle_error:
            if self._last_ca_bundle_error != self._last_logged_ca_bundle_error:
                log_to_file(f"SSL CA bundle load failed: {self._last_ca_bundle_error}")
                self._last_logged_ca_bundle_error = self._last_ca_bundle_error

        return ssl_context

    #retrieved from https://wiki.documentfoundation.org/Macros/General/IO_to_Screen
    #License: Creative Commons Attribution-ShareAlike 3.0 Unported License,
    #License: The Document Foundation  https://creativecommons.org/licenses/by-sa/3.0/
    #begin sharealike section
    def _show_about_dialog(self):
        """Show the About dialog with version, icon, description and update check."""
        from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE

        WIDTH = 420
        BTN_HEIGHT = 26
        HORI_MARGIN = 20
        VERT_MARGIN = 16
        CHANGELOG_HEIGHT = 110
        HEIGHT = 430

        ctx = uno.getComponentContext()
        def create(name):
            return ctx.getServiceManager().createInstanceWithContext(name, ctx)

        dialog = create("com.sun.star.awt.UnoControlDialog")
        dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
        dialog.setModel(dialog_model)
        dialog.setVisible(False)
        dialog.setTitle(_t("about.title"))
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
        try:
            dialog_model.BackgroundColor = _UI["bg"]
        except Exception:
            pass

        def add(name, type_, x_, y_, width_, height_, props):
            try:
                m = dialog_model.createInstance("com.sun.star.awt.UnoControl" + type_ + "Model")
                dialog_model.insertByName(name, m)
                control = dialog.getControl(name)
                control.setPosSize(x_, y_, width_, height_, POSSIZE)
                for key, value in props.items():
                    try:
                        setattr(m, key, value)
                    except Exception:
                        pass
                return control
            except Exception:
                return None

        y = VERT_MARGIN

        # Dans l'OXT installé, entrypoint.py est sous <oxt>/src/mirai/ et le
        # logo sous <oxt>/assets/.
        logo_url = ""
        logo = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png"))
        if os.path.exists(logo):
            logo_url = uno.systemPathToFileUrl(logo)

        LOGO_SIZE = 64
        if logo_url:
            add("about_logo", "ImageControl",
                WIDTH // 2 - LOGO_SIZE // 2, y, LOGO_SIZE, LOGO_SIZE,
                {"ImageURL": logo_url, "Border": 0, "ScaleImage": True})
            y += LOGO_SIZE + 10
        else:
            y += 10  # small spacing if no logo

        # Title
        add("about_title", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 22,
            {"Label": _t("about.window_title"),
             "FontHeight": 16, "FontWeight": 200,
             "TextColor": _UI["primary"], "Align": 1})
        y += 26

        # Version
        version = self._get_extension_version() or "0.1.0"
        add("about_version", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 16,
            {"Label": _t("about.version", version=version),
             "FontHeight": _UI["font_label"],
             "TextColor": _UI["text_secondary"], "Align": 1})
        y += 22

        # Separator
        add("about_sep1", "FixedLine",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 6, {})
        y += 12

        # Description (non-editable label, smaller text, white bg)
        desc_line1 = _t("about.desc")
        add("about_desc1", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 30,
            {"Label": desc_line1, "NoLabel": True, "MultiLine": True,
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"]})
        y += 32
        add("about_desc2", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 12,
            {"Label": _t("about.program"), "NoLabel": True,
             "FontHeight": 7, "FontSlant": 2,
             "TextColor": _UI["text_light"]})
        y += 18

        # Separator
        add("about_sep2", "FixedLine",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 6, {})
        y += 12

        # Changelog title
        add("about_changelog_title", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 16,
            {"Label": _t("about.changelog_title"),
             "FontHeight": _UI["font_section"], "FontWeight": 150,
             "TextColor": _UI["primary"]})
        y += 20

        changelog = _t("about.changelog")
        add("about_changelog", "Edit",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, CHANGELOG_HEIGHT,
            {"Text": changelog, "MultiLine": True, "ReadOnly": True,
             "BackgroundColor": _UI["bg_section"],
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_light"],
             "Border": 1, "BorderColor": _UI["border"],
             "VScroll": True})
        y += CHANGELOG_HEIGHT + 10

        # Buttons pinned at bottom
        BTN_WIDTH = 140
        btn_y = HEIGHT - VERT_MARGIN - BTN_HEIGHT - 18

        # Check updates button
        _mascot_path = os.path.join(os.path.dirname(__file__), "icons", "mascot16.png")
        btn_update_props = {
            "Label": _t("about.updates_button"),
            "FontHeight": _UI["font_small"],
            "FontWeight": 150,
            "TextColor": _UI["btn_primary_fg"],
            "BackgroundColor": _UI["btn_primary_bg"],
        }
        if os.path.exists(_mascot_path):
            btn_update_props["ImageURL"] = uno.systemPathToFileUrl(_mascot_path)
            btn_update_props["ImagePosition"] = 0
            btn_update_props["ImageAlign"] = 0

        btn_update = add("about_btn_update", "Button",
            HORI_MARGIN, btn_y, BTN_WIDTH, BTN_HEIGHT, btn_update_props)

        # Close button
        btn_close = add("about_btn_close", "Button",
            WIDTH - HORI_MARGIN - BTN_WIDTH, btn_y, BTN_WIDTH, BTN_HEIGHT,
            {"Label": _t("common.close"),
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"],
             "BackgroundColor": _UI["bg_section"]})

        # Diagnostic : ouvrir le dossier de mise à jour dans l'explorateur/Finder,
        # en natif (SystemShellExecute, sans cmd.exe) — même mécanisme que le bouton
        # de la boîte « mise à jour bloquée ». Utile pour tester localement (Mac inclus).
        btn_open_folder = add("about_btn_open_folder", "Button",
            (HORI_MARGIN + BTN_WIDTH + WIDTH - HORI_MARGIN - BTN_WIDTH) // 2 - 48,
            btn_y, 96, BTN_HEIGHT,
            {"Label": _t("about.open_folder"),
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"],
             "BackgroundColor": _UI["bg_section"]})

        # Status label for update check
        update_status = add("about_update_status", "FixedText",
            HORI_MARGIN, btn_y + BTN_HEIGHT + 4, WIDTH - HORI_MARGIN * 2, 14,
            {"Label": "", "NoLabel": True,
             "FontHeight": 7,
             "TextColor": _UI["text_secondary"], "Align": 1})

        about_self = self

        class AboutActionListener(unohelper.Base, XActionListener):
            def actionPerformed(self, event):
                source = getattr(event, "Source", None)
                if source == btn_close:
                    try:
                        dialog.setVisible(False)
                        dialog.dispose()
                    except Exception:
                        pass
                elif source == btn_update:
                    if update_status:
                        try:
                            update_status.getModel().Label = _t("about.checking")
                            update_status.getModel().TextColor = _UI["primary"]
                        except Exception:
                            pass
                    def _check_update_bg():
                        try:
                            # Self-test (dev) : forcer la boîte « mise à jour bloquée »
                            # pour valider le bouton d'ouverture du dossier — sans
                            # déployer de vraie MAJ. Activer via l'env
                            # MIRAI_SELFTEST_UPDATE_BLOCKED=1 (inerte en production).
                            if os.environ.get("MIRAI_SELFTEST_UPDATE_BLOCKED"):
                                try:
                                    pend = about_self._pending_update_dir()
                                    os.makedirs(pend, exist_ok=True)
                                    oxt = os.path.join(pend, "mirai_update.oxt")
                                    open(oxt, "a").close()
                                    if update_status:
                                        update_status.getModel().Label = (
                                            _t("about.selftest_blocked")
                                        )
                                        update_status.getModel().TextColor = _UI["info"]
                                    about_self._notify_update_blocked("SELFTEST", oxt)
                                except Exception as _e:
                                    log_to_file(f"selftest update-blocked: {str(_e)}")
                                return
                            config_data = about_self._fetch_config(force=True)
                            update_dir = None
                            if isinstance(config_data, dict):
                                update_dir = config_data.get("update")
                            if not update_status:
                                return
                            if isinstance(update_dir, dict) and update_dir.get("action") in ("update", "rollback"):
                                target = update_dir.get("target_version", "?")
                                update_status.getModel().Label = _t("about.update_available", target=target)
                                update_status.getModel().TextColor = _UI["info"]
                                # Wait for update to finish (max 60s)
                                for _ in range(120):
                                    time.sleep(0.5)
                                    if not about_self.runtime.update_in_progress:
                                        break
                                if about_self.runtime.update_in_progress:
                                    update_status.getModel().Label = _t("about.downloading", target=target)
                                    update_status.getModel().TextColor = _UI["info"]
                                else:
                                    new_ver = about_self._get_extension_version() or "?"
                                    if new_ver == target:
                                        update_status.getModel().Label = _t("about.installed_restart", target=target)
                                        update_status.getModel().TextColor = _UI["success"]
                                    else:
                                        update_status.getModel().Label = _t("about.download_failed", target=target)
                                        update_status.getModel().TextColor = _UI["error"]
                            else:
                                current = about_self._get_extension_version() or "?"
                                update_status.getModel().Label = _t("about.uptodate", current=current)
                                update_status.getModel().TextColor = _UI["success"]
                        except Exception as e:
                            if update_status:
                                try:
                                    update_status.getModel().Label = _t("common.error", detail=str(e)[:50])
                                    update_status.getModel().TextColor = _UI["error"]
                                except Exception:
                                    pass
                    threading.Thread(target=_check_update_bg, daemon=True).start()
                elif source == btn_open_folder:
                    # Ouvre le dossier de MAJ en natif (Finder/Explorer, sans cmd.exe).
                    try:
                        folder = about_self._pending_update_dir()
                        if not os.path.isdir(folder):
                            folder = about_self._data_dir()
                        ok = about_self._open_folder_native(folder)
                        if update_status:
                            update_status.getModel().Label = (
                                _t("about.folder_opened") if ok
                                else _t("about.folder_failed")
                            )
                            update_status.getModel().TextColor = (
                                _UI["success"] if ok else _UI["error"]
                            )
                    except Exception as _e:
                        log_to_file(f"about open-folder: {str(_e)}")
            def disposing(self, event):
                return

        listener = AboutActionListener()
        if btn_update:
            try:
                btn_update.addActionListener(listener)
            except Exception:
                pass
        if btn_close:
            try:
                btn_close.addActionListener(listener)
            except Exception:
                pass
        if btn_open_folder:
            try:
                btn_open_folder.addActionListener(listener)
            except Exception:
                pass

        # Rollover effects
        if btn_update:
            class _UpdateRollover(unohelper.Base, XMouseListener):
                def mousePressed(self, e):
                    return
                def mouseReleased(self, e):
                    return
                def mouseEntered(self, e):
                    try:
                        btn_update.getModel().BackgroundColor = _UI["primary_hover"]
                    except BaseException:
                        pass
                def mouseExited(self, e):
                    try:
                        btn_update.getModel().BackgroundColor = _UI["btn_primary_bg"]
                    except BaseException:
                        pass
                def disposing(self, e):
                    return
            try:
                btn_update.addMouseListener(_UpdateRollover())
            except Exception:
                pass

        # Window close
        class AboutTopWindowListener(unohelper.Base, XTopWindowListener):
            def windowClosing(self, e):
                try:
                    dialog.setVisible(False)
                    dialog.dispose()
                except BaseException:
                    pass
            def windowOpened(self, e):
                return
            def windowClosed(self, e):
                return
            def windowMinimized(self, e):
                return
            def windowNormalized(self, e):
                return
            def windowActivated(self, e):
                return
            def windowDeactivated(self, e):
                return
            def disposing(self, e):
                return

        # Position and show
        frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
        window = frame.getContainerWindow() if frame else None
        dialog.createPeer(create("com.sun.star.awt.Toolkit"), window)
        if window:
            ps = window.getPosSize()
            dialog.setPosSize(ps.Width // 2 - WIDTH // 2, ps.Height // 2 - HEIGHT // 2, 0, 0, POS)

        try:
            peer = dialog.getPeer()
            if peer:
                peer.addTopWindowListener(AboutTopWindowListener())
        except Exception:
            pass

        dialog.setVisible(True)

    def _settings_token_values(self):
        """(valeur affichée, jeton des requêtes du dialogue) du champ « Token OWUI ».

        En mode DM le jeton est le llmToken minté par le DM : le champ reste vide,
        sinon l'enregistrer comme clé utilisateur le rendrait persistant. Les
        requêtes du dialogue utilisent le jeton courant."""
        token = str(self.get_config("llm_api_tokens", "") or "")
        if self._device_management_enabled():
            return "", token
        return token, token

    def settings_box(self,title="", x=None, y=None):
        """ Settings dialog with configurable backend options """
        WIDTH = 740
        HORI_MARGIN = 16
        VERT_MARGIN = 12
        BUTTON_WIDTH = 150
        BUTTON_HEIGHT = 34
        HORI_SEP = 10
        VERT_SEP = 8
        LABEL_HEIGHT = 22
        EDIT_HEIGHT = 28
        IMAGE_HEIGHT = 132
        EXTRA_BOTTOM = 60
        DESC_HEIGHT = EDIT_HEIGHT * 2
        TEST_ROW_HEIGHT = BUTTON_HEIGHT + VERT_SEP
        from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
        from com.sun.star.awt.PushButtonType import OK, CANCEL
        from com.sun.star.util.MeasureUnit import TWIP
        ctx = uno.getComponentContext()
        def create(name):
            return ctx.getServiceManager().createInstanceWithContext(name, ctx)
        dialog = create("com.sun.star.awt.UnoControlDialog")
        dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
        dialog.setModel(dialog_model)
        try:
            dialog_model.BackgroundColor = _UI["bg"]
        except Exception:
            pass
        dialog.setVisible(False)
        dialog.setTitle(title or _t("app.title"))

        def _mask_value(value):
            try:
                text = str(value or "")
            except Exception:
                return ""
            if not text:
                return ""
            if len(text) <= 4:
                return "*" * len(text)
            return f"{text[:2]}***{text[-2:]}"

        def _log_launch_config():
            config_file_path = "unknown"
            package_config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config.default.json'))
            user_exists = False
            user_size = -1
            package_exists = os.path.exists(package_config_path)
            package_size = os.path.getsize(package_config_path) if package_exists else -1
            try:
                path_settings = self.sm.createInstanceWithContext('com.sun.star.util.PathSettings', self.ctx)
                user_config_path = getattr(path_settings, "UserConfig")
                if user_config_path.startswith('file://'):
                    user_config_path = str(uno.fileUrlToSystemPath(user_config_path))
                config_file_path = os.path.join(user_config_path, "config.json")
                user_exists = os.path.exists(config_file_path)
                user_size = os.path.getsize(config_file_path) if user_exists else -1
            except Exception:
                pass

            system_prompt = self._get_config_from_file("systemPrompt", "")
            log_to_file(
                "Config loaded "
                f"path={config_file_path} user_exists={user_exists} user_size={user_size} "
                f"package_path={package_config_path} package_exists={package_exists} package_size={package_size} "
                f"llm_base_urls={self._get_config_from_file('llm_base_urls','')} "
                f"llm_api_tokens={_mask_value(self._get_config_from_file('llm_api_tokens',''))} "
                f"authHeaderName={self._get_config_from_file('authHeaderName','')} "
                f"authHeaderPrefix={self._get_config_from_file('authHeaderPrefix','')} "
                f"keycloakIssuerUrl={self._get_config_from_file('keycloakIssuerUrl','')} "
                f"keycloakRealm={self._get_config_from_file('keycloakRealm','')} "
                f"keycloakClientId={self._get_config_from_file('keycloakClientId','')} "
                f"systemPromptLen={len(str(system_prompt))} "
                f"telemetryEndpoint={self._get_config_from_file('telemetryEndpoint','')} "
                f"telemetryAuthorizationType={self._get_config_from_file('telemetryAuthorizationType','')} "
                f"telemetryKey={_mask_value(self._get_config_from_file('telemetryKey',''))} "
                f"bootstrap_url={self._active_bootstrap_url()} "
                f"config_path={self._get_config_from_file('config_path','')} "
                f"enabled={self._get_config_from_file('enabled', False)} "
                f"llm_default_models={self._get_config_from_file('llm_default_models','')}"
            )

        _log_launch_config()

        def show_wait_dialog():
            wait_width = 300
            wait_height = 90
            wait_dialog = create("com.sun.star.awt.UnoControlDialog")
            wait_model = create("com.sun.star.awt.UnoControlDialogModel")
            wait_dialog.setModel(wait_model)
            try:
                wait_model.BackgroundColor = _UI["bg"]
            except Exception:
                pass
            wait_dialog.setVisible(False)
            wait_dialog.setTitle("MIrAI")
            wait_dialog.setPosSize(0, 0, wait_width, wait_height, SIZE)

            try:
                label_model = wait_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
                wait_model.insertByName("wait_label", label_model)
                label_model.Label = _t("settings.connecting")
                label_model.NoLabel = True
                try:
                    label_model.FontHeight = _UI["font_label"]
                    label_model.TextColor = _UI["text"]
                except Exception:
                    pass
                wait_label = wait_dialog.getControl("wait_label")
                wait_label.setPosSize(20, 28, wait_width - 40, 24, POSSIZE)
            except Exception:
                wait_label = None

            frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
            window = frame.getContainerWindow() if frame else None
            toolkit = create("com.sun.star.awt.Toolkit")
            wait_dialog.createPeer(toolkit, window)
            if window:
                ps = window.getPosSize()
                _x = ps.Width / 2 - wait_width / 2
                _y = ps.Height / 2 - wait_height / 2
                wait_dialog.setPosSize(_x, _y, 0, 0, POS)
            wait_dialog.setVisible(True)
            return wait_dialog, wait_label, toolkit

        def animate_wait(label, toolkit, steps=3, delay=0.2):
            if not label or not toolkit:
                return
            for i in range(steps):
                try:
                    dots = "." * ((i % 3) + 1)
                    label.getModel().Label = f'{_t("settings.connecting_base")}{dots}'
                    pump_events(toolkit)
                except Exception:
                    pass
                time.sleep(delay)

        wait_dialog, wait_label, wait_toolkit = show_wait_dialog()
        try:
            animate_wait(wait_label, wait_toolkit, steps=4, delay=0.15)
            endpoint_value = str(self.get_config("llm_base_urls","http://127.0.0.1:5000/api"))
            api_key_value, request_token = self._settings_token_values()
            log_to_file(f"Settings open: llm_api_tokens length={len(request_token)}")
            current_model = str(self._get_config_from_file("llm_default_models","")).strip()
            models, model_descriptions = self._fetch_models(endpoint_value, request_token, True, include_info=True)
            if current_model and current_model not in models:
                models = [current_model] + models
            if not models and current_model:
                models = [current_model]
            log_to_file(f"Models loaded: {len(models)} -> {models}")
        finally:
            try:
                wait_dialog.setVisible(False)
                wait_dialog.dispose()
            except Exception:
                pass

        field_specs = [
            {"name": "endpoint", "label": _t("settings.endpoint_label"), "value": endpoint_value, "type": "text"},
            {"name": "api_key", "label": _t("settings.api_key_label"), "value": api_key_value, "type": "password"},
            {"name": "model", "label": _t("settings.model_label"), "value": current_model, "type": "list", "items": models},
        ]

        num_fields = len(field_specs)
        total_field_height = num_fields * (LABEL_HEIGHT + EDIT_HEIGHT + VERT_SEP * 2) + TEST_ROW_HEIGHT + (BUTTON_HEIGHT - LABEL_HEIGHT)
        desc_block_height = LABEL_HEIGHT + VERT_SEP + DESC_HEIGHT + VERT_SEP * 2
        HEIGHT = VERT_MARGIN * 2 + IMAGE_HEIGHT + VERT_SEP + total_field_height + desc_block_height + LABEL_HEIGHT + BUTTON_HEIGHT * 2 + VERT_SEP * 6 + EXTRA_BOTTOM
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)

        def add(name, type, x_, y_, width_, height_, props):
            try:
                model = dialog_model.createInstance("com.sun.star.awt.UnoControl" + type + "Model")
            except Exception as e:
                log_to_file(f"Dialog control type unsupported: name={name} type={type} error={str(e)}")
                return None
            try:
                dialog_model.insertByName(name, model)
            except Exception as e:
                log_to_file(f"Dialog insert failed: name={name} type={type} error={str(e)}")
                return None
            control = dialog.getControl(name)
            try:
                control.setPosSize(x_, y_, width_, height_, POSSIZE)
            except Exception as e:
                log_to_file(f"Dialog size failed: name={name} type={type} error={str(e)}")
            for key, value in props.items():
                try:
                    setattr(model, key, value)
                except Exception as e:
                    log_to_file(f"Dialog prop unsupported: control={name} type={type} prop={key} error={str(e)}")
            return control

        field_controls = {}
        current_y = VERT_MARGIN

        image_path = os.path.join(os.path.dirname(__file__), "icons", "iassistant.png")
        if os.path.exists(image_path):
            try:
                image_url = uno.systemPathToFileUrl(image_path)
                available_width = WIDTH - HORI_MARGIN * 2
                image_width = int(min(available_width, IMAGE_HEIGHT * (1505.0 / 400.0)))
                image_x = HORI_MARGIN + int((available_width - image_width) / 2)
                img_splash = add("img_splash", "ImageControl", image_x, current_y,
                    image_width, IMAGE_HEIGHT, {
                        "ImageURL": image_url,
                        "Border": 0,
                        "ScaleImage": True
                    })

                # Make image clickable → opens mirai website
                if img_splash:
                    class SplashClickListener(unohelper.Base, XMouseListener):
                        def __init__(self, outer):
                            self.outer = outer
                        def mousePressed(self, event):
                            try:
                                import webbrowser
                                webbrowser.open("https://mirai.interieur.gouv.fr")
                            except Exception as e:
                                log_to_file(f"Splash click open URL failed: {str(e)}")
                        def mouseReleased(self, event):
                            return
                        def mouseEntered(self, event):
                            return
                        def mouseExited(self, event):
                            return
                        def disposing(self, event):
                            return
                    try:
                        img_splash.addMouseListener(SplashClickListener(self))
                    except Exception:
                        pass

                current_y += IMAGE_HEIGHT + VERT_SEP
                # Separator after image
                add("line_after_image", "FixedLine", HORI_MARGIN, current_y,
                    WIDTH - HORI_MARGIN * 2, 2, {})
                current_y += VERT_SEP
                # Section header: Connexion
                add("section_connexion", "FixedText", HORI_MARGIN, current_y,
                    WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
                        "Label": _t("settings.section_connection"), "NoLabel": True,
                        "FontHeight": _UI["font_section"],
                        "TextColor": _UI["primary"],
                        "FontWeight": 150,
                    })
                current_y += LABEL_HEIGHT + VERT_SEP
            except Exception:
                pass
        api_key_plain_control = None
        for field in field_specs:
            label_name = f"label_{field['name']}"
            edit_name = f"edit_{field['name']}"
            label_width = WIDTH - HORI_MARGIN * 2
            if field.get("name") == "api_key":
                label_width -= (90 + HORI_SEP)
            add(label_name, "FixedText", HORI_MARGIN, current_y, label_width, LABEL_HEIGHT, {
                "Label": field["label"], "NoLabel": True,
                "FontHeight": _UI["font_label"],
                "TextColor": _UI["text"],
            })
            if field.get("name") == "api_key":
                add("toggle_api_key", "Button", HORI_MARGIN + label_width + HORI_SEP, current_y, 90, BUTTON_HEIGHT, {
                    "Label": _t("settings.show"), "NoLabel": True,
                    "FontHeight": _UI["font_small"],
                })
            current_y += (BUTTON_HEIGHT if field.get("name") == "api_key" else LABEL_HEIGHT) + VERT_SEP
            if field.get("type") == "list":
                items = field.get("items") or []
                control = add(edit_name, "ListBox", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
                    "StringItemList": tuple(items), "Dropdown": True,
                    "BackgroundColor": _UI["bg_input"],
                })
                if control:
                    try:
                        if field["value"]:
                            control.selectItem(field["value"], True)
                    except Exception:
                        pass
                    field_controls[field["name"]] = control
            else:
                props = {"Text": field["value"], "BackgroundColor": _UI["bg_input"]}
                if field.get("type") == "password":
                    props["EchoChar"] = ord("*")
                control = add(edit_name, "Edit", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT,
                    props)
                if control:
                    field_controls[field["name"]] = control
                if field.get("name") == "api_key":
                    api_key_plain_control = add("edit_api_key_plain", "Edit", HORI_MARGIN, current_y,
                        WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {"Text": field["value"], "BackgroundColor": _UI["bg_input"]})
                    if api_key_plain_control:
                        try:
                            api_key_plain_control.setVisible(False)
                        except Exception:
                            pass
            current_y += EDIT_HEIGHT + VERT_SEP * 2
            if field.get("name") == "api_key":
                add("btn_test_token", "Button", HORI_MARGIN, current_y - VERT_SEP, 150, BUTTON_HEIGHT, {
                    "Label": _t("settings.refresh_token"), "Name": "test_token", "NoLabel": True,
                    "FontHeight": _UI["font_small"],
                })
                current_y += TEST_ROW_HEIGHT

        description_label = _t("settings.model_desc_label")
        add("label_model_desc", "FixedText", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": description_label, "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text_secondary"],
        })
        current_y += LABEL_HEIGHT + VERT_SEP

        add("edit_model_desc", "Edit", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, DESC_HEIGHT, {
            "Text": "", "ReadOnly": True, "MultiLine": True,
            "BackgroundColor": _UI["bg_section"],
            "TextColor": _UI["text_secondary"],
            "FontHeight": _UI["font_body"],
            "Border": 0,
        })
        current_y += DESC_HEIGHT + VERT_SEP

        # Separator before status
        add("line_before_status", "FixedLine", HORI_MARGIN, current_y,
            WIDTH - HORI_MARGIN * 2, 2, {})
        current_y += VERT_SEP

        access_token = str(self._get_config_from_file("access_token", "")).strip()
        email = self._token_email(access_token, allow_network=False) if access_token else None
        anon_ok, auth_ok = self._api_status(endpoint_value, request_token, True)

        def _status_style(anon_ok, auth_ok, email_value):
            if auth_ok:
                return (_t("settings.status_connected"), _UI["status_ok"])
            if anon_ok and not auth_ok:
                return (_t("settings.status_anonymous"), _UI["status_warn"])
            if not anon_ok and not auth_ok and email_value is None:
                return (_t("settings.status_untested"), _UI["status_neutral"])
            return (_t("settings.status_unreachable"), _UI["status_fail"])

        status_label, status_color = _status_style(anon_ok, auth_ok, email)
        status_text = f"{status_label}" + (f" ({email})" if email else "")

        # Status section header
        add("section_status", "FixedText", HORI_MARGIN, current_y,
            WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
                "Label": _t("settings.section_status"), "NoLabel": True,
                "FontHeight": _UI["font_section"],
                "TextColor": _UI["primary"],
                "FontWeight": 150,
            })
        current_y += LABEL_HEIGHT + VERT_SEP

        add("label_status_dot", "FixedText", HORI_MARGIN + 4, current_y,
            16, LABEL_HEIGHT, {"Label": "●", "NoLabel": True, "TextColor": status_color,
                               "FontHeight": 12})
        add("label_status_text", "FixedText", HORI_MARGIN + 24, current_y,
            WIDTH - HORI_MARGIN * 2 - 24, LABEL_HEIGHT, {
                "Label": status_text, "NoLabel": True,
                "FontHeight": _UI["font_label"],
                "TextColor": _UI["text"],
            })
        current_y += LABEL_HEIGHT + VERT_SEP * 2

        # Separator before action buttons
        add("line_before_actions", "FixedLine", HORI_MARGIN, current_y,
            WIDTH - HORI_MARGIN * 2, 2, {})
        current_y += VERT_SEP + 4

        # Action buttons row
        keycloak_width = 120
        reload_width = 210
        add("btn_keycloak", "Button", HORI_MARGIN, current_y,
            keycloak_width, BUTTON_HEIGHT, {
                "Label": _t("settings.sso_login"), "Name": "keycloak_login",
                "Tabstop": True, "Enabled": True, "NoLabel": True,
                "FontHeight": _UI["font_small"],
            })
        add("btn_reload_config", "Button", HORI_MARGIN + keycloak_width + HORI_SEP,
            current_y, reload_width, BUTTON_HEIGHT, {
                "Label": _t("settings.reload_config"), "Name": "reload_config",
                "Tabstop": True, "Enabled": True, "NoLabel": True,
                "FontHeight": _UI["font_small"],
            })
        current_y += BUTTON_HEIGHT + VERT_SEP * 2

        # OK / Cancel row - right-aligned
        ok_cancel_width = BUTTON_WIDTH
        add("btn_ok", "Button", WIDTH - HORI_MARGIN - ok_cancel_width * 2 - HORI_SEP, current_y,
            ok_cancel_width, BUTTON_HEIGHT, {
                "PushButtonType": OK, "DefaultButton": True, "Label": _t("common.save"),
                "FontHeight": _UI["font_label"],
            })
        add("btn_cancel", "Button", WIDTH - HORI_MARGIN - ok_cancel_width, current_y,
            ok_cancel_width, BUTTON_HEIGHT, {
                "PushButtonType": CANCEL, "Label": _t("common.cancel"),
                "FontHeight": _UI["font_label"],
            })
        dialog.setPosSize(0, 0, WIDTH, current_y + BUTTON_HEIGHT + 16, SIZE)

        frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
        window = frame.getContainerWindow() if frame else None
        dialog.createPeer(create("com.sun.star.awt.Toolkit"), window)
        if not x is None and not y is None:
            ps = dialog.convertSizeToPixel(uno.createUnoStruct("com.sun.star.awt.Size", x, y), TWIP)
            _x, _y = ps.Width, ps.Height
        elif window:
            ps = window.getPosSize()
            _x = ps.Width / 2 - WIDTH / 2
            _y = ps.Height / 2 - HEIGHT / 2
        dialog.setPosSize(_x, _y, 0, 0, POS)

        for field in field_specs:
            if field.get("type") == "list":
                continue
            control = field_controls[field["name"]]
            text_value = str(field["value"])
            control.setSelection(uno.createUnoStruct("com.sun.star.awt.Selection", 0, len(text_value)))

        field_controls["endpoint"].setFocus()

        status_dot_label = dialog.getControl("label_status_dot")
        status_text_label = dialog.getControl("label_status_text")
        model_desc_control = dialog.getControl("edit_model_desc")
        btn_keycloak = dialog.getControl("btn_keycloak")
        toggle_api_key = dialog.getControl("toggle_api_key")
        btn_reload_config = dialog.getControl("btn_reload_config")
        btn_test_token = dialog.getControl("btn_test_token")
        if not btn_reload_config:
            log_to_file("Reload config button not found in dialog")
        else:
            log_to_file("Reload config button created")

        def _read_api_key_value():
            try:
                if api_key_plain_control and api_key_plain_control.isVisible():
                    return str(api_key_plain_control.getModel().Text)
            except Exception:
                pass
            try:
                return str(field_controls["api_key"].getModel().Text)
            except Exception:
                return ""

        def _update_api_status_label(endpoint_val, api_key_val, email_value=None):
            anon_ok, auth_ok = self._api_status(endpoint_val, api_key_val, True)
            label, color = _status_style(anon_ok, auth_ok, email_value)
            status_text = label + (f" ({email_value})" if email_value else "")
            if status_dot_label:
                try:
                    status_dot_label.getModel().TextColor = color
                except Exception:
                    pass
            if status_text_label:
                try:
                    status_text_label.getModel().Label = status_text
                except Exception:
                    pass
            return anon_ok, auth_ok

        def _test_token_and_refresh():
            nonlocal model_descriptions
            try:
                endpoint_val = str(field_controls["endpoint"].getModel().Text)
            except Exception:
                endpoint_val = ""
            api_key_val = _read_api_key_value()
            effective_api_key = self._effective_api_token(api_key_val or request_token)
            log_to_file("Token test: start")
            conn_ok, conn_detail = self._endpoint_connectivity_status(endpoint_val, True)
            if not conn_ok:
                proxy_cfg = self._get_proxy_config()
                err = conn_detail.get("error", _t("settings.token_error_unknown"))
                url = conn_detail.get("url", endpoint_val)
                if proxy_cfg.get("enabled"):
                    self._show_message(
                        _t("settings.models_failed_title"),
                        _t("settings.token_error_proxy", url=url, detail=err)
                    )
                else:
                    self._show_message(
                        _t("settings.models_failed_title"),
                        _t("settings.token_error_unreachable", url=url, detail=err)
                    )
                log_to_file(f"Token test: connectivity failed url={url} err={err}")
                return
            anon_ok, auth_ok = _update_api_status_label(endpoint_val, effective_api_key)
            if not auth_ok:
                self._show_message(
                    _t("settings.models_failed_title"),
                    _t("settings.token_invalid")
                )
                log_to_file("Token test: auth failed")
                return
            try:
                if endpoint_val.startswith("http"):
                    self.set_config("llm_base_urls", endpoint_val)
                if api_key_val:
                    self.set_config("llm_api_tokens", api_key_val)
                    log_to_file("Token test: token saved")
            except Exception:
                pass
            models, model_descriptions_local = self._fetch_models(endpoint_val, effective_api_key, True, include_info=True)
            if not models:
                self._show_message(
                    _t("settings.models_failed_title"),
                    _t("settings.no_models")
                )
                log_to_file("Token test: models empty")
                return
            model_descriptions = model_descriptions_local
            model_control = field_controls.get("model")
            if model_control:
                try:
                    model_control.removeItems(0, model_control.getItemCount())
                except Exception:
                    pass
                try:
                    model_control.addItems(tuple(models), 0)
                except Exception:
                    pass
            selected = models[0]
            try:
                if model_control:
                    model_control.selectItem(selected, True)
            except Exception:
                pass
            try:
                self.set_config("llm_default_models", selected)
            except Exception:
                pass
            desc = model_descriptions.get(selected) or _t("settings.id_prefix", value=selected)
            try:
                model_desc_control.getModel().Text = desc
            except Exception:
                pass
            log_to_file(f"Token test: ok, models={len(models)}")

        class SettingsActionListener(unohelper.Base, XActionListener):
            def __init__(self, outer, endpoint_control, api_key_control, api_key_plain_control, toggle_control):
                self.outer = outer
                self.endpoint_control = endpoint_control
                self.api_key_control = api_key_control
                self.api_key_plain_control = api_key_plain_control
                self.toggle_control = toggle_control
                self.api_key_masked = True

            def actionPerformed(self, event):
                try:
                    source = getattr(event, "Source", None)
                except Exception:
                    source = None
                if source and self.toggle_control and source == self.toggle_control:
                    self.api_key_masked = not self.api_key_masked
                    try:
                        if self.api_key_plain_control:
                            if self.api_key_masked:
                                text = self.api_key_plain_control.getModel().Text
                                self.api_key_control.getModel().Text = text
                                self.api_key_plain_control.setVisible(False)
                                self.api_key_control.setVisible(True)
                            else:
                                text = self.api_key_control.getModel().Text
                                self.api_key_plain_control.getModel().Text = text
                                self.api_key_control.setVisible(False)
                                self.api_key_plain_control.setVisible(True)
                        else:
                            model = self.api_key_control.getModel()
                            model.EchoChar = ord("*") if self.api_key_masked else 0
                            current_text = model.Text
                            model.Text = current_text
                    except Exception:
                        pass
                    try:
                        self.toggle_control.getModel().Label = _t("settings.show") if self.api_key_masked else _t("settings.hide")
                    except Exception:
                        pass
                    return
                try:
                    command = getattr(event, "ActionCommand", "") or ""
                except Exception:
                    command = ""
                if not command:
                    try:
                        if source:
                            command = getattr(source.getModel(), "Name", "") or ""
                    except Exception:
                        command = ""
                if not command:
                    log_to_file("SettingsActionListener: empty ActionCommand")
                if command == "keycloak_login":
                    config_data = self.outer._fetch_config() or {}
                    if not config_data:
                        log_to_file("Keycloak login: DM config unavailable, using local Keycloak settings fallback")
                    self.outer._clear_tokens()
                    access_token = self.outer._authorization_code_flow(config_data)
                    if access_token:
                        try:
                            self.outer._ensure_device_management_state_async()
                        except Exception as exc:
                            log_to_file(f"Post-login enroll scheduling failed: {str(exc)}")
                    email = self.outer._token_email(access_token, allow_network=False) if access_token else None
                    _update_api_status_label(
                        str(self.endpoint_control.getModel().Text) if self.endpoint_control else "",
                        _read_api_key_value() or request_token,
                        email_value=email
                    )
                elif command == "test_token":
                    _test_token_and_refresh()

        def _do_reload_config():
            log_to_file("Reload config: button clicked")
            cancel_flag = {"cancel": False}

            def _show_reload_dialog():
                try:
                    dialog = create("com.sun.star.awt.UnoControlDialog")
                    dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
                    dialog.setModel(dialog_model)
                    dialog.setVisible(False)
                    dialog.setTitle("MIrAI")
                    dialog.setPosSize(0, 0, 340, 110, SIZE)
                    try:
                        dialog_model.AlwaysOnTop = True
                    except Exception:
                        pass
                    try:
                        dialog_model.BackgroundColor = _UI["bg"]
                    except Exception:
                        pass

                    label_model = dialog_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
                    dialog_model.insertByName("reload_label", label_model)
                    label_model.Label = _t("settings.reload_title")
                    label_model.NoLabel = True
                    try:
                        label_model.FontHeight = _UI["font_label"]
                        label_model.TextColor = _UI["text"]
                    except Exception:
                        pass
                    label = dialog.getControl("reload_label")
                    label.setPosSize(20, 24, 300, 24, POSSIZE)

                    btn_model = dialog_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
                    dialog_model.insertByName("reload_cancel", btn_model)
                    btn_model.Label = _t("common.cancel")
                    try:
                        btn_model.FontHeight = _UI["font_small"]
                    except Exception:
                        pass
                    btn = dialog.getControl("reload_cancel")
                    btn.setPosSize(120, 62, 100, 28, POSSIZE)

                    frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
                    window = frame.getContainerWindow() if frame else None
                    toolkit = create("com.sun.star.awt.Toolkit")
                    dialog.createPeer(toolkit, window)
                    if window:
                        ps = window.getPosSize()
                        _x = ps.Width / 2 - 160
                        _y = ps.Height / 2 - 55
                        dialog.setPosSize(_x, _y, 0, 0, POS)
                    dialog.setVisible(True)
                    pump_events(toolkit)
                    log_to_file("Reload config dialog shown")
                    return dialog, label, btn, toolkit
                except Exception as e:
                    log_to_file(f"Reload config dialog failed: {str(e)}")
                    return None, None, None, None

            class CancelListener(unohelper.Base, XActionListener):
                def actionPerformed(self, event):
                    cancel_flag["cancel"] = True
                def disposing(self, event):
                    return

            dialog, label, btn, toolkit = _show_reload_dialog()
            if btn:
                try:
                    btn.addActionListener(CancelListener())
                except Exception:
                    pass

            result_holder = {"settings": None}

            def _worker():
                result_holder["settings"] = self._refresh_config_to_local(cancel_flag=cancel_flag)

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()

            dots_i = 0
            while worker.is_alive():
                try:
                    if cancel_flag["cancel"]:
                        break
                    dots_i += 1
                    dots = "." * ((dots_i % 3) + 1)
                    if label:
                        label.getModel().Label = f'{_t("settings.reload_title_base")}{dots}'
                    if toolkit:
                        pump_events(toolkit)
                except Exception:
                    pass
                time.sleep(0.2)

            if dialog:
                try:
                    dialog.setVisible(False)
                    dialog.dispose()
                except Exception:
                    pass

            if cancel_flag["cancel"]:
                log_to_file("Reload config: canceled by user")
                return

            settings = result_holder.get("settings")
            if not settings:
                self._show_message(
                    _t("settings.reload_dialog_title"),
                    _t("settings.reload_failed")
                )
                return
            try:
                endpoint_val = str(self.get_config("llm_base_urls", ""))
                api_key_val = str(self.get_config("llm_api_tokens", ""))
                model_val = str(self.get_config("llm_default_models", ""))
                models, model_descriptions_local = self._fetch_models(endpoint_val, api_key_val, True, include_info=True)
                if not models:
                    self._show_message(
                        _t("settings.models_failed_title"),
                        _t("settings.models_failed")
                    )
                if field_controls.get("endpoint"):
                    field_controls["endpoint"].getModel().Text = endpoint_val
                if field_controls.get("api_key"):
                    field_controls["api_key"].getModel().Text = self._settings_token_values()[0]
                if field_controls.get("model"):
                    model_control = field_controls["model"]
                    try:
                        model_control.removeItems(0, model_control.getItemCount())
                    except Exception:
                        pass
                    if models:
                        try:
                            model_control.addItems(tuple(models), 0)
                        except Exception:
                            pass
                    if model_val:
                        try:
                            model_control.selectItem(model_val, True)
                        except Exception:
                            pass
                desc = model_descriptions_local.get(model_val) if models else model_descriptions.get(model_val)
                if not desc:
                    desc = _t("settings.id_prefix", value=model_val) if model_val else _t("settings.no_description")
                model_desc_control.getModel().Text = desc
            except Exception:
                pass

        class ReloadActionListener(unohelper.Base, XActionListener):
            def actionPerformed(self, event):
                _do_reload_config()

            def disposing(self, event):
                return

        listener = SettingsActionListener(
            self,
            field_controls.get("endpoint"),
            field_controls.get("api_key"),
            api_key_plain_control,
            toggle_api_key,
        )
        try:
            btn_keycloak.addActionListener(listener)
            log_to_file("Keycloak listener attached")
        except Exception as e:
            log_to_file(f"Keycloak listener attach failed: {str(e)}")
        try:
            btn_keycloak.getModel().ActionCommand = "keycloak_login"
        except Exception as e:
            log_to_file(f"Keycloak ActionCommand set failed: {str(e)}")
        if toggle_api_key:
            try:
                toggle_api_key.addActionListener(listener)
            except Exception:
                pass
            try:
                toggle_api_key.getModel().Name = "toggle_api_key"
            except Exception:
                pass

        if btn_test_token:
            try:
                btn_test_token.addActionListener(listener)
            except Exception:
                pass
            try:
                btn_test_token.getModel().ActionCommand = "test_token"
            except Exception:
                pass

        if btn_reload_config:
            try:
                btn_reload_config.addActionListener(ReloadActionListener())
                log_to_file("Reload config action listener attached")
            except Exception as e:
                log_to_file(f"Reload config action listener attach failed: {str(e)}")
            try:
                btn_reload_config.getModel().ActionCommand = "reload_config"
            except Exception as e:
                log_to_file(f"Reload config ActionCommand set failed: {str(e)}")

        # Apply model selection after peer creation
        model_control = field_controls.get("model")
        if model_control:
            try:
                if current_model:
                    model_control.selectItem(current_model, True)
                selected = ""
                try:
                    selected = model_control.getSelectedItem()
                except Exception:
                    selected = current_model
                if not selected:
                    selected = current_model
                desc = model_descriptions.get(selected)
                if not desc:
                    desc = _t("settings.id_prefix", value=selected)
                model_desc_control.getModel().Text = desc
            except Exception:
                pass

        if model_control:
            class ModelItemListener(unohelper.Base, XItemListener):
                def __init__(self, outer, control, desc_control, descriptions):
                    self.outer = outer
                    self.control = control
                    self.desc_control = desc_control
                    self.descriptions = descriptions

                def itemStateChanged(self, event):
                    try:
                        value = self.control.getSelectedItem()
                        if value:
                            self.outer.set_config("llm_default_models", value)
                            desc = self.descriptions.get(value)
                            if not desc:
                                desc = _t("settings.id_prefix", value=value)
                            self.desc_control.getModel().Text = desc
                        else:
                            self.desc_control.getModel().Text = _t("settings.no_description")
                    except Exception:
                        pass

                def disposing(self, event):
                    return

            try:
                model_control.addItemListener(ModelItemListener(self, model_control, model_desc_control, model_descriptions))
            except Exception:
                pass

        if dialog.execute():
            result = {}
            for field in field_specs:
                control = field_controls[field["name"]]
                if field.get("type") == "list":
                    try:
                        selected = control.getSelectedItem()
                    except Exception:
                        selected = ""
                    if not selected:
                        selected = current_model
                    log_to_file(f"Model dialog selection text='{selected}' current='{current_model}'")
                    result[field["name"]] = selected
                    if selected:
                        self.set_config("llm_default_models", selected)
                else:
                    if field.get("name") == "api_key" and api_key_plain_control:
                        try:
                            if api_key_plain_control.isVisible():
                                control = api_key_plain_control
                        except Exception:
                            pass
                    control_text = control.getModel().Text
                    if (field.get("name") == "api_key" and not control_text
                            and self._device_management_enabled()):
                        continue
                    result[field["name"]] = control_text
        else:
            result = {}

        dialog.dispose()
        return result
    #end sharealike section

    def _needs_first_enrollment(self):
        """True si le wizard d'enrôlement doit s'ouvrir (DM actif) : jamais
        enrôlé et sans session valide, ou enrôlé sans creds relay ni session
        valide."""
        try:
            # No enrollment needed if device management is disabled
            if not self._device_management_enabled():
                return False
            enrolled = self._as_bool(self._get_config_from_file("enrolled", False))
            access_token = str(self._get_config_from_file("access_token", "")).strip()
            has_login = bool(access_token) and not self._token_is_expired(access_token)
            if enrolled:
                # Enrôlé « à moitié » : sans creds relay le DM ne mint aucun
                # llmToken, et le ré-enrôlement de fond a lui-même besoin d'une
                # session valide (il dérive l'email du token). Sans les deux, le
                # poste ne peut plus sortir de l'impasse tout seul → wizard.
                if not self._relay_credentials_valid() and not has_login:
                    log_to_file(
                        "[ENROLL] enrolled=True mais ni creds relay ni session "
                        "valide — wizard d'enrôlement requis"
                    )
                    return True
                return False
            if has_login:
                return False
            return True
        except Exception:
            return False

    def _run_first_enrollment(self):
        """Run the first-time enrollment: wizard → fetch config → auth flow."""
        log_to_file("First enrollment detected, starting wizard flow")
        # Un fetch déjà en vol ferait refuser le fetch forcé (garde de récursion)
        # et abandonner le wizard pour la session : on attend sa fin.
        deadline = time.time() + 15
        while self._fetching_config and time.time() < deadline:
            time.sleep(0.1)
        config_data = self._fetch_config(force=True)
        if not config_data:
            log_to_file("First enrollment: config fetch failed")
            return False
        access_token = self._ensure_access_token(config_data, interactive=True)
        if access_token:
            log_to_file("First enrollment: auth succeeded")
            self._send_telemetry("EnrollSuccess", {"status": "ok"})
            return True
        log_to_file("First enrollment: auth flow canceled or failed")
        self._send_telemetry("EnrollFailed", {"status": "canceled"})
        return False

    def execute(self, args):
        """XJob.execute : appelé par oxt/Jobs.xcu (onFirstVisibleTask, onLoad,
        onNew). Le constructeur a déjà lancé les tâches de démarrage."""
        log_to_file("=== XJob.execute called ===")

    # Actions qui ne portent ni sur le document ni sur une sélection : elles
    # doivent aboutir dans TOUS les contextes, Writer comme Calc, avec ou sans
    # sélection, et même sans document ouvert.
    _SHELL_ACTIONS = ("settings", "AboutDialog", "Documentation", "TestModel")

    def _handle_shell_action(self, action):
        """Traite les actions non textuelles. Retourne True si prise en charge."""
        if action not in self._SHELL_ACTIONS:
            return False
        try:
            if action == "settings":
                self._send_telemetry("OpenSettings", {"action": "open_settings"})
                apply_settings_result(self, self.settings_box("Settings"))
            elif action == "AboutDialog":
                self._send_telemetry("AboutDialog", {"action": "about"})
                self._show_about_dialog()
            elif action == "Documentation":
                self._send_telemetry("OpenDocumentation",
                                     {"action": "open_documentation"})
                self._open_url_config("doc_url")
            elif action == "TestModel":
                # Import paresseux : le moteur n'est chargé qu'à l'usage.
                from .core.entry import test_model_capabilities
                test_model_capabilities(self)
        except Exception as exc:
            # Une action de coquille qui échoue doit se voir.
            log_to_file(f"[dispatch] action {action} en échec : {exc}")
            self._show_message(
                _t("msg.action_failed_title"),
                _t("msg.action_failed_body", action=action, exc=exc))
        return True

    def _open_url_config(self, key):
        """Ouvre l'URL d'une clé de configuration, avec repli sur le portail."""
        url = self.get_config(key, "") or self.get_config("portal_url", "")
        if not url:
            raise RuntimeError(f"aucune URL configurée ({key})")
        if not open_url(self.ctx, url):
            raise RuntimeError(_t("msg.no_browser"))

    def trigger(self, args):
        # Parse &src= suffix if present (menu, key)
        if "&src=" in args:
            action, source = args.split("&src=", 1)
        else:
            action, source = args, "user"
        self._trigger_source = source
        self._log(f"=== trigger called: action={action} src={source} ===")
        try:
            self._schedule_config_refresh(force=True, reason=f"trigger:{action}")
        except Exception:
            pass

        # Reuse a still-fresh persisted config so the action starts immediately
        # instead of blocking on a network fetch (the async refresh above keeps
        # it up to date). Only block when we have nothing cached at all.
        self._hydrate_config_cache()

        # Wait for any in-progress config fetch to finish (e.g. from __init__)
        # so the trigger has access to config/token for LLM calls
        self._wait_for_config(action)

        # First-time enrollment: intercept before any action
        # Informational/navigation actions bypass enrollment check
        _enrollment_bypass = {"Documentation", "settings", "AboutDialog"}
        if action not in _enrollment_bypass and not self.runtime.enrollment_dismissed and self._needs_first_enrollment():
            with self.runtime.enrollment_wizard_lock:
                if self.runtime.enrollment_wizard_active:
                    log_to_file("[ENROLL] Trigger: wizard already running, skipping")
                    return
                self.runtime.enrollment_wizard_active = True
            try:
                if not self._run_first_enrollment():
                    self.runtime.enrollment_dismissed = True
                    return
            finally:
                self.runtime.enrollment_wizard_active = False

        desktop = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx)
        model = desktop.getCurrentComponent()
        self._log(f"Current component type: {type(model)}")

        # Palette universelle (démonstrateur moteur MCP) — import paresseux :
        # zéro coût au chargement de l'extension.
        if action == "OpenAssistant":
            from .core.entry import open_palette
            open_palette(self, model)
            return

        if self._handle_shell_action(action):
            return

        # Aucune branche n'a traité l'action : le dire, plutôt que de rendre la
        # main en silence.
        log_to_file(f"[dispatch] action non gérée : {action!r} "
                    f"(document={type(model).__name__}, src={source})")
        self._report_unhandled_action(action, model)
        self._show_message(
            _t("msg.action_unavailable_title"),
            _t("msg.action_unavailable_body", action=action))

# pythonloader loads a static g_ImplementationHelper variable
log_to_file("=== Loading mirai extension module ===")
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    MainJob,  # UNO object class
    "fr.gouv.interieur.mirai.do",  # implementation name
    ("com.sun.star.task.JobExecutor", "com.sun.star.task.Job"), )  # implemented services
log_to_file("=== mirai extension registered successfully ===")
