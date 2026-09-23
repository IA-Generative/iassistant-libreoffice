import sys
import unohelper
import officehelper
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
# it itself — it reuses this reference. See _install_oxt_inprocess (which
# prefers the import-free PackageManagerFactory and uses this only as a
# fallback).
try:
    from com.sun.star.deployment import ExtensionManager as _EXT_MGR_SINGLETON
except Exception:
    _EXT_MGR_SINGLETON = None

# Extension identifier (matches oxt/description.xml <identifier>). Used to remove a
# prior registration before re-installing in-process (avoids duplicate components).
_EXTENSION_IDENTIFIER = "fr.gouv.interieur.mirai"

# Feed natif LibreOffice (<update-information>) servi par le DM
# (device-management#23). Le chemin doit rester aligné avec
# scripts/inject_update_feed.py (FEED_PATH) qui le bake dans description.xml.
_UPDATE_FEED_PATH = "/catalog/mirai-libreoffice/update.xml"
_UPDATE_FEED_NS = "http://openoffice.org/extensions/update/2006"

# Route native pilotée (spec 2026-09-22) : refus mémorisé, attente de
# l'installation par LibreOffice, bornes du déclenchement.
_UPDATE_POSTPONE_SECONDS = 24 * 3600
_NATIVE_INSTALL_WAIT_SECONDS = 900
_NATIVE_POLL_SECONDS = 5
_NATIVE_TRIGGER_TIMEOUT_SECONDS = 30
_NATIVE_MAX_ATTEMPTS = 2
_CLOSE_RETRY_SECONDS = 120
_CLOSE_RETRY_INTERVAL_SECONDS = 3
_PROMPT_WIZARD_WAIT_SECONDS = 120
_PROMPT_GRACE_SECONDS = 30
_MAIN_THREAD_TIMEOUT_AFTER_START = "timeout after start"
_MAIN_THREAD_VETOED = "vetoed"
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
    from com.sun.star.awt import XActionListener, XItemListener, XMouseListener, XWindowListener, XTopWindowListener
    from com.sun.star.beans import PropertyValue
    from com.sun.star.container import XNamed
except ImportError:
    # Running outside LibreOffice (e.g. unopkg install) — provide safe stubs
    class _S1: pass
    class _S2: pass
    class _S3: pass
    class _S4: pass
    class _S5: pass
    class _S6: pass
    class _S7: pass
    class _S8: pass
    class _S9: pass
    class _S10: pass
    XJobExecutor = _S1
    XJob = _S2
    MSG_BUTTONS = None
    XActionListener = _S3
    XItemListener = _S4
    XMouseListener = _S5
    XWindowListener = _S6
    XTopWindowListener = _S7
    PropertyValue = _S8
    XNamed = _S9
try:
    from com.sun.star.ui import XContextMenuInterceptor
    from com.sun.star.ui import XContextMenuInterception as _XContextMenuInterception
    _HAS_CONTEXT_MENU_INTERFACE = True
except ImportError:
    class _S10: pass
    XContextMenuInterceptor = _S10
    _XContextMenuInterception = None
    _HAS_CONTEXT_MENU_INTERFACE = False
try:
    from com.sun.star.document import XEventListener as XDocumentEventListener
    _HAS_DOC_EVENT_LISTENER = True
except ImportError:
    class _S11: pass
    XDocumentEventListener = _S11
    _HAS_DOC_EVENT_LISTENER = False
import uno
import os
import logging
import re
import uuid
import time
import base64
import hashlib
import threading
import socket
from .formatting import insert_formatted
from .menu_actions.writer import handle_writer_action
from .menu_actions.calc import handle_calc_action
from .security_flow import (
    SecureBootstrapFlow,
    FileJsonStore,
    FileQueueStore,
    default_vault,
    Ed25519Provider,
)


PLUGIN_NAME = "MIrAI-LibreOffice"
_DEFAULT_USER_AGENT = PLUGIN_NAME
_current_user_agent = _DEFAULT_USER_AGENT
CONTEXT_MENU_IGNORED = 0
CONTEXT_MENU_EXECUTE_MODIFIED = 2

MIRAI_CONTEXT_MENU_ITEMS = (
    ("Résumer la sélection", "service:fr.gouv.interieur.mirai.do?SummarizeSelection&src=context"),
    ("Reformuler", "service:fr.gouv.interieur.mirai.do?SimplifySelection&src=context"),
    ("Corriger", "service:fr.gouv.interieur.mirai.do?CorrectSelection&src=context"),
    ("Traduire", "service:fr.gouv.interieur.mirai.do?TranslateSelection&src=context"),
)


class MirAIContextMenuInterceptor(unohelper.Base, XContextMenuInterceptor):
    """Adds the MirAI submenu to Writer contextual menus."""

    def __init__(self, ctx, job):
        self.ctx = ctx
        self.job = job

    def notifyContextMenuExecute(self, event):  # noqa: N802
        try:
            container = event.ActionTriggerContainer
            if not self._is_writer_context(event):
                self.job._log("[context-menu] ignored: not a Writer context")
                return CONTEXT_MENU_IGNORED
            self._insert_mirai_submenu(container)
            self.job._log("[context-menu] MirAI submenu inserted")
            return CONTEXT_MENU_EXECUTE_MODIFIED
        except Exception as e:
            self.job._log(f"[context-menu] insertion failed: {type(e).__name__}: {e}")
            return CONTEXT_MENU_IGNORED

    def _is_writer_context(self, event):
        try:
            controller = getattr(event, "Source", None)
            model = getattr(controller, "Model", None)
            if model is not None and hasattr(model, "Text"):
                return True
        except Exception:
            pass
        return True

    def _create_menu_service(self, container, service_name):
        try:
            return container.createInstance(service_name)
        except Exception:
            sm = self.ctx.getServiceManager()
            return sm.createInstanceWithContext(service_name, self.ctx)

    def _create_action_trigger(self, container, text, command_url, sub_container=None):
        action = self._create_menu_service(container, "com.sun.star.ui.ActionTrigger")
        action.setPropertyValue("Text", text)
        action.setPropertyValue("CommandURL", command_url)
        if sub_container is not None:
            action.setPropertyValue("SubContainer", sub_container)
        return action

    def _insert_mirai_submenu(self, container):
        submenu = self._create_menu_service(container, "com.sun.star.ui.ActionTriggerContainer")
        for index, (label, command_url) in enumerate(MIRAI_CONTEXT_MENU_ITEMS):
            submenu.insertByIndex(index, self._create_action_trigger(submenu, label, command_url))
        root_entry = self._create_action_trigger(
            container,
            "MirAI",
            "service:fr.gouv.interieur.mirai.do?MenuSeparator&src=context",
            submenu,
        )
        container.insertByIndex(0, root_entry)



class MirAIDocumentEventListener(unohelper.Base, XDocumentEventListener):
    """Listens to global document events to register the context menu interceptor."""

    def __init__(self, ctx, register_fn):
        self.ctx = ctx
        self._register_fn = register_fn

    def notifyEvent(self, event):  # noqa: N802
        event_name = getattr(event, 'EventName', '') or ''
        if event_name not in ('OnLoad', 'OnNew', 'OnCreate'):
            return
        try:
            model = event.Source
            if model is not None and hasattr(model, 'Text'):
                controller = model.CurrentController
                self._register_fn(controller, f"docEvent:{event_name}")
        except Exception as e:
            log_to_file(f"[doc-event] {event_name} handler failed: {type(e).__name__}: {e}")

    def disposing(self, source):  # noqa: N802
        pass


def _extract_frame_from_job_args(args):
    """Return the XFrame from XJob.execute args (present for onLoad/onNew events)."""
    try:
        for nv in (args or []):
            if getattr(nv, 'Name', None) == 'Environment':
                for env_nv in (getattr(nv, 'Value', None) or []):
                    if getattr(env_nv, 'Name', None) == 'Frame':
                        return env_nv.Value
    except Exception:
        pass
    return None


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

# ── UI colour palette (DSFR-inspired) ──────────────────────────────
_UI = {
    "bg":              0xFFFFFF,   # white background
    "bg_section":      0xF6F6F6,   # light-grey section background
    "bg_input":        0xFCFCFC,   # very light input background
    "bg_header":      0x000091,   # Bleu France (DSFR primary)
    "bg_accent":       0xF5F5FE,   # light blue accent
    "text":            0x161616,   # almost-black text
    "text_secondary":  0x666666,   # secondary grey text
    "text_light":      0x929292,   # light hint text
    "text_on_dark":    0xFFFFFF,   # white text on dark backgrounds
    "border":          0xDDDDDD,   # subtle border grey
    "primary":         0x000091,   # Bleu France
    "primary_hover":   0x1212FF,   # lighter blue
    "success":         0x18753C,   # DSFR success green
    "warning":         0xB34000,   # DSFR warning orange
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

# Configure logging once at module level (thread-safe, not per-call)
_log_file_path = os.path.join(os.path.expanduser('~'), 'log.txt')
logging.basicConfig(filename=_log_file_path, level=logging.INFO, format='%(asctime)s - %(message)s')

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

def _redacted_headers(headers):
    safe = {}
    for key, value in (headers or {}).items():
        safe[key] = _redact_header_value(key, value)
    return safe

def _curl_headers_for_log(headers):
    parts = []
    for key, value in (headers or {}).items():
        safe_value = _redact_header_value(key, value)
        parts.append(f"-H '{key}: {safe_value}'")
    return " ".join(parts)

def log_to_file(message):
    """Journalise sans jamais pouvoir faire échouer l'appelant.

    `logging.info` peut lever (handler fermé, disque plein, fichier de log
    verrouillé). Comme les appels à cette fonction sont disséminés au milieu de
    chemins critiques eux-mêmes enveloppés dans des `except Exception` larges,
    une panne de JOURNALISATION se transformait en perte silencieuse de
    données : observé sur `_persist_bootstrap_config`, où un log levé entre
    deux `set_config` faisait perdre `llmTokenExpiresAt` — donc un jeton LLM
    sans date d'expiration, rejoué jusqu'au 401.
    """
    try:
        logging.info(message)
    except Exception:
        pass


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

    Vécu le 2026-07-26 pendant l'enrôlement SSO : `stream_request` (qui pompe)
    lancé hors du thread principal.

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

    Tout aplatir en `stringValue` (comportement d'origine) rend la trace
    inexploitable comme mesure : un nombre de paragraphes rendu « 45 » ne peut
    plus être agrégé, moyenné ni seuillé côté Tempo/Grafana.

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
        span_name: Name of the span (e.g., "ExtendSelection", "EditSelection")
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
        span_name: Name of the span (e.g., "ExtendSelection", "EditSelection")
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

        # Preferred secure telemetry pipeline (bootstrap/enroll/token rotation + offline queue).
        if hasattr(config, "_secure_send_telemetry_payload"):
            try:
                handled = bool(config._secure_send_telemetry_payload(payload, span_name))
                if handled:
                    return
            except Exception as e:
                log_to_file(f"Secure telemetry pipeline unavailable, fallback legacy sender: {str(e)}")
        
        if log_json:
            log_to_file(f"=== Telemetry Request ===")
            log_to_file(f"URL: {endpoint}")
            log_to_file(f"Method: POST")
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
            log_to_file(f"=== Request Headers ===")
            for header_name, header_value in req.headers.items():
                if header_name.lower() == 'authorization':
                    log_to_file(f"{header_name}: <redacted>")
                else:
                    log_to_file(f"{header_name}: {header_value}")
            log_to_file(f"Content-Length: {len(json_data)}")
            log_to_file(f"===")
        
        ssl_context = config.get_ssl_context() if hasattr(config, "get_ssl_context") else ssl.create_default_context()

        if hasattr(config, "_urlopen"):
            response = config._urlopen(req, context=ssl_context, timeout=5)
        else:
            response = urllib.request.urlopen(req, context=ssl_context, timeout=5)
        with response as response:
            response_status = response.status
            response_headers = dict(response.headers)
            response_body = response.read().decode('utf-8') if response.readable() else ""
            
            if log_json:
                log_to_file(f"=== Telemetry Response ===")
                log_to_file(f"Status: {response_status}")
                log_to_file(f"Headers: {json.dumps(response_headers, indent=2)}")
                log_to_file(f"Body: {response_body if response_body else '(empty)'}")
                log_to_file(f"=== End Telemetry Response ===")
            
            log_to_file(f"Telemetry trace sent successfully: {span_name}, status: {response_status}")
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if hasattr(e, 'read') else ""
        log_to_file(f"=== Telemetry HTTP Error ===")
        log_to_file(f"URL: {endpoint}")
        log_to_file(f"Status: {e.code}")
        log_to_file(f"Reason: {e.reason}")
        log_to_file(f"Headers: {dict(e.headers) if hasattr(e, 'headers') else 'N/A'}")
        log_to_file(f"Body: {error_body if error_body else '(empty)'}")
        log_to_file(f"=== End Telemetry Error ===")
    except Exception as e:
        log_to_file(f"=== Telemetry Exception ===")
        log_to_file(f"URL: {endpoint}")
        log_to_file(f"Error: {str(e)}")
        log_to_file(f"Type: {type(e).__name__}")
        log_to_file(f"=== End Telemetry Exception ===")


# The MainJob is a UNO component derived from unohelper.Base class
# and also the XJobExecutor, the implemented interface
class MainJob(unohelper.Base, XJobExecutor, XJob):
    # Class-level flags shared across all instances to prevent duplicate wizards/updates
    _enrollment_dismissed_cls = False
    _enrollment_wizard_active_cls = False
    _enrollment_wizard_lock_cls = threading.Lock()
    _update_in_progress_cls = False
    # Target versions whose install-script launch was blocked by the workstation
    # policy (e.g. WinError 5 from AppLocker / Defender ASR). Recorded so we stop
    # re-downloading / re-prompting the same update in a loop.
    _update_launch_blocked_cls = set()
    _update_lock_cls = threading.Lock()
    # Réconciliation post-redémarrage de la MAJ précédente : une seule fois par
    # process (voir _schedule_update_reconciliation / _reconcile_update_state).
    _update_reconcile_started_cls = False
    # Diagnostic passif du feed natif : une seule fois par process
    # (voir _schedule_native_feed_check / _check_native_feed).
    _feed_check_started_cls = False
    _context_menu_refs_cls = []
    _context_menu_controller_ids_cls = set()
    _context_menu_schedule_started_cls = False
    _doc_event_listener_cls = None
    _doc_event_broadcaster_cls = None

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
        self._config_write_lock = threading.Lock()
        self._edit_dialog = None
        self._resize_dialog = None
        self._formula_dialog = None
        self._formula_dialog_state = None
        self._secure_flow = None
        self._secure_flow_lock = threading.RLock()
        self._secure_flow_init_error = None
        # enrollment flags are class-level (shared across instances)
        self._secure_legacy_fallback_logged = False
        self._last_loaded_ca_bundle = None
        self._last_ca_bundle_error = None
        self._last_logged_ca_bundle_error = None
        # Update & feature toggling (schema_version 2)
        self._features_cache = {}
        # Use class-level flags (shared across instances)
        # update flags are class-level (shared across instances)
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
            path_settings = self.sm.createInstanceWithContext('com.sun.star.util.PathSettings', self.ctx)
            user_config_path = getattr(path_settings, "UserConfig")
            if user_config_path.startswith('file://'):
                user_config_path = str(uno.fileUrlToSystemPath(user_config_path))
            config_file_path = os.path.join(user_config_path, "config.json")
            log_to_file(f"Profile config path: {config_file_path}")
        except Exception as e:
            log_to_file(f"Failed to resolve profile config path: {str(e)}")
        
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
            self._warmup_secure_flow_async()
            self._trigger_source = "auto"
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
        # de LibreOffice sur la flotte et détecte un feed DM absent
        # (device-management#23) avant d'appuyer le déploiement large dessus.
        try:
            self._schedule_native_feed_check()
        except Exception as e:
            log_to_file(f"Failed to schedule native feed check: {str(e)}")

        # Proxy consistency check removed — proxy is configured via
        # bootstrap or the Settings dialog, no startup prompt needed.

        # Auto-launch enrollment wizard on first use (deferred to let UI init)
        try:
            self._schedule_enrollment_check()
        except Exception as e:
            log_to_file(f"Failed to schedule enrollment check: {str(e)}")

        try:
            self._schedule_context_menu_registration("startup")
        except Exception as e:
            log_to_file(f"[context-menu] failed to schedule registration: {str(e)}")

        # Register a global document event listener so the context menu interceptor
        # is installed automatically on every document open (OnLoad, OnNew).
        try:
            if _HAS_DOC_EVENT_LISTENER and MainJob._doc_event_listener_cls is None:
                broadcaster = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.GlobalEventBroadcaster", self.ctx
                )
                listener = MirAIDocumentEventListener(self.ctx, self._register_writer_context_menu_on)
                broadcaster.addEventListener(listener)
                MainJob._doc_event_listener_cls = listener
                MainJob._doc_event_broadcaster_cls = broadcaster
                log_to_file("[doc-event] global document event listener registered")
        except Exception as e:
            log_to_file(f"[doc-event] listener registration failed: {type(e).__name__}: {e}")
    
    def _log(self, message):
        log_to_file(message)

    # In LibreOffice 25.x the API was renamed:
    #   addContextMenuInterceptor    → registerContextMenuInterceptor
    #   removeContextMenuInterceptor → releaseContextMenuInterceptor
    # We probe both so the extension works on LO 7.x and LO 24+/25+.
    _REGISTER_METHOD = None   # resolved once at runtime
    _RELEASE_METHOD = None

    def _resolve_context_menu_method_names(self, obj):
        """Detect the correct method name for the current LibreOffice version."""
        if MainJob._REGISTER_METHOD is not None:
            return MainJob._REGISTER_METHOD
        for new, old in (
            ("registerContextMenuInterceptor", "addContextMenuInterceptor"),
        ):
            if hasattr(obj, new):
                MainJob._REGISTER_METHOD = new
                MainJob._RELEASE_METHOD = "releaseContextMenuInterceptor"
                self._log(f"[ctx-qi] resolved register method: {new} (LO 25.x API)")
                return new
            if hasattr(obj, old):
                MainJob._REGISTER_METHOD = old
                MainJob._RELEASE_METHOD = "removeContextMenuInterceptor"
                self._log(f"[ctx-qi] resolved register method: {old} (LO 7.x API)")
                return old
        return None

    def _get_context_menu_interception_iface(self, controller):
        """Return (obj, obj_id) where obj exposes registerContextMenuInterceptor (or the old name).

        The controller itself exposes XContextMenuInterception directly when its getTypes()
        includes that interface — no queryInterface needed.
        """
        frame = getattr(controller, 'Frame', None)
        for obj_label, obj in (("controller", controller), ("frame", frame)):
            if obj is None:
                continue
            method_name = self._resolve_context_menu_method_names(obj)
            if method_name is not None:
                self._log(f"[ctx-qi] {obj_label} has {method_name} directly")
                return obj, id(obj)
        self._log("[ctx-qi] neither controller nor frame exposes context menu interception")
        return None, None

    def _invoke_via_core_reflection(self, target_obj, method_name, invoke_args):
        """Invoke a method on a UNO object via CoreReflection, bypassing Python-UNO proxy limits.

        This is a fallback for when queryInterface returns a cached proxy that doesn't
        expose the method via Python-UNO's __getattr__.
        """
        interface_name = "com.sun.star.ui.XContextMenuInterception"
        try:
            refl = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.reflection.CoreReflection", self.ctx
            )
            idl_class = refl.forName(interface_name)
            if idl_class is None:
                self._log(f"[core-refl] {interface_name} not found in CoreReflection")
                return False
            methods = idl_class.getMethods()
            self._log(f"[core-refl] found {len(methods)} methods in {interface_name}")
            for m in methods:
                try:
                    name = m.getName()
                    self._log(f"[core-refl] method: {name}")
                    if name == method_name:
                        mutable_args = list(invoke_args)
                        m.invoke(target_obj, mutable_args)
                        self._log(f"[core-refl] {method_name} invoked via CoreReflection: OK")
                        return True
                except Exception as e:
                    self._log(f"[core-refl] invoke {method_name} failed: {type(e).__name__}: {e}")
            self._log(f"[core-refl] {method_name} not found in {interface_name}")
            return False
        except Exception as e:
            self._log(f"[core-refl] setup failed: {type(e).__name__}: {e}")
            return False

    def _do_add_interceptor(self, obj, interceptor, obj_label):
        """Call register(Context)MenuInterceptor on obj (name differs by LO version)."""
        method_name = MainJob._REGISTER_METHOD or self._resolve_context_menu_method_names(obj)
        if method_name and hasattr(obj, method_name):
            getattr(obj, method_name)(interceptor)
            self._log(f"[ctx-add] {obj_label}: {method_name} OK")
            return True
        # Last resort: CoreReflection invocation (bypasses Python-UNO proxy type limits)
        self._log(f"[ctx-add] {obj_label}: direct call unavailable, trying CoreReflection")
        for name in ("registerContextMenuInterceptor", "addContextMenuInterceptor"):
            if self._invoke_via_core_reflection(obj, name, [interceptor]):
                return True
        return False

    def _register_current_writer_context_menu(self, reason="manual"):
        try:
            if not _HAS_CONTEXT_MENU_INTERFACE:
                self._log(f"[context-menu] skip ({reason}): XContextMenuInterceptor unavailable")
                return False
            desktop = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx
            )
            model = desktop.getCurrentComponent()
            if model is None or not hasattr(model, "Text"):
                self._log(f"[context-menu] skip ({reason}): not a Writer document")
                return False
            controller = model.CurrentController
            frame = getattr(controller, 'Frame', None)
            # Try queryInterface strategies first (logs diagnostics internally)
            iface, obj_id = self._get_context_menu_interception_iface(controller)
            if iface is not None:
                if obj_id in MainJob._context_menu_controller_ids_cls:
                    self._log(f"[context-menu] already registered ({reason})")
                    return True
                interceptor = MirAIContextMenuInterceptor(self.ctx, self)
                if self._do_add_interceptor(iface, interceptor, f"qi-iface({reason})"):
                    MainJob._context_menu_refs_cls.append((iface, interceptor))
                    MainJob._context_menu_controller_ids_cls.add(obj_id)
                    self._log(f"[context-menu] interceptor registered via qi ({reason})")
                    return True
            # Fallback: try CoreReflection on controller and frame directly
            for cand_label, cand in (("controller", controller), ("frame", frame)):
                if cand is None:
                    continue
                cand_id = id(cand)
                if cand_id in MainJob._context_menu_controller_ids_cls:
                    self._log(f"[context-menu] already registered on {cand_label} ({reason})")
                    return True
                interceptor = MirAIContextMenuInterceptor(self.ctx, self)
                if self._do_add_interceptor(cand, interceptor, f"{cand_label}({reason})"):
                    MainJob._context_menu_refs_cls.append((cand, interceptor))
                    MainJob._context_menu_controller_ids_cls.add(cand_id)
                    self._log(f"[context-menu] interceptor registered on {cand_label} ({reason})")
                    return True
            self._log(f"[context-menu] all registration strategies failed ({reason})")
            return False
        except Exception as e:
            self._log(f"[context-menu] registration failed ({reason}): {type(e).__name__}: {e}")
            return False

    def _register_writer_context_menu_on(self, controller, reason="manual"):
        """Register the MirAI context menu interceptor on the given controller."""
        try:
            if not _HAS_CONTEXT_MENU_INTERFACE:
                self._log(f"[context-menu] skip ({reason}): XContextMenuInterceptor unavailable")
                return False
            if controller is None:
                self._log(f"[context-menu] skip ({reason}): no controller")
                return False
            model = getattr(controller, 'Model', None)
            if model is None or not hasattr(model, 'Text'):
                self._log(f"[context-menu] skip ({reason}): not a Writer controller")
                return False
            frame = getattr(controller, 'Frame', None)
            # Try queryInterface strategies first (logs diagnostics internally)
            iface, obj_id = self._get_context_menu_interception_iface(controller)
            if iface is not None:
                if obj_id in MainJob._context_menu_controller_ids_cls:
                    self._log(f"[context-menu] already registered ({reason})")
                    return True
                interceptor = MirAIContextMenuInterceptor(self.ctx, self)
                if self._do_add_interceptor(iface, interceptor, f"qi-iface({reason})"):
                    MainJob._context_menu_refs_cls.append((iface, interceptor))
                    MainJob._context_menu_controller_ids_cls.add(obj_id)
                    self._log(f"[context-menu] interceptor registered via qi ({reason})")
                    return True
            # Fallback: try CoreReflection on controller and frame directly
            for cand_label, cand in (("controller", controller), ("frame", frame)):
                if cand is None:
                    continue
                cand_id = id(cand)
                if cand_id in MainJob._context_menu_controller_ids_cls:
                    self._log(f"[context-menu] already registered on {cand_label} ({reason})")
                    return True
                interceptor = MirAIContextMenuInterceptor(self.ctx, self)
                if self._do_add_interceptor(cand, interceptor, f"{cand_label}({reason})"):
                    MainJob._context_menu_refs_cls.append((cand, interceptor))
                    MainJob._context_menu_controller_ids_cls.add(cand_id)
                    self._log(f"[context-menu] interceptor registered on {cand_label} ({reason})")
                    return True
            self._log(f"[context-menu] all registration strategies failed ({reason})")
            return False
        except Exception as e:
            self._log(f"[context-menu] registration failed ({reason}): {type(e).__name__}: {e}")
            return False

    def _schedule_context_menu_registration(self, reason="startup", force=False):
        if MainJob._context_menu_schedule_started_cls and not force:
            self._log("[context-menu] deferred registration already scheduled")
            return
        MainJob._context_menu_schedule_started_cls = True
        delays = (0.5, 1.5, 3.0, 6.0, 10.0)
        self._log(f"[context-menu] scheduling deferred registrations ({reason})")

        def _attempt(attempt_index):
            try:
                if self._register_current_writer_context_menu(f"deferred#{attempt_index + 1}"):
                    self._log(f"[context-menu] deferred registration succeeded on attempt {attempt_index + 1}")
                    return
            except Exception as e:
                self._log(f"[context-menu] deferred attempt {attempt_index + 1} failed: {e}")
            if attempt_index + 1 >= len(delays):
                self._log("[context-menu] deferred registration exhausted")
                MainJob._context_menu_schedule_started_cls = False

        for index, delay in enumerate(delays):
            timer = threading.Timer(delay, _attempt, args=(index,))
            timer.daemon = True
            timer.start()

    # Condensed action names for telemetry — appears as plugin.action attribute
    _ACTION_NAMES = {
        "ExtensionLoaded": "launch",
        "ExtensionUpdated": "update",
        # Étapes du flux de MAJ (issue #9) — permettent de mesurer le taux de
        # succès PAR ÉTAPE sur la flotte (staged → accepted → installed confirmé),
        # au lieu d'un unique événement ambigu.
        "UpdateStaged": "update",
        "UpdateAccepted": "update",
        "UpdatePostponed": "update",
        "UpdateInstalledPendingRestart": "update",
        "UpdateInstallFailed": "update",
        "NativeFeedCheck": "update",
        "UpdateNativeDialogShown": "update",
        "UpdateCloseDeferred": "update",
        "ExtendSelection": "extend",
        "EditSelection": "edit",
        "ResizeSelection": "resize",
        "SummarizeSelection": "summarize",
        "SimplifySelection": "simplify",
        "CorrectSelection": "correct",
        "TranslateSelection": "translate",
        "TransformToColumn": "transform",
        "GenerateFormula": "formula",
        "AnalyzeRange": "analyze",
        "OpenmiraiWebsite": "website",
        "OpenDocumentation": "docs",
        "OpenSettings": "settings",
        "AboutDialog": "about",
        "EnrollSuccess": "enroll.ok",
        "EnrollFailed": "enroll.fail",
        "BootstrapConfig": "bootstrap",
        "LlmRelayError": "llm.error",
        "ConfigWaitAtTrigger": "config.wait",
        "ActionUnhandled": "dispatch.unhandled",
    }

    # Une action non gérée signalée une fois par nom et par session : un
    # utilisateur qui reclique sur une entrée de menu morte ne doit pas
    # produire une rafale de traces.
    _unhandled_reported_cls = set()

    # Spans émis AVANT que le poste ne soit lié à un utilisateur. Les autres
    # sont jetés tant que l'identité télémétrie n'est pas "user" — ceux-ci
    # décrivent le poste, pas la personne, et sont justement ceux dont on a
    # besoin quand rien ne fonctionne encore.
    _TECHNICAL_EVENTS = {
        "ExtensionLoaded",
        "OpenSettings",
        "OpenmiraiWebsite",
        "OpenWebsite",
        "ReloadConfig",
        "ProxyCheck",
        "ProxyTest",
        "ConfigWaitAtTrigger",
        "ActionUnhandled",
        # Flux de mise à jour (issue #9) : télémétrie technique de flotte,
        # envoyée même avant la liaison utilisateur.
        "UpdateStaged",
        "UpdateAccepted",
        "UpdatePostponed",
        "UpdateInstalledPendingRestart",
        "UpdateInstallFailed",
        "UpdateNativeDialogShown",
        "UpdateCloseDeferred",
        "ExtensionUpdated",
        "NativeFeedCheck",
    }

    def _send_telemetry(self, span_name, attributes=None):
        attrs = dict(attributes or {})
        attrs.setdefault("plugin.action", self._ACTION_NAMES.get(span_name, span_name))
        attrs.setdefault("trigger.source", getattr(self, "_trigger_source", "auto"))
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
            if not hasattr(self, "_llm_error_last_sent"):
                self._llm_error_last_sent = {}
            key = str(error_code or "unknown")
            if now - self._llm_error_last_sent.get(key, 0) < self._LLM_ERROR_DEDUP_SECONDS:
                return
            self._llm_error_last_sent[key] = now
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
        """Attend une configuration en vol, et DIT combien de temps ça a duré.

        Au démarrage à froid, un déclenchement peut rester bloqué jusqu'à 15 s
        sur un fetch réseau : l'utilisateur voit une extension qui « ne fait
        rien ». Cette attente n'était mesurée nulle part — impossible de dire
        si elle touche tout le parc ou trois postes au réseau lent. Retourne
        la durée d'attente en millisecondes (0 = aucune attente).
        """
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
        """Une action déclarée mais non implémentée : le dire au parc.

        Le clic sans effet laissait un message à l'écran et une ligne dans le
        journal local — invisible pour le support. Une entrée de menu morte
        après une mise à jour ne se voyait donc que si un utilisateur pensait
        à la signaler.
        """
        try:
            if action in MainJob._unhandled_reported_cls:
                return
            MainJob._unhandled_reported_cls.add(action)
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

    def _secure_http_call(self, method, url, headers=None, body=None, timeout=10, use_proxy=True):
        request = urllib.request.Request(url, data=body, headers=_with_user_agent(headers or {}))
        request.get_method = lambda: str(method or "GET").upper()
        try:
            with self._urlopen(request, context=self.get_ssl_context(), timeout=timeout, use_proxy=use_proxy) as response:
                payload = response.read()
                status = int(getattr(response, "status", 0) or 0)
                response_headers = dict(response.headers.items()) if hasattr(response, "headers") else {}
                return status, response_headers, payload
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:
                payload = b""
            response_headers = dict(exc.headers.items()) if hasattr(exc, "headers") and exc.headers else {}
            return int(exc.code), response_headers, payload

    def _get_secure_flow(self):
        with self._secure_flow_lock:
            if self._secure_flow is not None:
                return self._secure_flow
            if self._secure_flow_init_error:
                return None
            bootstrap_url = str(self._active_bootstrap_url() or "").strip()
            if not bootstrap_url:
                return None
            plugin_uuid = self._ensure_plugin_uuid()
            device_name = str(self._get_config_from_file("device_name", "mirai-libreoffice") or "").strip() or "mirai-libreoffice"
            user_config_dir = self._get_user_config_dir()
            if not user_config_dir:
                # Pas de dossier de config exploitable -> flux sécurisé
                # indisponible (évite d'écrire l'état dans un chemin fantôme).
                return None
            state_path = os.path.join(user_config_dir, "secure_bootstrap_state.json")
            queue_path = os.path.join(user_config_dir, "telemetry_queue.json")
            try:
                flow = SecureBootstrapFlow(
                    bootstrap_base_url=bootstrap_url.rstrip("/"),
                    plugin_uuid=plugin_uuid,
                    device_name=device_name,
                    http_call=self._secure_http_call,
                    log_func=lambda m: log_to_file(m),
                    state_store=FileJsonStore(state_path),
                    queue_store=FileQueueStore(queue_path),
                    vault=default_vault(),
                    signer=Ed25519Provider(),
                )
            except Exception as exc:
                log_to_file(f"Secure flow init failed: {str(exc)}")
                self._secure_flow_init_error = str(exc)
                return None
            self._secure_flow = flow
            return flow

    def _warmup_secure_flow_async(self):
        def _worker():
            try:
                flow = self._get_secure_flow()
                if not flow:
                    return
                flow.ensure_identity()
                try:
                    flow.fetch_bootstrap_config()
                    self._send_telemetry("BootstrapConfig", {"status": "ok"})
                except Exception as exc:
                    log_to_file(f"Secure flow bootstrap fetch failed: {str(exc)}")
                    self._send_telemetry("BootstrapConfig", {"status": "error", "error": str(exc)[:120]})
            except Exception as exc:
                log_to_file(f"Secure flow warmup failed: {str(exc)}")

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _secure_send_telemetry_payload(self, payload, _span_name=None):
        flow = self._get_secure_flow()
        if not flow:
            bootstrap_url = str(self._active_bootstrap_url() or "").strip()
            if bootstrap_url:
                if not self._secure_legacy_fallback_logged:
                    self._secure_legacy_fallback_logged = True
                    log_to_file("Secure telemetry unavailable; fallback to legacy sender")
                return False
            return False
        try:
            current_kind = flow.telemetry_kind()
            access_token = str(self._get_config_from_file("access_token", "") or "").strip()
            has_valid_login = bool(access_token) and (not self._token_is_expired(access_token))
            if current_kind != "user":
                if not has_valid_login:
                    return True
                if _span_name and _span_name not in self._TECHNICAL_EVENTS:
                    return True
            handled = bool(flow.send_trace(payload))
            if flow.rebind_required():
                if access_token and not self._token_is_expired(access_token):
                    self._secure_bind_identity(access_token)
                else:
                    log_to_file("Secure telemetry requires user rebind/login")
            return handled
        except Exception as exc:
            log_to_file(f"Secure telemetry pipeline failure: {str(exc)}")
            return True

    def _secure_bind_identity(self, access_token):
        flow = self._get_secure_flow()
        if not flow:
            return ""
        try:
            return flow.bind_identity(access_token)
        except Exception as exc:
            log_to_file(f"Secure identity bind failed: {str(exc)}")
            return ""
    
    def _decode_default_key(self):
        """
        Decode the default telemetry key using base64 decoding.
        The key is stored in an obfuscated format and decoded at runtime.
        """
        # Obfuscated key - reversed string then base64 encoded
        obfuscated = "PT13WXBKWFp0UTNjbFJuT2psbWNsMUNkelZHZA=="
        try:
            # Decode the obfuscated string
            decoded = base64.b64decode(obfuscated).decode('utf-8')
            # Reverse the string to get the original key
            return decoded[::-1]
        except Exception as e:
            log_to_file(f"Error decoding telemetry key: {str(e)}")
            return ""
    
    def _get_telemetry_defaults(self):
        """Return default values for telemetry configuration."""
        return {
            "telemetryEnabled": True,
            "telemetryEndpoint": "https://traces.cpin.numerique-interieur.com/v1/traces",
            "telemetrySel": "mirai_salt",
            "telemetryAuthorizationType": "Basic",
            "telemetryKey": self._decode_default_key(),
            "telemetryHost": "",
            "telemetrylogJson": False,
            "telemetryFormatProtobuf": False
        }

    def _get_config_from_file(self, key, default, telemetry_defaults=None):
        name_file = "config.json"
        package_file = "config.default.json"
        path_settings = self.sm.createInstanceWithContext('com.sun.star.util.PathSettings', self.ctx)

        user_config_path = getattr(path_settings, "UserConfig")

        if user_config_path.startswith('file://'):
            user_config_path = str(uno.fileUrlToSystemPath(user_config_path))

        # Ensure the path ends with the filename
        config_file_path = os.path.join(user_config_path, name_file)

        user_config_data = None
        package_config_data = None

        # Load user config (if present)
        if os.path.exists(config_file_path):
            try:
                with open(config_file_path, 'r', encoding='utf-8') as file:
                    user_config_data = json.load(file)
            except (IOError, json.JSONDecodeError):
                user_config_data = None
        else:
            log_to_file(f"Config file not found in user profile: {config_file_path}")

        # Load packaged config.default.json (inside extension)
        package_config_candidates = [
            os.path.join(os.path.dirname(__file__), package_file),
            os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', package_file)),
        ]
        for package_config_path in package_config_candidates:
            if os.path.exists(package_config_path):
                try:
                    with open(package_config_path, 'r', encoding='utf-8') as file:
                        package_config_data = json.load(file)
                    break
                except (IOError, json.JSONDecodeError):
                    package_config_data = None

        # If user config missing or invalid, initialize from package defaults
        if not isinstance(user_config_data, dict) or not user_config_data:
            if isinstance(package_config_data, dict) and package_config_data:
                try:
                    with open(config_file_path, 'w', encoding='utf-8') as file:
                        json.dump(package_config_data, file, indent=4, ensure_ascii=False)
                    user_config_data = dict(package_config_data)
                    log_to_file(f"Config initialized from package defaults: {config_file_path}")
                except Exception:
                    user_config_data = None

        # Merge: packaged defaults overridden by user config
        config_data = {}
        if isinstance(package_config_data, dict):
            config_data.update(package_config_data)
        if isinstance(user_config_data, dict):
            config_data.update(user_config_data)

        # Debug: log where token is read from (masked)
        if key == "llm_api_tokens":
            try:
                raw_value = config_data.get(key, default)
                masked = ""
                if raw_value:
                    text = str(raw_value)
                    masked = text[:2] + "***" + text[-2:] if len(text) > 4 else "*" * len(text)
                log_to_file(
                    "Config read llm_api_tokens "
                    f"path={config_file_path} "
                    f"user_present={bool(user_config_data)} "
                    f"package_present={bool(package_config_data)} "
                    f"value={masked}"
                )
            except Exception:
                pass

        # Upgrade user config if package has higher configVersion
        pkg_version = None
        user_version = None
        try:
            pkg_version = int(config_data.get("configVersion")) if "configVersion" in config_data else None
        except Exception:
            pkg_version = None
        try:
            user_version = int(user_config_data.get("configVersion")) if isinstance(user_config_data, dict) and "configVersion" in user_config_data else None
        except Exception:
            user_version = None
        if pkg_version is not None and (user_version is None or user_version < pkg_version):
            try:
                merged = {}
                if isinstance(package_config_data, dict):
                    merged.update(package_config_data)
                if isinstance(user_config_data, dict):
                    merged.update(user_config_data)
                merged["configVersion"] = pkg_version
                with open(config_file_path, 'w') as file:
                    json.dump(merged, file, indent=4)
                config_data = merged
                log_to_file(f"Config upgraded to version {pkg_version}: {config_file_path}")
            except Exception:
                pass

        if not config_data:
            return default

        # Get the value from config file
        value = config_data.get(key, default)

        # If telemetry key is empty string and we have a default from telemetry_defaults, use it
        if telemetry_defaults and key == "telemetryKey" and (value == "" or value is None) and key in telemetry_defaults:
            return telemetry_defaults[key]

        return value

    def _device_management_enabled(self):
        return self._as_bool(self._get_config_from_file("enabled", False))

    def _select_settings(self, config_data):
        if not isinstance(config_data, dict):
            return None
        for candidate in ("config", "settings", "parameters", "mirai", "mirai_config", "miraiConfig"):
            value = config_data.get(candidate)
            if isinstance(value, dict):
                return value
        return None

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
            getattr(self, "_resolved_bootstrap_url", "")
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
        resolved = str(getattr(self, "_resolved_bootstrap_url", "") or "").strip()
        if resolved:
            return resolved
        persisted = str(self._get_config_from_file("last_bootstrap_url", "") or "").strip()
        if persisted:
            return persisted
        urls = self._bootstrap_urls()
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
                    log_to_file(f"DM bootstrap raw response ({mode}): {payload[:4000]}")
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
                        f"HTTP {e.code} {e.reason} body={body[:500]}"
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
        """Write key bootstrap values (LLM, telemetry) into local config file."""
        try:
            inner = config_data.get("config", {}) if isinstance(config_data, dict) else {}
            if not isinstance(inner, dict):
                return
            keys_to_sync = [
                "llm_base_urls", "llm_api_tokens", "llmTokenExpiresAt",
                "llm_default_models", "systemPrompt",
                "telemetryEndpoint", "telemetryKey",
                "telemetryAuthorizationType", "telemetrySel",
                "relayAssistantBaseUrl",
                "doc_url", "portal_url",
                "keycloak_redirect_uri", "keycloak_allowed_redirect_uri",
                "analyze_range_max_tokens", "llm_request_timeout_seconds",
                "simplify_selection_max_tokens", "simplify_selection_system_prompt",
                "extend_selection_max_tokens", "extend_selection_system_prompt",
                "edit_selection_max_new_tokens", "edit_selection_system_prompt",
                "summarize_selection_max_tokens", "summarize_selection_system_prompt",
            ]
            # Keys that are only written locally if the user has no local value yet
            user_preference_keys = {"llm_default_models"}
            # Clés dont la valeur VIDE est significative : le DM nous dit « ce
            # credential n'est plus valable ». L'ignorer laisse un llmToken
            # périmé ou révoqué sur disque, rejoué indéfiniment en 401.
            clearable_keys = {"llm_api_tokens", "llmTokenExpiresAt"}
            for key in keys_to_sync:
                if key not in inner:
                    continue
                val = inner[key]
                current = self._get_config_from_file(key, None)
                if key in user_preference_keys:
                    # Only set from DM if user has no local preference
                    if not current and val:
                        self.set_config(key, val)
                    continue
                if val == current:
                    continue
                if val == "" and key not in clearable_keys:
                    continue
                self.set_config(key, val)
                if key == "llm_api_tokens":
                    log_to_file(
                        f"[persist] llm_api_tokens synced from DM ({len(str(val))} chars)"
                        if val else
                        "[persist] llm_api_tokens vidé par le DM (aucun llmToken minté)"
                    )
            log_to_file("Bootstrap config persisted to local file")
        except Exception as e:
            log_to_file(f"Failed to persist bootstrap config: {str(e)}")

    def _config_cache_path(self):
        """Path of the on-disk cache of the full enriched config_data."""
        try:
            base = self._get_user_config_dir()
            return os.path.join(base, "config_cache.json") if base else ""
        except Exception:
            return ""

    def _persist_config_cache(self, config_data):
        """Persist the full enriched config_data + timestamp so a fresh MainJob
        instance (LO re-instantiates the job per action) can reuse it without a
        blocking network fetch."""
        path = self._config_cache_path()
        if not path or not isinstance(config_data, dict):
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "config_data": config_data}, f)
        except Exception as exc:
            log_to_file(f"config cache persist failed: {str(exc)}")

    def _hydrate_config_cache(self):
        """Load the last persisted config_data into the in-memory cache when it
        is still fresh (< config_ttl), so per-action instances don't block on a
        network fetch. The background refresh keeps it up to date."""
        if self.config_cache:
            return
        path = self._config_cache_path()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                blob = json.load(f)
            ts = float(blob.get("ts", 0))
            data = blob.get("config_data")
            age = time.time() - ts
            if isinstance(data, dict) and 0 <= age < self.config_ttl:
                self.config_cache = data
                self.config_loaded_at = ts
                log_to_file(f"config cache hydrated from disk (age {int(age)}s)")
        except Exception as exc:
            log_to_file(f"config cache hydrate failed: {str(exc)}")

    # ── Update & Feature Toggling (schema_version 2) ─────────────────

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
            pkg_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            desc_path = os.path.join(pkg_dir, "description.xml")
            if os.path.isfile(desc_path):
                import re
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

    def _is_feature_enabled(self, name, default=True):
        """Check whether a feature flag is enabled, using the cached features dict."""
        if name in self._features_cache:
            return bool(self._features_cache[name])
        return default

    def _schedule_update(self, directive):
        """Start a background daemon thread to perform the plugin update if not already running."""
        urgency = directive.get("urgency", "normal")

        target_version = str(directive.get("target_version") or "").strip()

        if target_version and target_version in MainJob._update_launch_blocked_cls:
            log_to_file(
                f"Update skipped: install of {target_version} was blocked by the "
                "workstation policy earlier — manual install required, not re-prompting"
            )
            return

        # Refus ou report mémorisé (route native ou dirigée) : ne pas reproposer
        # ni retélécharger à chaque rafraîchissement de config avant l'échéance.
        state = self._load_update_state()
        if target_version and str(state.get("target_version", "")) == target_version:
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

        # Déjà installée, en attente de redémarrage (fermeture refusée ou
        # abandonnée) : ne pas re-proposer — la réconciliation au prochain
        # démarrage rapportera « installed ».
        if target_version and str(state.get("target_version", "")).strip() == target_version \
                and state.get("stage") in ("installed_native", "installed_inprocess"):
            log_to_file(f"Update skipped: {target_version} already installed, pending restart")
            return

        with MainJob._update_lock_cls:
            if MainJob._update_in_progress_cls:
                log_to_file("Update already in progress, skipping duplicate schedule")
                return
            MainJob._update_in_progress_cls = True

        if urgency == "deferred":
            log_to_file(f"Deferred update scheduled: target={directive.get('target_version')} — download only, install on next restart")

        if urgency == "critical":
            log_to_file(f"Critical update initiated: target={directive.get('target_version')}")

        def _worker():
            try:
                # Réconcilier d'abord une mise à jour précédente déjà active (la
                # directive suivante peut arriver avant le timer de réconciliation
                # et écraserait l'état persistant). Hors du chemin de fetch config,
                # sous le verrou « en cours » : ni blocage, ni doublon.
                previous = self._load_update_state()
                previous_target = str(previous.get("target_version", "")).strip() if previous else ""
                if previous_target and previous_target != target_version:
                    try:
                        self._reconcile_update_state()
                    except Exception as exc:
                        log_to_file(f"_schedule_update: reconciliation failed: {exc}")
                self._perform_update(directive)
            finally:
                with MainJob._update_lock_cls:
                    MainJob._update_in_progress_cls = False

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
            # Route native d'abord (spec 2026-09-22) : jamais pour une directive
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
            import tempfile
            suffix = ".oxt" if "libreoffice" in full_url.lower() or full_url.endswith(".oxt") else ".oxt"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
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
            try:
                stable_dir = os.path.join(self._get_user_config_dir(), "pending_update")
                os.makedirs(stable_dir, exist_ok=True)
            except Exception:
                stable_dir = os.path.dirname(tmp_path)
            stable_oxt = os.path.join(stable_dir, "mirai_update.oxt")
            import shutil
            shutil.copy2(tmp_path, stable_oxt)
            self._pending_install_oxt = stable_oxt
            self._pending_install_script = ""
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

            # Script d'installation de secours (.bat/.sh) : spawne un processus
            # enfant → refusé sur postes durcis (WinError 5, AppLocker / Defender
            # ASR), et son cycle unopkg remove/add est un vecteur de corruption du
            # registre (issue #9). DÉSACTIVÉ par défaut ; réactivable explicitement
            # via MIRAI_UPDATE_ALLOW_SCRIPT=1 (postes non durcis, diagnostic).
            if os.environ.get("MIRAI_UPDATE_ALLOW_SCRIPT") == "1":
                try:
                    import platform
                    sys_name = platform.system()  # Darwin, Windows, Linux

                    # Find unopkg
                    unopkg = None
                    if sys_name == "Darwin":
                        for candidate in [
                            "/Applications/LibreOffice.app/Contents/MacOS/unopkg",
                            os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/unopkg"),
                        ]:
                            if os.path.isfile(candidate):
                                unopkg = candidate
                                break
                    elif sys_name == "Windows":
                        for candidate in [
                            os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "LibreOffice", "program", "unopkg.com"),
                            os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "LibreOffice", "program", "unopkg.com"),
                        ]:
                            if os.path.isfile(candidate):
                                unopkg = candidate
                                break
                    else:  # Linux
                        for candidate in ["/usr/bin/unopkg", "/usr/lib/libreoffice/program/unopkg"]:
                            if os.path.isfile(candidate):
                                unopkg = candidate
                                break
                    if not unopkg:
                        # Generic fallback: look relative to extension install
                        fallback = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                            os.path.dirname(os.path.abspath(__file__))))), "program", "unopkg")
                        if os.path.isfile(fallback):
                            unopkg = fallback
                    if not unopkg:
                        raise FileNotFoundError("unopkg not found")
                    log_to_file(f"_perform_update: unopkg={unopkg} platform={sys_name}")

                    # Stage the update: quit LO → wait → remove old → install new → relaunch
                    log_path = os.path.expanduser("~/log.txt")
                    _ts = 'date "+%Y-%m-%d %H:%M:%S"'

                    if sys_name == "Windows":
                        soffice_path = os.path.join(os.path.dirname(unopkg), "soffice.exe")
                        install_script = os.path.join(stable_dir, "mirai_update.bat")
                        # Windows: use %TIME% for timestamp
                        with open(install_script, "w") as sf:
                            sf.write("@echo off\r\n")
                            sf.write(f'echo %DATE% %TIME% - [UPDATE] script started >> "{log_path}"\r\n')
                            sf.write(":wait_lo\r\n")
                            sf.write("tasklist /FI \"IMAGENAME eq soffice.bin\" 2>nul | find /I \"soffice.bin\" >nul\r\n")
                            sf.write("if not errorlevel 1 (\r\n")
                            sf.write("  timeout /t 2 /nobreak >nul\r\n")
                            sf.write("  goto wait_lo\r\n")
                            sf.write(")\r\n")
                            sf.write(f'echo %DATE% %TIME% - [UPDATE] LO quit detected >> "{log_path}"\r\n')
                            sf.write(f'"{unopkg}" remove fr.gouv.interieur.mirai 2>nul\r\n')
                            sf.write(f'echo %DATE% %TIME% - [UPDATE] old extension removed >> "{log_path}"\r\n')
                            sf.write(f'"{unopkg}" add --force --suppress-license "{stable_oxt}"\r\n')
                            sf.write(f'if errorlevel 1 (\r\n')
                            sf.write(f'  echo %DATE% %TIME% - [UPDATE] unopkg add FAILED >> "{log_path}"\r\n')
                            sf.write(f') else (\r\n')
                            sf.write(f'  echo %DATE% %TIME% - [UPDATE] extension installed OK >> "{log_path}"\r\n')
                            sf.write(f')\r\n')
                            sf.write(f'echo %DATE% %TIME% - [UPDATE] launching LibreOffice >> "{log_path}"\r\n')
                            sf.write(f'start "" "{soffice_path}"\r\n')
                            sf.write(f'del "{stable_oxt}" 2>nul\r\n')
                            sf.write(f'del "{install_script}" 2>nul\r\n')
                        self._pending_install_script = install_script
                    else:
                        # macOS / Linux
                        # Capture current document path to reopen after update
                        _doc_path = ""
                        try:
                            _desktop = self.ctx.getServiceManager().createInstanceWithContext(
                                "com.sun.star.frame.Desktop", self.ctx
                            )
                            _doc = _desktop.getCurrentComponent() if _desktop else None
                            if _doc and hasattr(_doc, "getURL") and _doc.getURL():
                                from urllib.parse import unquote, urlparse
                                _parsed = urlparse(_doc.getURL())
                                if _parsed.scheme == "file":
                                    _doc_path = unquote(_parsed.path)
                        except Exception:
                            pass
                        soffice_bin = os.path.join(os.path.dirname(unopkg), "soffice")
                        if sys_name == "Darwin":
                            if _doc_path:
                                relaunch_cmd = f'"{soffice_bin}" "{_doc_path}" &'
                            else:
                                relaunch_cmd = f'"{soffice_bin}" --writer &'
                            wait_cmd = "while pgrep -x soffice >/dev/null 2>&1 || pgrep -x oosplash >/dev/null 2>&1; do sleep 1; done"
                        else:
                            relaunch_cmd = f'"{soffice_bin}" &' if os.path.isfile(soffice_bin) else "libreoffice &"
                            if _doc_path:
                                relaunch_cmd = f'"{soffice_bin}" "{_doc_path}" &' if os.path.isfile(soffice_bin) else f'libreoffice "{_doc_path}" &'
                            wait_cmd = "while pgrep -x soffice >/dev/null 2>&1; do sleep 1; done"
                        install_script = os.path.join(stable_dir, "mirai_update.sh")
                        with open(install_script, "w") as sf:
                            sf.write("#!/bin/bash\n")
                            sf.write(f'LOG="{log_path}"\n')
                            sf.write(f'echo "$({_ts}) - [UPDATE] script started" >> "$LOG"\n')
                            sf.write(f'{wait_cmd}\n')
                            sf.write(f'echo "$({_ts}) - [UPDATE] LO quit detected" >> "$LOG"\n')
                            sf.write("sleep 5\n")
                            sf.write(f'"{unopkg}" remove fr.gouv.interieur.mirai 2>/dev/null || true\n')
                            sf.write(f'echo "$({_ts}) - [UPDATE] old extension removed" >> "$LOG"\n')
                            sf.write(f'"{unopkg}" add --force --suppress-license "{stable_oxt}"\n')
                            sf.write(f'RC=$?\n')
                            sf.write(f'if [ "$RC" -eq 0 ]; then\n')
                            sf.write(f'  echo "$({_ts}) - [UPDATE] extension installed OK" >> "$LOG"\n')
                            sf.write(f'else\n')
                            sf.write(f'  echo "$({_ts}) - [UPDATE] unopkg add FAILED rc=$RC" >> "$LOG"\n')
                            sf.write(f'fi\n')
                            sf.write("sync\n")
                            sf.write("sleep 3\n")
                            sf.write(f'echo "$({_ts}) - [UPDATE] launching LibreOffice" >> "$LOG"\n')
                            sf.write(f'{relaunch_cmd}\n')
                            sf.write(f'rm -f "{stable_oxt}" "{install_script}"\n')
                        os.chmod(install_script, 0o755)
                        self._pending_install_script = install_script
                    log_to_file("_perform_update: fallback install script staged (opt-in)")
                except Exception as pkg_err:
                    log_to_file(f"_perform_update: script staging failed (non-fatal): {pkg_err}")

            log_to_file(f"_perform_update: install staged for version={target_version}")
            # « deferred » = artefact prêt, installation à suivre (acceptation
            # utilisateur + redémarrage). « installed » n'est rapporté qu'une fois
            # la nouvelle version réellement active (_reconcile_update_state au
            # démarrage suivant) — l'ancien rapport « installed » dès le staging
            # comptait comme réussies des installations jamais abouties.
            self._report_update_status(campaign_id, "deferred", version_before, target_version)

            self._wait_before_prompting()
            log_to_file("_perform_update: showing update dialog to user")

            # Ask user BEFORE launching the install script
            user_wants_restart = False
            try:
                desktop = self.ctx.getServiceManager().createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx
                )
                active_frame = desktop.getCurrentFrame() if desktop else None
                if active_frame:
                    toolkit = self.ctx.getServiceManager().createInstance("com.sun.star.awt.Toolkit")
                    parent = active_frame.getContainerWindow()
                    msg_text = (
                        f"MIrAI {target_version} est prêt.\n\n"
                        "Pour l'installer, LibreOffice va se fermer —\n"
                        "vous le rouvrirez ensuite pour l'activer.\n\n"
                        "Installer et fermer maintenant ?\n\n"
                        "(Si vous choisissez Non, la mise à jour sera\n"
                        "reproposée plus tard. Vous pouvez aussi la\n"
                        "lancer depuis le menu MIrAI → À propos…)"
                    )
                    if urgency == "critical":
                        msg_text = (
                            f"Une nouvelle version de MIrAI ({target_version})\n"
                            "avec des améliorations importantes est prête.\n\n"
                            "Pour l'installer, LibreOffice va se fermer —\n"
                            "rouvrez-le ensuite pour l'activer.\n\n"
                            "Installer et fermer maintenant ?\n\n"
                            "(Si vous choisissez Non, la mise à jour sera\n"
                            "reproposée plus tard.)"
                        )
                    msgbox = toolkit.createMessageBox(
                        parent,
                        4,  # MessageBoxType.QUERYBOX
                        MSG_BUTTONS.BUTTONS_YES_NO,
                        "MIrAI — Mise à jour",
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
                # automatique par défaut — aucun processus enfant (WinError 5-immune).
                if self._install_and_restart_in_process(getattr(self, "_pending_install_oxt", "")):
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
                # Script de secours : uniquement si explicitement réactivé
                # (MIRAI_UPDATE_ALLOW_SCRIPT=1). Sinon, dégradation directe vers
                # le message manuel validé GPO (bouton « Ouvrir le dossier »).
                install_script = getattr(self, "_pending_install_script", "")
                if install_script and os.path.isfile(install_script):
                    log_to_file("_perform_update: in-process install failed, using opt-in install script")
                    try:
                        import subprocess
                        import platform as _pf
                        if _pf.system() == "Windows":
                            try:
                                subprocess.Popen(["cmd", "/c", "start", "/min", "", install_script], close_fds=True)
                            except Exception:
                                subprocess.Popen(["cmd", "/c", install_script])
                        else:
                            subprocess.Popen(["bash", install_script], start_new_session=True)
                        # terminate() must run on the main thread to avoid
                        # macOS autolayout crashes — schedule it via UNO timer
                        self._terminate_on_main_thread()
                        return
                    except Exception as launch_err:
                        # WinError 5 (AppLocker / Defender ASR "block child
                        # process") atterrit ici — on continue vers le manuel.
                        log_to_file(f"_perform_update: failed to launch install script: {launch_err}")
                # L'install auto n'a pas abouti : anti-boucle (ne pas re-prompter
                # ce target), rapport DM, puis message manuel (voie validée).
                if target_version:
                    MainJob._update_launch_blocked_cls.add(target_version)
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
                self._notify_update_blocked(target_version, install_script)
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

    def _restart_libreoffice(self):
        """Quit LibreOffice and relaunch it."""
        try:
            desktop = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            if desktop:
                # Schedule relaunch before quitting
                import subprocess
                soffice = None
                for candidate in [
                    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                    os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice"),
                ]:
                    if os.path.isfile(candidate):
                        soffice = candidate
                        break
                if soffice:
                    # Detached process that waits 2s then launches LO
                    subprocess.Popen(
                        ["bash", "-c", f"sleep 2 && open -a LibreOffice"],
                        start_new_session=True,
                    )
                self._terminate_on_main_thread()
        except Exception as e:
            log_to_file(f"_restart_libreoffice error: {e}")

    def _notify_update_blocked(self, target_version, install_script):
        """Inform the user once that the automatic update could not be launched.

        On a locked-down workstation an AppLocker / Defender-ASR policy can deny
        spawning the install script (WinError 5). We stop the re-prompt loop and
        point to the ready-to-install package so an admin can finish manually.
        Uses the same message-box path as the update prompt (known to work from
        this worker thread).
        """
        try:
            oxt = ""
            try:
                base = os.path.dirname(install_script or "")
                if base:
                    oxt = os.path.join(base, "mirai_update.oxt")
            except Exception:
                oxt = ""
            # Dossier à proposer à l'ouverture : celui du .oxt téléchargé, sinon
            # le dossier pending_update du profil. Sert au bouton « Ouvrir le
            # dossier » (ouverture native, sans cmd.exe — cf. _open_folder_native).
            folder = ""
            try:
                if oxt and os.path.isfile(oxt):
                    folder = os.path.dirname(oxt)
                else:
                    cand = os.path.join(self._get_user_config_dir(), "pending_update")
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
            oxt_line = oxt or "le dossier pending_update de votre profil LibreOffice"
            msg = (
                f"La mise à jour MIrAI {target_version} a été téléchargée et\n"
                "vérifiée, mais son installation automatique a été bloquée par\n"
                "la politique de sécurité de ce poste.\n\n"
                "Elle ne sera plus reproposée automatiquement — vous pouvez\n"
                "l'installer vous-même :\n\n"
                "── Installation manuelle ─────────────────────────\n"
                "1. Menu  Outils ▸ Gestionnaire des extensions…\n"
                "2. Si « MIrAI » est déjà dans la liste : sélectionnez-le,\n"
                "   puis cliquez sur « Supprimer ».\n"
                "3. Cliquez sur « Ajouter » et sélectionnez le fichier :\n"
                f"      {oxt_line}\n"
                "4. Acceptez la licence.\n"
                "5. Fermez puis rouvrez LibreOffice.\n\n"
                "(La suppression/ajout se fait dans LibreOffice — pas besoin\n"
                "de droits administrateur.)\n"
                "En cas d'échec, contactez votre support / administrateur."
            )
            # Quand on connaît le dossier du fichier téléchargé, on propose de
            # l'ouvrir directement (Oui = ouvrir l'explorateur, sans cmd.exe).
            open_folder_offered = bool(folder)
            if open_folder_offered:
                msg = msg + (
                    "\n\n──────────────────────────────────────────────\n"
                    "► Pour ouvrir le dossier contenant le fichier téléchargé,\n"
                    "  cliquez sur « Oui » : l'explorateur de fichiers s'ouvre\n"
                    "  directement (sans invite de commande). « Non » referme\n"
                    "  simplement ce message."
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
                "MIrAI — Mise à jour bloquée",
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

    def _install_oxt_inprocess(self, oxt_url, props, abort, cmd_env):
        """Install an OXT for the current user, in-process (no child process).

        Runs from the update WORKER thread, where pyuno's `from com.sun.star…
        import …` hook is NOT available ("No module named 'com'") — confirmed in
        the field. So we must **not** import here. Order of attempts:

          1. **thePackageManagerFactory** obtained via `ctx.getValueByName` — a plain
             UNO method call, **no import** → works off the main thread. Its
             `getPackageManager("user").addPackage(...)` deploys the OXT. (Historique :
             la sonde terrain n'obtenait jamais le nom inexistant theExtensionManager ;
             avec le vrai singleton ExtensionManager, ce repli n'a plus de raison
             d'être et ne sert qu'en dernier recours.)
          2. The **ExtensionManager singleton pre-bound on the MAIN thread** at module
             load (`_EXT_MGR_SINGLETON`) → `addExtension`, as a fallback.

        Returns True on success. Any install exception (e.g. a policy denial) is
        propagated so the caller can log it and fall back; returns False only when
        **no** deployment API is reachable.
        """
        props = props or ()
        # 1) PackageManagerFactory — import-free, worker-thread safe.
        factory = None
        try:
            factory = self.ctx.getValueByName(
                "/singletons/com.sun.star.deployment.thePackageManagerFactory"
            )
        except Exception as exc:
            log_to_file(f"_install_oxt_inprocess: PackageManagerFactory lookup failed: {exc}")
        if factory is not None:
            pkg_mgr = factory.getPackageManager("user")
            # Remove-before-add: drop any existing registration of this identifier
            # first. Re-installing over an ACTIVE extension can otherwise leave a
            # stale duplicate component ("Insert duplicate implementation name
            # fr.gouv.interieur.mirai.PromptFunction") that blocks activation.
            try:
                pkg_mgr.removePackage(_EXTENSION_IDENTIFIER, "", abort, cmd_env)
                log_to_file("_install_oxt_inprocess: removed prior package before add")
            except Exception as rm_exc:
                log_to_file(f"_install_oxt_inprocess: removePackage (ignored): {rm_exc}")
            pkg_mgr.addPackage(oxt_url, props, "", abort, cmd_env)
            log_to_file("_install_oxt_inprocess: installed via thePackageManagerFactory")
            return True
        # 2) ExtensionManager singleton pre-bound on the main thread (see module top).
        if _EXT_MGR_SINGLETON is not None:
            mgr = None
            try:
                mgr = _EXT_MGR_SINGLETON.get(self.ctx)
            except Exception as exc:
                log_to_file(f"_install_oxt_inprocess: ExtensionManager.get failed: {exc}")
            if mgr is not None:
                # Pas de remove-avant-add : addExtension remplace atomiquement une
                # extension de même identifiant (VersionException approuvée par le
                # handler silencieux), comme sur le chemin main-thread.
                mgr.addExtension(oxt_url, props, "user", abort, cmd_env)
                log_to_file("_install_oxt_inprocess: installed via ExtensionManager singleton")
                return True
        log_to_file("_install_oxt_inprocess: no in-process deployment API available")
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
            return False, "main-thread callback unavailable"
        holder = {"ok": False, "err": "", "cancelled": False, "started": False, "action_s": 0.0}
        done = threading.Event()

        def _run():
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
                return False, "AsyncCallback unavailable"
            async_cb.addCallback(_MainThreadCallback(_run), None)
        except Exception as exc:
            log_to_file(f"{label}: schedule failed: {exc}")
            self._last_main_thread_action_s = float(holder.get("action_s") or 0.0)
            return False, f"schedule failed: {exc}"
        if not done.wait(timeout):
            holder["cancelled"] = True
            if holder["started"]:
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
        répétés depuis le thread worker sont ce qui corrompait le registre
        (issue #9). Pas de remove-avant-add : addExtension remplace atomiquement
        une extension de même identifiant (VersionException approuvée par le
        handler silencieux). Aucun processus enfant (immunisé WinError 5).

        Retourne True sur succès confirmé ; False sur échec ou timeout (l'appelant
        dégrade). Après un timeout, le callback éventuel devient no-op (garde
        `cancelled`) pour interdire une double installation concurrente.
        """
        ctx = self.ctx

        def _install():
            mgr = None
            try:
                mgr = ctx.getValueByName(
                    "/singletons/com.sun.star.deployment.ExtensionManager")
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

    def _install_and_restart_in_process(self, oxt_path):
        """Install the update via LibreOffice's own deployment API — entirely
        inside the soffice process — then close LibreOffice cleanly.

        This is the key path for locked-down postes: it spawns **no** child
        process (no cmd.exe / soffice.exe), so it is not affected by the
        AppLocker / Defender-ASR policy that denies the install script (WinError
        5). Order of attempts:

          1. ExtensionManager.addExtension sur le MAIN thread (voie du
             Gestionnaire des extensions — remplace proprement, pas de
             corruption du registre) ;
          2. legacy : thePackageManagerFactory depuis le worker
             (_install_oxt_inprocess) — dernier recours seulement.

        Returns True on success; any failure returns False so the caller falls
        back to the manual-install message (ou au script si explicitement
        réactivé). UNO usage is lazy so the module imports under test stubs.
        """
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
                if getattr(self, "_main_thread_install_in_flight", False):
                    # addExtension tourne encore sur le thread principal : surtout
                    # pas de second flux (double install = corruption du registre).
                    # Repli manuel ; la réconciliation au prochain démarrage
                    # rapportera « installed » si l'install a abouti.
                    log_to_file("_perform_update: main-thread install still running, not starting a second one")
                    return False
                log_to_file("_perform_update: main-thread install unavailable, trying legacy worker path")
                if not self._install_oxt_inprocess(oxt_url, props, None, cmd_env):
                    log_to_file("_perform_update: in-process deployment API unavailable")
                    return False
            log_to_file("_perform_update: in-process install succeeded")

            # Close LibreOffice cleanly so the user reopens it with the new version
            # active. We deliberately do NOT re-exec.
            if not self._close_after_inprocess_update():
                self._send_telemetry("UpdateCloseDeferred", {"route": "directed"})
            return True
        except Exception as exc:
            log_to_file(f"_perform_update: in-process install failed, falling back: {exc}")
            return False

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
        arriver est traité comme un refus humain (« Enregistrer ? » → Annuler) et
        respecté sans nouvel essai. SIGTERM n'intervient que si le thread principal
        est injoignable (callback/AsyncCallback indisponible) — jamais pour un simple
        veto ou une occupation temporaire.

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
                    parent, 1, MSG_BUTTONS.BUTTONS_OK, "MIrAI — Mise à jour",
                    "La mise à jour a été installée.\n\n"
                    "LibreOffice va se fermer pour l'activer.\n"
                    "Rouvrez-le ensuite."
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
            ok, err = self._run_on_main_thread(_terminate, 10, "_close_after_inprocess_update")
            action_s = getattr(self, "_last_main_thread_action_s", 0.0)
            if ok or err == _MAIN_THREAD_TIMEOUT_AFTER_START:
                log_to_file("_close_after_inprocess_update: terminate accepted on main thread")
                return True
            if err == _MAIN_THREAD_VETOED and action_s >= _CLOSE_USER_REFUSAL_SECONDS:
                # Un veto après une action longue = réponse humaine (dialogue
                # « Enregistrer ? ») ; la latence de planification sur le thread
                # principal ne compte pas.
                log_to_file(f"_close_after_inprocess_update: fermeture refusée par l'utilisateur ({action_s:.1f} s dans terminate()), abandon")
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
                return False
            # Le thread principal est injoignable (callback/AsyncCallback indisponible,
            # planification impossible) : seul cas où SIGTERM reste justifié.
            log_to_file(f"_close_after_inprocess_update: main thread unreachable ({err}), SIGTERM fallback")
            self._terminate_on_main_thread()
            return False

    def _terminate_on_main_thread(self):
        """Quit LibreOffice without calling desktop.terminate() from a background thread.

        desktop.terminate() from a non-main thread corrupts the macOS
        autolayout engine (NSISEngine assertion).  Instead, we send SIGTERM
        to the soffice process — macOS delivers the signal on the main thread,
        which triggers a clean shutdown identical to Cmd+Q.
        On Windows we fall back to desktop.terminate() (no autolayout issue).
        """
        import platform as _pf
        if _pf.system() in ("Darwin", "Linux"):
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

    # ── Update state & post-restart reconciliation (issue #9) ────────────
    # Le résultat réel d'une mise à jour n'est connu qu'au redémarrage suivant
    # (l'installation remplace l'extension qui exécute ce code). On persiste
    # donc l'état de campagne à côté de l'artefact stagé, et au démarrage on
    # compare la version active au target : rapport « installed » véridique,
    # purge idempotente de pending_update, levée de l'anti-boucle.

    def _update_state_path(self):
        base = self._get_user_config_dir()
        if not base:
            return ""
        return os.path.join(base, "pending_update", "update_state.json")

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
        if not path:
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
        base = self._get_user_config_dir()
        if not base:
            return
        folder = os.path.join(base, "pending_update")
        try:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
            log_to_file("_purge_pending_update_dir: pending_update purged")
        except Exception as exc:
            log_to_file(f"_purge_pending_update_dir: {exc}")

    def _reconcile_update_state(self):
        """Au démarrage : clôt la mise à jour précédente de façon idempotente.

        - version active == target → rapport « installed » au DM + télémétrie,
          purge de pending_update (OXT stagé, scripts, état), retrait du target
          de l'anti-boucle _update_launch_blocked_cls ;
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
            MainJob._update_launch_blocked_cls.discard(target)
            self._purge_pending_update_dir()
        else:
            age = time.time() - float(state.get("ts", 0) or 0)
            if age > 14 * 24 * 3600:
                log_to_file("_reconcile_update_state: stale update state, purging")
                self._purge_pending_update_dir()

    def _schedule_update_reconciliation(self):
        """Lance la réconciliation en fond, une fois par process (réseau possible)."""
        if MainJob._update_reconcile_started_cls:
            return
        MainJob._update_reconcile_started_cls = True

        def _safe_reconcile():
            try:
                self._reconcile_update_state()
            except Exception as exc:
                log_to_file(f"_reconcile_update_state: {exc}")

        timer = threading.Timer(5.0, _safe_reconcile)
        timer.daemon = True
        timer.start()

    # ── Route native pilotée (spec 2026-09-22, issue #9) ─────────────────
    # Le DM décide (directive update), LibreOffice installe (dialogue « Mise à
    # jour des extensions »). Le plugin ne télécharge rien : il vérifie que le
    # feed <update-information> de l'extension installée annonce exactement la
    # cible, ouvre le dialogue natif sur le thread principal, puis surveille la
    # version installée et ferme LibreOffice proprement.

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
            with MainJob._enrollment_wizard_lock_cls:
                if not MainJob._enrollment_wizard_active_cls:
                    return
            time.sleep(1)

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
        (<profil>/user/uno_packages/cache/uno_packages), déduit de l'emplacement
        de ce module : <cache>/<lu…>/<paquet>.oxt/src/mirai/entrypoint.py."""
        here = os.path.abspath(__file__)
        for _ in range(5):
            here = os.path.dirname(here)
        return here

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
            import re
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

    def _perform_native_update(self, directive, wait_seconds=None, poll_seconds=None):
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
        wait_seconds = _NATIVE_INSTALL_WAIT_SECONDS if wait_seconds is None else wait_seconds
        poll_seconds = _NATIVE_POLL_SECONDS if poll_seconds is None else poll_seconds
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

        deadline = time.time() + wait_seconds
        last_seen = None
        polls = 0
        hits = 0
        while time.time() < deadline:
            time.sleep(poll_seconds)
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
            # première lecture), ou lecture anormalement lente (>1 s).
            if seen != last_seen or probe_ms > 1000:
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

    # ── Diagnostic passif du feed natif (<update-information>, issue #5) ──
    # LibreOffice récupère le feed avec SA pile HTTP (proxy/TLS/GPO propres),
    # pas celle du plugin. Ce check headless valide donc, sans aucune action
    # utilisateur et à l'échelle de la flotte, que la route native est viable
    # sur les postes durcis — et détecte un feed DM absent ou mal formé
    # (device-management#23) AVANT d'appuyer le déploiement large dessus.

    def _update_feed_urls(self):
        """URLs du feed natif, dérivées des bootstrap configurés (failover d'abord).
        Même convention que le bake au build (scripts/inject_update_feed.py)."""
        return [
            base.rstrip("/") + _UPDATE_FEED_PATH
            for base in (self._failover_ordered_urls() or [])
            if isinstance(base, str) and base.strip()
        ]

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
        if MainJob._feed_check_started_cls:
            return
        MainJob._feed_check_started_cls = True

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

        import json
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
        settings = self._select_settings(config_data)
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
            return self._get_config_from_file("llm_base_urls", default, telemetry_defaults=telemetry_defaults)

        if key == "llm_api_tokens":
            return self._resolve_llm_token(default, telemetry_defaults=telemetry_defaults)

        if key == "llm_default_models":
            local_model = str(self._get_config_from_file("llm_default_models", "", telemetry_defaults=telemetry_defaults)).strip()
            config_model = self._get_setting("llm_default_models")
            config_model = str(config_model).strip() if config_model is not None else ""
            if config_model and len(config_model) < 6:
                config_model = ""

            endpoint = self.get_config("llm_base_urls", "http://127.0.0.1:5000")
            api_key = self.get_config("llm_api_tokens", "")
            is_openwebui = True
            if not is_openwebui:
                endpoint_lower = str(endpoint).lower()
                if "/api" in endpoint_lower and "/v1" not in endpoint_lower:
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
        if config_value is not None:
            return config_value

        return self._get_config_from_file(key, default, telemetry_defaults=telemetry_defaults)

    def set_config(self, key, value):
        name_file = "config.json"

        path_settings = self.sm.createInstanceWithContext('com.sun.star.util.PathSettings', self.ctx)
        user_config_path = getattr(path_settings, "UserConfig")

        if user_config_path.startswith('file://'):
            user_config_path = str(uno.fileUrlToSystemPath(user_config_path))

        config_file_path = os.path.join(user_config_path, name_file)

        with self._config_write_lock:
            if os.path.exists(config_file_path):
                try:
                    with open(config_file_path, 'r', encoding='utf-8') as file:
                        config_data = json.load(file)
                except (IOError, json.JSONDecodeError):
                    config_data = {}
            else:
                config_data = {}

            config_data[key] = value
            if key == "llm_default_models":
                log_to_file(f"Model saved (local): {value}")

            # Écriture ATOMIQUE : fichier temporaire puis remplacement.
            # Écrire en place expose tout lecteur concurrent — un autre thread
            # du plugin, une seconde instance de LibreOffice — à un JSON
            # tronqué. Le lecteur repart alors sur les valeurs par défaut et
            # PERD les credentials : c'est une façon d'entrer dans l'état
            # absorbant (enrôlé sans paire relais) sans que personne ne l'ait
            # demandé. os.replace est atomique sur POSIX comme sur Windows.
            temporary_path = f"{config_file_path}.tmp"
            try:
                with open(temporary_path, 'w', encoding='utf-8') as file:
                    json.dump(config_data, file, indent=4, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary_path, config_file_path)
            except OSError as e:
                log_to_file(f"Error writing to {config_file_path}: {e}")
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass

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

    # ── Thinking widget (floating indicator while LLM works) ───────────
    _thinking_widget = None
    _thinking_container = None
    _thinking_dots_count = 0

    def _show_thinking(self):
        """Show a small floating window with the plume icon
        and an animated 'MIrAI réfléchit...' label.
        Minimal chrome: no close button, not sizeable, empty title."""
        try:
            self._close_thinking()
            from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
            ctx = uno.getComponentContext()
            sm = ctx.getServiceManager()
            def _cr(n):
                return sm.createInstanceWithContext(n, ctx)

            W, H = 180, 64
            dlg = _cr("com.sun.star.awt.UnoControlDialog")
            dlg_m = _cr("com.sun.star.awt.UnoControlDialogModel")
            dlg.setModel(dlg_m)
            dlg.setVisible(False)
            dlg.setTitle("")
            dlg.setPosSize(0, 0, W, H, SIZE)
            try:
                dlg_m.BackgroundColor = 0xFFFFFF
                dlg_m.Closeable = False
                dlg_m.Sizeable = False
                dlg_m.Moveable = True
            except Exception:
                pass

            def _add(name, ctrl_type, x, y, w, h, props):
                m = dlg_m.createInstance(
                    "com.sun.star.awt.UnoControl" + ctrl_type + "Model")
                dlg_m.insertByName(name, m)
                c = dlg.getControl(name)
                c.setPosSize(x, y, w, h, POSSIZE)
                for k, v in props.items():
                    try:
                        setattr(m, k, v)
                    except Exception:
                        pass
                return c

            # Icon
            plume_path = os.path.join(
                os.path.dirname(__file__), "icons", "plume.png")
            plume_url = ""
            if os.path.exists(plume_path):
                plume_url = uno.systemPathToFileUrl(plume_path)
            _add("img_plume", "ImageControl", 6, 6, 48, 48, {
                "ImageURL": plume_url,
                "BackgroundColor": 0xFFFFFF,
                "Border": 0,
                "ScaleImage": True,
            })

            # Label "MIrAI"
            from com.sun.star.awt.FontWeight import BOLD
            _add("lbl_thinking", "FixedText", 60, 10, W - 68, 20, {
                "Label": "MIrAI",
                "FontHeight": 11,
                "FontWeight": BOLD,
                "TextColor": _UI["primary"],
                "BackgroundColor": 0xFFFFFF,
            })

            # Sub-label "réfléchit..."
            from com.sun.star.awt.FontSlant import ITALIC
            _add("lbl_dots", "FixedText", 60, 32, W - 68, 18, {
                "Label": "réfléchit...",
                "FontHeight": 9,
                "FontSlant": ITALIC,
                "TextColor": _UI["text_secondary"],
                "BackgroundColor": 0xFFFFFF,
            })

            # Position: centered horizontally, 2/3 down the document window
            frame = _cr("com.sun.star.frame.Desktop").getCurrentFrame()
            window = frame.getContainerWindow() if frame else None
            toolkit = _cr("com.sun.star.awt.Toolkit")
            dlg.createPeer(toolkit, window)
            if window:
                ps = window.getPosSize()
                _x = ps.X + (ps.Width - W) // 2
                _y = ps.Y + int(ps.Height * 2 / 3) - H // 2
                dlg.setPosSize(_x, _y, 0, 0, POS)
            dlg.setVisible(True)
            self._thinking_widget = dlg
            self._thinking_container = dlg
            self._thinking_dots_count = 0

            pump_events(toolkit)
        except Exception:
            pass

    def _update_thinking_dots(self):
        """Animate the dots on the thinking widget (call from main loop)."""
        if not self._thinking_container:
            return
        try:
            self._thinking_dots_count = (self._thinking_dots_count + 1) % 4
            dots = "." * (self._thinking_dots_count + 1)
            lbl = self._thinking_container.getControl("lbl_dots")
            if lbl:
                lbl.getModel().Label = f"réfléchit{dots}"
        except Exception:
            pass

    def _close_thinking(self):
        """Close the thinking widget if open."""
        try:
            if self._thinking_container:
                self._thinking_container.dispose()
        except Exception:
            pass
        try:
            if self._thinking_widget:
                self._thinking_widget.setVisible(False)
                self._thinking_widget.dispose()
        except Exception:
            pass
        self._thinking_widget = None
        self._thinking_container = None

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
                    "title": "Bienvenue dans IA'ssistant by MIrAI",
                    "text": (
                        "Votre assistant IA pour LibreOffice est presque prêt !\n\n"
                        "IA'ssistant vous aide à rédiger, reformuler, résumer\n"
                        "et enrichir vos documents en toute simplicité.\n\n"
                        "Pour activer les fonctionnalités IA, une courte\n"
                        "procédure d'enrôlement sécurisé est nécessaire.\n\n"
                        "Cela ne prend que quelques secondes."
                    ),
                    "btn_next": "Commencer",
                    "btn_cancel": "Plus tard",
                    "step_label": "Étape 1/5 — Présentation",
                },
                {
                    "title": "Connexion sécurisée",
                    "text": (
                        "Cliquez sur « Ouvrir le navigateur » pour vous connecter.\n\n"
                        "  • Votre navigateur s'ouvrira sur la page de connexion MIrAI\n"
                        "  • Après connexion, revenez dans LibreOffice\n"
                        "  • L'enrôlement se fera automatiquement\n\n"
                        "Vos données restent protégées :\n"
                        "aucun mot de passe n'est stocké par le plugin."
                    ),
                    "btn_next": "Ouvrir le navigateur",
                    "btn_cancel": "Annuler",
                    "step_label": "Étape 2/5 — Authentification",
                },
            ]

            result = {"proceed": False, "step": 0, "cancelled": False}

            dialog = create("com.sun.star.awt.UnoControlDialog", ctx)
            dialog_model = create("com.sun.star.awt.UnoControlDialogModel", ctx)
            dialog.setModel(dialog_model)
            dialog.setVisible(False)
            dialog.setTitle("IA'ssistant by MIrAI")
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
            if not os.path.exists(logo_path):
                logo_path = os.path.join(os.path.dirname(__file__), "icons", "iassistant.png")
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
                "Connexion MIrAI requise",
                "Vous allez être redirigé vers la page de connexion MIrAI.\n\n"
                "Voulez-vous continuer ?"
            )
            return proceed, None, None, None, None

    def _show_message_and_open_settings(self, title, message):
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
            if result == 1:
                try:
                    self.settings_box("Settings")
                except Exception:
                    pass
        except Exception as e:
            log_to_file(f"Failed to show message box: {str(e)}")

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

        settings = self._select_settings(config_data)
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

        # ── Auth endpoint: always from keycloakIssuerUrl + realm ──────────
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

        # ── Token endpoint: prefer explicit (may point to relay) ──────────
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
        expires_in = token_response.get("expires_in")
        if isinstance(expires_in, (int, float)):
            self.set_config("access_token_expires_at", int(time.time() + int(expires_in)))

    def _clear_tokens(self):
        try:
            self.set_config("access_token", "")
            self.set_config("refresh_token", "")
            self.set_config("access_token_expires_at", 0)
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

    def _wait_for_auth_code(self, redirect_uri, timeout_seconds=120, tick=None, cancel_event=None):
        try:
            parsed = urllib.parse.urlparse(redirect_uri)
            if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
                return None, "redirect_uri_invalid"
            host = parsed.hostname
            port = parsed.port or 80
            path = parsed.path or "/"
        except Exception:
            return None, "redirect_uri_invalid"

        if cancel_event is None:
            cancel_event = threading.Event()

        done_event = threading.Event()
        result = {"code": None, "error": None}

        from http.server import BaseHTTPRequestHandler, HTTPServer
        try:
            from http.server import ThreadingHTTPServer as CallbackHTTPServer
        except Exception:
            CallbackHTTPServer = HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                parsed_path = urllib.parse.urlparse(self.path)
                request_path = parsed_path.path or "/"
                expected_path = path or "/"
                log_to_file(f"PKCE callback received: path={request_path} query={parsed_path.query}")
                if request_path.rstrip("/") != expected_path.rstrip("/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed_path.query)
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
                html = """<!doctype html>
<html lang="fr">
  <head>
    <meta charset="utf-8"/>
    <title>Authentification terminée</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 28px; color: #222; background: #f7f8fb; }
      .card { background: #fff; border: 1px solid #e3e6ef; border-radius: 10px; padding: 18px 20px; max-width: 560px; box-shadow: 0 2px 10px rgba(0,0,0,0.04); }
      .muted { color: #666; }
      .ok { display: inline-block; margin-top: 6px; padding: 6px 10px; background: #e8f5e9; color: #1b5e20; border-radius: 6px; font-weight: 600; }
      .small { font-size: 12px; color: #778; margin-top: 10px; }
    </style>
  </head>
  <body>
    <div class="card">
      <h2>Authentification terminée</h2>
      <div class="ok">Connexion validée</div>
      <p>Vous pouvez fermer cet onglet et revenir à LibreOffice.</p>
      <p class="muted">Si LibreOffice ne réagit pas, attendez quelques secondes puis relancez l’action.</p>
      <div class="small">Aucune action supplémentaire n’est requise ici.</div>
    </div>
  </body>
</html>
"""
                self.wfile.write(html.encode("utf-8"))

        bind_host = "" if host in ("localhost", "127.0.0.1") else host
        try:
            httpd = CallbackHTTPServer((bind_host, port), Handler)
            if hasattr(httpd, "daemon_threads"):
                httpd.daemon_threads = True
        except Exception as e:
            log_to_file(f"Failed to start local callback server: {str(e)}")
            return None, "callback_server_error"
        log_to_file(f"Local callback server listening on http://{bind_host or '0.0.0.0'}:{port}{path}")

        server_thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True
        )
        server_thread.start()

        start = time.time()
        try:
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
        redirect_uri = self._get_config_from_file("keycloak_redirect_uri", "")
        if not redirect_uri and isinstance(config_data, dict):
            inner = config_data.get("config", {}) if isinstance(config_data.get("config"), dict) else config_data
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
        if not allowed and isinstance(config_data, dict):
            inner = config_data.get("config", {}) if isinstance(config_data.get("config"), dict) else config_data
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
                    "Configuration Keycloak invalide",
                    "redirect_uri n'est pas autorisé.\n\n"
                    "Vérifiez keycloak_allowed_redirect_uri."
                )
                return None
        valid = self._validate_redirect_uri(redirect_uri)
        if valid:
            log_to_file(f"Keycloak redirect_uri selected: {valid}")
        return valid

    def _authorization_code_flow(self, config_data):
        auth_endpoint, token_endpoint = self._keycloak_endpoints(config_data)
        if not auth_endpoint or not token_endpoint:
            log_to_file("Keycloak auth endpoints missing; cannot open browser")
            self._show_message(
                "Configuration Keycloak incomplète",
                "Impossible d'ouvrir la page d'authentification : endpoints Keycloak manquants.\n\n"
                "Vérifiez keycloakIssuerUrl / keycloakRealm."
            )
            return None

        client_id = self._get_config_from_file("keycloakClientId", "")
        if not client_id:
            log_to_file("Keycloak client_id missing; cannot open browser")
            self._show_message(
                "Configuration Keycloak incomplète",
                "Impossible d'ouvrir la page d'authentification : client_id manquant."
            )
            return None

        redirect_uri = self._select_redirect_uri(config_data)
        if not redirect_uri:
            log_to_file("Keycloak redirect_uri missing; cannot open browser")
            self._show_message(
                "Configuration Keycloak incomplète",
                "Impossible d'ouvrir la page d'authentification : redirect_uri manquant.\n\n"
                "Exemple : http://localhost:28443/callback"
            )
            return None

        is_first_enrollment = not self._as_bool(self._get_config_from_file("enrolled", False))
        wiz_dialog = wiz_toolkit = wiz_update = wiz_state = None
        if is_first_enrollment:
            proceed, wiz_dialog, wiz_toolkit, wiz_update, wiz_state = self._show_enrollment_wizard()
        else:
            proceed = self._confirm_message(
                "Connexion MIrAI requise",
                "Vous allez être redirigé vers la page de connexion MIrAI dans votre navigateur.\n\n"
                "Après la connexion, revenez à LibreOffice.\n\n"
                "Voulez-vous continuer ?"
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
            f"auth_endpoint={auth_endpoint} client_id={client_id} redirect_uri={redirect_uri} "
            f"code_challenge={code_challenge} state={state} url={auth_url}"
        )
        try:
            import webbrowser
            webbrowser.open(auth_url)
        except Exception:
            try:
                from com.sun.star.system import XSystemShellExecute
                shell = self.ctx.getServiceManager().createInstanceWithContext(
                    "com.sun.star.system.SystemShellExecute", self.ctx
                )
                if isinstance(shell, XSystemShellExecute):
                    shell.execute(auth_url, "", 0)
            except Exception as e:
                log_to_file(f"Failed to open browser: {str(e)}")

        auth_cancel_event = threading.Event()

        # ── Étape 4/5 : attente du callback Keycloak ─────────────────────────
        # Si le wizard est actif, on l'utilise comme dialog d'attente.
        # Sinon on crée un dialog séparé (fallback re-login).
        wait_dialog = None

        if wiz_dialog and wiz_update and wiz_toolkit:
            wiz_update(
                "Connexion en cours...",
                "Votre navigateur est ouvert sur la page de connexion.\n\n"
                "Connectez-vous puis revenez dans LibreOffice.",
                "Étape 4/5 — Connexion",
                4,
                btn_cancel="Annuler",
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
                    wiz_dialog.getControl("wiz_text").getModel().Label = (
                        f"En attente de la connexion{dots}\n\n"
                        "Connectez-vous dans le navigateur puis revenez."
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
                    lbl_m.Label = "Authentification Keycloak..."
                    lbl_m.NoLabel = True
                    lbl = dlg.getControl("auth_wait_label")
                    lbl.setPosSize(10, 24, 280, 20, POSSIZE)
                    btn_m = dlg_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
                    dlg_model.insertByName("auth_wait_cancel", btn_m)
                    btn_m.Label = "Annuler"
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
                        wait_label.getModel().Label = "Annulation..."
                    else:
                        wait_label.getModel().Label = f"Authentification Keycloak{dots}"
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

        code, error = self._wait_for_auth_code(
            redirect_uri,
            timeout_seconds=auth_timeout_seconds,
            tick=_tick,
            cancel_event=auth_cancel_event
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

        def _wiz_show_error_and_wait(title, text, step_label="Étape 4/5 — Connexion"):
            """Affiche une erreur dans le wizard, attend Fermer, ferme le dialog."""
            if not wiz_dialog or not wiz_update or not wiz_toolkit:
                return
            wiz_update(title, text, step_label, 4, btn_next="Fermer")
            wiz_state["cancelled"] = False
            step_snap = wiz_state["step"]
            while wiz_state["step"] == step_snap:
                pump_events(wiz_toolkit)
                time.sleep(0.1)
            _wiz_dispose()

        if error == "cancelled_by_user":
            log_to_file("Authorization code flow cancelled by user")
            _wiz_show_error_and_wait(
                "Connexion annulée",
                "L'authentification a été annulée.\n\nVous pouvez réessayer via le menu MIrAI.",
            )
            return None
        if not code:
            log_to_file(f"Authorization code flow failed: {error}")
            if wiz_dialog:
                err_txt = (
                    "Délai dépassé. Vérifiez la redirection et réessayez."
                    if error == "timeout"
                    else f"Erreur : {error or 'inconnue'}. Vérifiez la configuration."
                )
                _wiz_show_error_and_wait("Connexion échouée", err_txt)
            elif error == "timeout":
                self._show_message(
                    "Connexion expirée",
                    "Le login Keycloak a expiré avant le retour navigateur.\n\n"
                    f"Redirection attendue:\n{redirect_uri}\n\n"
                    "Vérifiez la redirection et relancez Login."
                )
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
                self._secure_bind_identity(access_token)
            except Exception as exc:
                log_to_file(f"Post-SSO identity bind failed: {str(exc)}")
            try:
                if wiz_dialog and wiz_update and wiz_toolkit and wiz_state:
                    # ── Étape 5/5 : enrôlement dans le wizard ────────────────
                    wiz_update(
                        "Enrôlement en cours...",
                        "Enregistrement de votre poste auprès du service MIrAI...",
                        "Étape 5/5 — Enrôlement",
                        5,
                    )
                    enroll_result = {"done": False, "success": False, "error": ""}

                    def _enroll_worker():
                        try:
                            self._ensure_device_management_state()
                            enroll_result["success"] = self._as_bool(
                                self._get_config_from_file("enrolled", False)
                            )
                            if not enroll_result["success"]:
                                enroll_result["error"] = "Non confirmé par le serveur"
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
                            wiz_dialog.getControl("wiz_text").getModel().Label = (
                                f"Enregistrement en cours{dots}"
                            )
                            pump_events(wiz_toolkit)
                        except Exception:
                            pass
                        time.sleep(0.4)

                    # ── Résultat ──────────────────────────────────────────────
                    wiz_state["cancelled"] = False
                    step_snap = wiz_state["step"]
                    if enroll_result["success"]:
                        wiz_update(
                            "Enrôlement terminé !",
                            "L'IA est intégrée directement dans vos documents\n"
                            "Writer et Calc, accessible depuis le menu MIrAI.\n\n"
                            "Writer :\n"
                            "  • Étendre — prolonger votre texte avec l'IA\n"
                            "  • Modifier — reformuler ou corriger une sélection\n"
                            "  • Résumer — condenser un passage\n"
                            "  • Simplifier — rendre un texte plus accessible\n\n"
                            "Calc :\n"
                            "  • Transformer — appliquer une consigne à chaque cellule\n"
                            "  • Formule IA — générer une formule par description\n"
                            "  • Analyser — obtenir une synthèse de vos données\n"
                            "  • =PROMPT() — interroger l'IA dans une cellule\n\n\n"
                            "       Menu MIrAI → 📚 Documentation pour en savoir plus.",
                            "Étape 5/5 — Terminé",
                            5,
                            btn_next="🚀 Commencer à utiliser",
                            title_color=_UI["success"],
                        )
                    else:
                        error_msg = enroll_result["error"] or "Erreur inconnue"
                        wiz_update(
                            "Enrôlement échoué",
                            f"L'enrôlement a échoué.\n"
                            f"Raison : {error_msg}\n\n"
                            "Consultez le menu MIrAI → 📚 Documentation\n"
                            "pour obtenir de l'aide.",
                            "Étape 5/5 — Erreur",
                            5,
                            btn_next="Fermer",
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
        try:
            self._sync_keycloak_from_config(config_data)
        except Exception:
            pass

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
        settings = self._select_settings(config_data) if isinstance(config_data, dict) else {}

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

        # Court-circuit sur les CREDS RELAY, pas sur le drapeau `enrolled`.
        # `enrolled=True` ne prouve que « /enroll a répondu 200 » : un poste
        # marqué enrôlé mais sans creds relay est dans un état absorbant — le DM
        # ne minte alors jamais de llmToken et tout /llm/v1 tombe en 401, sans
        # aucun chemin de sortie. On re-tente donc l'enrôlement (POST /enroll est
        # idempotent côté DM : il ré-émet une paire).
        if self._relay_credentials_valid() and not force_enroll:
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
        log_to_file(f"Device management enroll payload: device_name={device_name} plugin_uuid={plugin_uuid} email={email} has_token={bool(access_token)} endpoint={enroll_endpoint}")
        try:
            json_data = json.dumps(enroll_payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            request = urllib.request.Request(enroll_endpoint, data=json_data, headers=_with_user_agent(headers))
            request.get_method = lambda: 'POST'
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
                # Enrôlement « à moitié » : accepté par le DM mais sans creds
                # relay. On le trace explicitement — c'est cet état, marqué
                # `enrolled` sans creds, qui bloquait le poste indéfiniment.
                log_to_file(
                    "Device management enroll succeeded WITHOUT relay credentials — "
                    "le DM ne pourra minter aucun llmToken (relais désactivé côté "
                    "serveur ?) ; l'enrôlement sera re-tenté"
                )
            self.set_config("enrolled", True)
        except Exception as e:
            error_body = ""
            if hasattr(e, "read"):
                try:
                    error_body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
            log_to_file(f"Device management enroll failed: {str(e)} body={error_body}")

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

    # ── Credentials du proxy LLM (llmToken / relay) ─────────────────────

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
        settings = self._select_settings(self.config_cache)
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
        `enrolled` ne dit que « un POST /enroll a répondu 200 ». Sans ces creds,
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

    def _resolve_llm_token(self, default, telemetry_defaults=None):
        """Résout le llmToken en gardant token et expiration SOLIDAIRES.

        Le llmToken est court (TTL DM 3600 s par défaut) alors que le cache de
        config vit 300 s : le servir sans vérifier son expiration produit un 401
        `invalid_api_key` que rien ne rattrape. Token et `llmTokenExpiresAt`
        sont donc lus depuis la même source, cache DM d'abord puis disque.
        """
        cached = self._get_setting("llm_api_tokens")
        settings = self._select_settings(self.config_cache) or {}
        if cached is not None and len(str(cached)) >= 6:
            if not self._token_expired_at(settings.get("llmTokenExpiresAt")):
                return cached
            log_to_file("[llm-auth] llmToken du cache DM expiré — refresh forcé")
            self._schedule_config_refresh(force=True, reason="llm_token_expired")

        stored = self._get_config_from_file(
            "llm_api_tokens", default, telemetry_defaults=telemetry_defaults)
        if stored and self._token_expired_at(
                self._get_config_from_file("llmTokenExpiresAt", 0)):
            log_to_file("[llm-auth] llmToken persisté expiré — ignoré, refresh forcé")
            self._schedule_config_refresh(force=True, reason="llm_token_expired")
            return default
        return stored

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
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(value, (int, float)):
            return value != 0
        return False

    def _get_proxy_config(self):
        enabled = self._as_bool(self._get_config_from_file("proxy_enabled", False))
        proxy_url = str(self._get_config_from_file("proxy_url", "")).strip()
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
        if not proxy_cfg.get("enabled"):
            log_to_file("[PROXY] disabled")
            handlers = []
            if context is not None:
                handlers.append(urllib.request.HTTPSHandler(context=context))
            return urllib.request.build_opener(*handlers)
        proxy_url = self._normalize_proxy_url(proxy_cfg.get("proxy_url", ""))
        if not proxy_url:
            log_to_file("[PROXY] enabled but proxy_url is empty/invalid")
            handlers = []
            if context is not None:
                handlers.append(urllib.request.HTTPSHandler(context=context))
            return urllib.request.build_opener(*handlers)
        handlers = []
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
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
        try:
            req_url = str(getattr(request, "full_url", "") or "")
            # NE PAS étendre cette règle à /llm/v1 : le trafic LLM s'authentifie
            # avec le llmToken SEUL (scopé "llm", TTL 1 h). Décision du
            # 2026-07-25, surface d'attaque : la paire relay est le credential
            # maître (config + télémétrie + LLM) et vit 30 jours. De plus, côté
            # DM, la présence de X-Relay-Client engage la branche relais qui
            # échoue en 401 SANS repli vers le Bearer — les en-têtes masqueraient
            # donc un llmToken valide. Voir prompts/fix-llm-token-auth.md.
            if "/relay-assistant/" in req_url:
                for header_name, header_value in self._relay_headers().items():
                    try:
                        request.add_header(header_name, header_value)
                    except Exception:
                        pass
        except Exception:
            pass
        proxy_cfg = self._get_proxy_config() if use_proxy else {
            "enabled": False,
            "proxy_url": "",
            "username": "",
            "password": "",
            "allow_insecure_ssl": False,
        }
        allow_insecure = bool(proxy_cfg.get("allow_insecure_ssl"))
        try:
            url = request.full_url if hasattr(request, "full_url") else str(request)
        except Exception:
            url = "<unknown>"
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
            f"[PROXY] request url={url} enabled={proxy_cfg.get('enabled')} "
            f"insecure_ssl={allow_insecure} use_proxy={use_proxy}"
        )
        if timeout is None:
            return opener.open(request)
        return opener.open(request, timeout=timeout)

    def _test_proxy_connection(self, proxy_cfg):
        test_url = "https://example.com"
        try:
            log_to_file(f"[PROXY][TEST] start url={test_url}")
            request = urllib.request.Request(test_url, headers=_with_user_agent({"Accept": "application/json"}))
            context = self.get_ssl_context() if proxy_cfg.get("allow_insecure_ssl") else ssl.create_default_context()
            opener = self._build_proxy_opener(proxy_cfg, context=context)
            log_to_file(f"[PROXY][TEST] connect allow_insecure_ssl={bool(proxy_cfg.get('allow_insecure_ssl'))}")
            with opener.open(request, timeout=8) as response:
                log_to_file(f"[PROXY][TEST] success status={response.status} url={test_url}")
                return True, f"Connexion OK ({response.status}) - URL: {test_url}"
        except Exception as e:
            log_to_file(f"[PROXY][TEST] error url={test_url} err={str(e)}")
            return False, f"Erreur: {str(e)} - URL: {test_url}"

    def _lo_proxy_settings(self):
        settings = {
            "enabled": False,
            "host": "",
            "port": "",
            "username": "",
            "password": "",
            "type": 0,
        }
        try:
            provider = self.ctx.getServiceManager().createInstanceWithContext(
                "com.sun.star.configuration.ConfigurationProvider", self.ctx
            )
            node = PropertyValue()
            node.Name = "nodepath"
            node.Value = "/org.openoffice.Inet/Settings"
            access = provider.createInstanceWithArguments(
                "com.sun.star.configuration.ConfigurationAccess", (node,)
            )
            proxy_type = getattr(access, "ooInetProxyType", 0)
            settings["type"] = int(proxy_type) if proxy_type is not None else 0
            settings["enabled"] = settings["type"] == 1
            http_host = getattr(access, "ooInetProxyHTTPName", "") or ""
            http_port = getattr(access, "ooInetProxyHTTPPort", "") or ""
            https_host = getattr(access, "ooInetProxyHTTPSName", "") or ""
            https_port = getattr(access, "ooInetProxyHTTPSPort", "") or ""
            host = http_host or https_host or ""
            port = http_port or https_port or ""
            settings["host"] = str(host)
            settings["port"] = str(port)
            settings["username"] = str(getattr(access, "ooInetProxyUser", "") or "")
            settings["password"] = str(getattr(access, "ooInetProxyPassword", "") or "")
        except Exception as e:
            log_to_file(f"Failed to read LibreOffice proxy settings: {str(e)}")
        return settings


    def _schedule_enrollment_check(self):
        """Deferred enrollment check — fires ~3s after init to let UI start."""
        def _deferred_enrollment():
            try:
                if MainJob._enrollment_dismissed_cls:
                    return
                if not self._needs_first_enrollment():
                    log_to_file("[ENROLL] Auto-check: already enrolled, skipping wizard")
                    return
                with MainJob._enrollment_wizard_lock_cls:
                    if MainJob._enrollment_wizard_active_cls:
                        log_to_file("[ENROLL] Auto-check: wizard already running, skipping")
                        return
                    MainJob._enrollment_wizard_active_cls = True
                try:
                    log_to_file("[ENROLL] Auto-check: first enrollment needed, launching wizard")
                    if not self._run_first_enrollment():
                        MainJob._enrollment_dismissed_cls = True
                        log_to_file("[ENROLL] Auto-check: wizard cancelled by user")
                    else:
                        log_to_file("[ENROLL] Auto-check: enrollment succeeded")
                finally:
                    MainJob._enrollment_wizard_active_cls = False
            except Exception as e:
                log_to_file(f"[ENROLL] Auto-check failed: {str(e)}")

        timer = threading.Timer(3.0, _deferred_enrollment)
        timer.daemon = True
        timer.start()

    def proxy_settings_box(self, title="Proxy", x=None, y=None):
        WIDTH = 640
        HORI_MARGIN = 16
        VERT_MARGIN = 12
        LABEL_HEIGHT = 20
        EDIT_HEIGHT = 28
        BUTTON_WIDTH = 140
        BUTTON_HEIGHT = 30
        HORI_SEP = 10
        VERT_SEP = 8
        import uno
        from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
        from com.sun.star.awt.PushButtonType import OK, CANCEL
        from com.sun.star.util.MeasureUnit import TWIP
        ctx = uno.getComponentContext()
        def create(name):
            return ctx.getServiceManager().createInstanceWithContext(name, ctx)
        dialog = create("com.sun.star.awt.UnoControlDialog")
        dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
        dialog.setModel(dialog_model)
        dialog.setVisible(False)
        dialog.setTitle(title)

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

        cfg = self._get_proxy_config()
        lo = self._lo_proxy_settings()
        proxy_url_value = cfg["proxy_url"]
        if not proxy_url_value and lo["host"]:
            proxy_url_value = f"{lo['host']}:{lo['port']}" if lo["port"] else lo["host"]

        HEIGHT = VERT_MARGIN * 2 + (LABEL_HEIGHT + EDIT_HEIGHT + VERT_SEP) * 5 + BUTTON_HEIGHT * 2 + VERT_SEP * 6 + 20
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)

        current_y = VERT_MARGIN
        # Section header
        add("label_proxy", "FixedText", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": "Paramètres proxy", "NoLabel": True,
            "FontHeight": _UI["font_section"],
            "TextColor": _UI["primary"],
            "FontWeight": 150,
        })
        current_y += LABEL_HEIGHT + VERT_SEP

        add("label_enabled", "FixedText", HORI_MARGIN, current_y, 200, LABEL_HEIGHT, {
            "Label": "Utiliser un proxy :", "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text"],
        })
        chk_enabled = add("chk_enabled", "CheckBox", HORI_MARGIN + 210, current_y, 50, LABEL_HEIGHT,
            {"State": 1 if cfg["enabled"] else 0})
        current_y += LABEL_HEIGHT + VERT_SEP

        add("label_url", "FixedText", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": "Proxy (host:port) :", "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text"],
        })
        current_y += LABEL_HEIGHT + VERT_SEP
        edit_url = add("edit_url", "Edit", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
            "Text": proxy_url_value, "BackgroundColor": _UI["bg_input"],
        })
        current_y += EDIT_HEIGHT + VERT_SEP * 2

        add("label_user", "FixedText", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": "Login proxy (optionnel) :", "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text_secondary"],
        })
        current_y += LABEL_HEIGHT + VERT_SEP
        edit_user = add("edit_user", "Edit", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
            "Text": cfg["username"], "BackgroundColor": _UI["bg_input"],
        })
        current_y += EDIT_HEIGHT + VERT_SEP * 2

        add("label_pass", "FixedText", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": "Mot de passe proxy (optionnel) :", "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text_secondary"],
        })
        current_y += LABEL_HEIGHT + VERT_SEP
        edit_pass = add("edit_pass", "Edit", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
            "Text": cfg["password"], "EchoChar": ord("*"),
            "BackgroundColor": _UI["bg_input"],
        })
        current_y += EDIT_HEIGHT + VERT_SEP * 2

        add("label_insecure", "FixedText", HORI_MARGIN, current_y, 260, LABEL_HEIGHT, {
            "Label": "Autoriser HTTPS sans vérification (-k) :", "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text"],
        })
        chk_insecure = add("chk_insecure", "CheckBox", HORI_MARGIN + 270, current_y, 50, LABEL_HEIGHT,
            {"State": 1 if cfg["allow_insecure_ssl"] else 0})
        current_y += LABEL_HEIGHT + VERT_SEP * 2

        # Separator
        add("line_lo_info", "FixedLine", HORI_MARGIN, current_y,
            WIDTH - HORI_MARGIN * 2, 2, {})
        current_y += VERT_SEP

        lo_text = "Proxy LibreOffice : "
        if lo["enabled"] and lo["host"]:
            lo_text += f"{lo['host']}:{lo['port']}" if lo["port"] else lo["host"]
        else:
            lo_text += "désactivé"
        add("label_lo", "FixedText", HORI_MARGIN, current_y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": lo_text, "NoLabel": True,
            "FontHeight": _UI["font_small"],
            "TextColor": _UI["text_light"],
        })
        current_y += LABEL_HEIGHT + VERT_SEP * 2

        btn_test = add("btn_test", "Button", HORI_MARGIN, current_y, BUTTON_WIDTH + 20, BUTTON_HEIGHT, {
            "Label": "Tester connexion", "Name": "test_proxy",
            "FontHeight": _UI["font_small"],
        })
        btn_copy = add("btn_copy", "Button", HORI_MARGIN + BUTTON_WIDTH + 30, current_y, BUTTON_WIDTH + 40, BUTTON_HEIGHT, {
            "Label": "Copier depuis LibreOffice", "Name": "copy_lo",
            "FontHeight": _UI["font_small"],
        })
        current_y += BUTTON_HEIGHT + VERT_SEP * 2

        # Separator
        add("line_before_proxy_btns", "FixedLine", HORI_MARGIN, current_y,
            WIDTH - HORI_MARGIN * 2, 2, {})
        current_y += VERT_SEP

        add("btn_ok", "Button", WIDTH - HORI_MARGIN - BUTTON_WIDTH * 2 - HORI_SEP, current_y,
            BUTTON_WIDTH, BUTTON_HEIGHT, {
                "PushButtonType": OK, "DefaultButton": True, "Label": "Enregistrer",
                "FontHeight": _UI["font_label"],
            })
        add("btn_cancel", "Button", WIDTH - HORI_MARGIN - BUTTON_WIDTH, current_y,
            BUTTON_WIDTH, BUTTON_HEIGHT, {
                "PushButtonType": CANCEL, "Label": "Annuler",
                "FontHeight": _UI["font_label"],
            })

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

        class ProxyActionListener(unohelper.Base, XActionListener):
            def __init__(self, outer):
                self.outer = outer
            def actionPerformed(self, event):
                try:
                    command = getattr(event, "ActionCommand", "") or ""
                except Exception:
                    command = ""
                if not command:
                    try:
                        source = getattr(event, "Source", None)
                        command = getattr(source.getModel(), "Name", "") if source else ""
                    except Exception:
                        command = ""
                if command == "copy_lo":
                    try:
                        if lo["enabled"] and lo["host"]:
                            url = f"{lo['host']}:{lo['port']}" if lo["port"] else lo["host"]
                            edit_url.getModel().Text = url
                            chk_enabled.getModel().State = 1
                        else:
                            chk_enabled.getModel().State = 0
                    except Exception:
                        pass
                elif command == "test_proxy":
                    try:
                        proxy_cfg = {
                            "enabled": bool(chk_enabled.getModel().State),
                            "proxy_url": str(edit_url.getModel().Text).strip(),
                            "username": str(edit_user.getModel().Text).strip(),
                            "password": str(edit_pass.getModel().Text),
                            "allow_insecure_ssl": bool(chk_insecure.getModel().State),
                        }
                        ok, message = self.outer._test_proxy_connection(proxy_cfg)
                        self.outer._show_message("Test proxy", message if ok else f"Échec: {message}")
                    except Exception as e:
                        self.outer._show_message("Test proxy", f"Échec: {str(e)}")
            def disposing(self, event):
                return

        listener = ProxyActionListener(self)
        if btn_test:
            try:
                btn_test.addActionListener(listener)
                btn_test.getModel().ActionCommand = "test_proxy"
            except Exception:
                pass
        if btn_copy:
            try:
                btn_copy.addActionListener(listener)
                btn_copy.getModel().ActionCommand = "copy_lo"
            except Exception:
                pass

        result = {}
        if dialog.execute():
            try:
                result["proxy_enabled"] = bool(chk_enabled.getModel().State)
                result["proxy_url"] = str(edit_url.getModel().Text).strip()
                result["proxy_username"] = str(edit_user.getModel().Text).strip()
                result["proxy_password"] = str(edit_pass.getModel().Text)
                result["proxy_allow_insecure_ssl"] = bool(chk_insecure.getModel().State)
                self.set_config("proxy_enabled", result["proxy_enabled"])
                self.set_config("proxy_url", result["proxy_url"])
                self.set_config("proxy_username", result["proxy_username"])
                self.set_config("proxy_password", result["proxy_password"])
                self.set_config("proxy_allow_insecure_ssl", result["proxy_allow_insecure_ssl"])
            except Exception:
                pass
        dialog.dispose()
        return result

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

    def _fetch_models_list(self, endpoint, api_key, is_openwebui):
        return self._fetch_models(endpoint, api_key, is_openwebui, include_info=False)

    def _fetch_models_info(self, endpoint, api_key, is_openwebui):
        return self._fetch_models(endpoint, api_key, is_openwebui, include_info=True)

    def _refresh_config_to_local(self, cancel_flag=None):
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
        if isinstance(config_obj, dict):
            settings = config_obj
            log_to_file("Reload config: using config object")
        else:
            settings = self._select_settings(config_data)

        if not isinstance(settings, dict):
            if isinstance(config_data, dict):
                settings = config_data
                log_to_file("Reload config: using top-level config (no settings wrapper)")
            else:
                log_to_file(f"Reload config: no settings dict found (type={type(config_data).__name__})")
                return {}
        if "model" in settings:
            settings.pop("model", None)
        if "owuiEndpoint" in settings:
            settings.pop("owuiEndpoint", None)
        if "tokenOWUI" in settings:
            settings.pop("tokenOWUI", None)
        self._sync_keycloak_from_settings(settings, config_data)
        # Normalize keycloak fields if provided at top-level settings
        try:
            if "keycloakRealm" not in settings:
                for k in ("realm", "keycloak_realm"):
                    if k in settings and str(settings.get(k) or "").strip():
                        settings["keycloakRealm"] = str(settings.get(k)).strip()
                        break
            if "keycloakIssuerUrl" not in settings:
                for k in ("issuerUrl", "issuerURL", "issuer_url", "baseUrl", "base_url", "keycloakIssuerUrl"):
                    if k in settings and str(settings.get(k) or "").strip():
                        settings["keycloakIssuerUrl"] = str(settings.get(k)).strip()
                        break
            if "keycloakClientId" not in settings:
                for k in ("client_id", "clientId", "clientID", "keycloakClientId"):
                    if k in settings and str(settings.get(k) or "").strip():
                        settings["keycloakClientId"] = str(settings.get(k)).strip()
                        break
        except Exception:
            pass
        config_path = str(self._get_config_from_file("config_path", "/config/config.json"))
        bootstrap_url = str(self._active_bootstrap_url() or "").strip()
        normalized_url = f"{bootstrap_url.rstrip('/')}/{config_path.lstrip('/')}"
        log_to_file(f"Reload config URL computed: {normalized_url}")
        log_to_file(f"Reload config: url={normalized_url} keys={list(settings.keys())}")
        synced = []
        skipped = []
        for meta_key in ("lastversion", "updateUrl", "configVersion", "environment"):
            if isinstance(config_data, dict) and meta_key in config_data:
                meta_val = config_data.get(meta_key)
                if meta_val is None or (isinstance(meta_val, str) and meta_val.strip() == ""):
                    continue
                try:
                    self.set_config(meta_key, meta_val)
                    synced.append(meta_key)
                except Exception:
                    pass
        for key, value in settings.items():
            if value is None or (isinstance(value, str) and value.strip() == ""):
                skipped.append(key)
                continue
            if key in ("proxy_url", "proxy_username", "proxy_password"):
                try:
                    if isinstance(value, str) and len(value.strip()) < 5:
                        skipped.append(key)
                        continue
                except Exception:
                    pass
            try:
                self.set_config(key, value)
                synced.append(key)
            except Exception:
                pass
        log_to_file(f"Device management config synced locally: {synced}")
        if skipped:
            log_to_file(f"Device management config skipped empty values: {skipped}")
        return settings

    def _sync_keycloak_from_settings(self, settings, config_data=None):
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
            return
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
        for target_key, value in keycloak_map.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            # F1 — Le DM est autoritatif sur le SSO : on écrase systématiquement la
            # valeur locale (potentiellement un placeholder baké) avec celle servie par
            # le DM, sinon un placeholder non vide gagnerait à jamais (bug `mysso`).
            try:
                current = str(self._get_config_from_file(target_key, "") or "").strip()
            except Exception:
                current = ""
            if text == current:
                continue
            try:
                self.set_config(target_key, text)
                log_to_file(f"[keycloak-sync] {target_key} updated from DM")
            except Exception:
                pass

    def _sync_keycloak_from_config(self, config_data):
        settings = None
        if isinstance(config_data, dict):
            config_obj = config_data.get("config")
            if isinstance(config_obj, dict):
                settings = config_obj
            else:
                settings = self._select_settings(config_data)
        self._sync_keycloak_from_settings(settings if isinstance(settings, dict) else {}, config_data=config_data)

    def _get_cached_models(self, endpoint, api_key, is_openwebui):
        key = (endpoint, api_key, bool(is_openwebui))
        now = time.time()
        if self._models_cache and self._models_cache_key == key:
            if (now - self._models_cache_loaded_at) < self._models_cache_ttl:
                return self._models_cache
        models = self._fetch_models_list(endpoint, api_key, is_openwebui)
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
        auth_headers = {"Content-Type": "application/json"}
        if is_openwebui:
            header_name, header_prefix = self._auth_header()
            if api_key:
                auth_headers[header_name] = f"{header_prefix}{api_key}"
        elif api_key:
            header_name, header_prefix = self._auth_header()
            auth_headers[header_name] = f"{header_prefix}{api_key}"

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



    def make_api_request(self, prompt, system_prompt="", max_tokens=15000, api_type=None):
        """
        Build a streaming chat/completions request for OpenAI-compatible endpoints.
        The api_type parameter is accepted for backwards compatibility but ignored
        — all requests use the chat/completions format.
        """
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 15000

        endpoint = str(self.get_config("llm_base_urls", "http://127.0.0.1:5000")).rstrip("/")
        api_key = self._effective_api_token(self.get_config("llm_api_tokens", ""))
        api_type = "chat"
        model = str(self.get_config("llm_default_models", ""))
        
        # Default system prompt: ask for structured Markdown (converted to native
        # Writer formatting on insertion — see src/mirai/formatting) and enforce
        # language preservation. /no_thinking prefix minimises reasoning tokens
        # on Qwen3-style models.
        default_system_prompt = (
            "/no_thinking\n"
            "Mets en forme ta réponse en Markdown standard : **gras**, *italique*, "
            "titres avec #, listes avec - ou 1., citations avec >, liens en "
            "[texte](url), tableaux avec des barres verticales. N'utilise JAMAIS de "
            "balises HTML, sauf pour un alignement explicitement demandé "
            "(centré, justifié, à droite), à indiquer uniquement avec "
            "<p style=\"text-align:center\">...texte...</p> (ou right/justify) "
            "autour du paragraphe concerné. "
            "RÈGLE ABSOLUE : tu DOIS répondre dans la MÊME LANGUE que le texte "
            "fourni par l'utilisateur. Si le texte est en français, réponds en "
            "français. Si le texte est en anglais, réponds en anglais. Ne change "
            "jamais la langue."
        )
        if system_prompt:
            system_prompt = default_system_prompt + " " + system_prompt
        else:
            system_prompt = default_system_prompt
        
        log_to_file(f"=== API Request Debug ===")
        log_to_file(f"Endpoint: {endpoint}")
        log_to_file(f"API Type: {api_type}")
        log_to_file(f"Model: {model}")
        log_to_file(f"Max Tokens: {max_tokens}")

        headers = {
            'Content-Type': 'application/json'
        }

        endpoint, api_path = self._split_endpoint_api_path(endpoint, True)
        header_name, header_prefix = self._auth_header()
        if api_key:
            headers[header_name] = f'{header_prefix}{api_key}'
        log_to_file(f"[llm-auth] {self._llm_auth_debug()}")

        url = endpoint + api_path + "/chat/completions"
        log_to_file(f"Full URL: {url}")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        data = {
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': 1,
            'top_p': 0.9,
            'stream': True
        }

        if model:
            data["model"] = model
            try:
                model_lower = model.lower()
                model_limits = {
                    "deepseek-r1-distill-llama-70b": 8196,
                    "llama-3.3-70b-instruct": 4096,
                }
                limit = None
                for key, value in model_limits.items():
                    if key in model_lower:
                        limit = value
                        break
                if limit:
                    if max_tokens > limit:
                        max_tokens = limit
                        data["max_tokens"] = limit
                    data["max_completion_tokens"] = min(int(data.get("max_tokens", limit)), limit)
                    log_to_file(f"Max tokens clamped for {model}: {data['max_completion_tokens']}")
            except Exception:
                pass

        json_data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        log_to_file(f"Request data: {json.dumps(data, ensure_ascii=False, indent=2)}")
        log_to_file(f"Headers: {_redacted_headers(headers)}")
        try:
            curl_headers = _curl_headers_for_log(headers)
            log_to_file(f"Chat completions curl: curl -i -X POST {curl_headers} '{url}' -d '{json.dumps(data)}'")
        except Exception:
            pass
        
        # Note: method='POST' is implicit when data is provided
        request = urllib.request.Request(url, data=json_data, headers=_with_user_agent(headers))
        request.get_method = lambda: 'POST'
        return request

    def make_chat_request(self, messages, max_tokens=2000, api_type=None):
        """Build a streaming chat request from a full messages[] array.

        Unlike make_api_request, the messages list is forwarded as-is
        (no default system-prompt prepended).  Useful for multi-turn
        conversations where the caller manages the history.
        The api_type parameter is accepted for backwards compatibility but ignored.
        """
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 2000

        endpoint = str(self.get_config("llm_base_urls", "http://127.0.0.1:5000")).rstrip("/")
        api_key = self._effective_api_token(self.get_config("llm_api_tokens", ""))
        model = str(self.get_config("llm_default_models", ""))

        headers = {"Content-Type": "application/json"}
        endpoint, api_path = self._split_endpoint_api_path(endpoint, True)
        header_name, header_prefix = self._auth_header()
        if api_key:
            headers[header_name] = f"{header_prefix}{api_key}"
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
        request.get_method = lambda: "POST"
        return request

    def extract_content_from_response(self, chunk, api_type="chat"):
        """Extract text content from an OpenAI chat/completions SSE chunk.

        The api_type parameter is accepted for backwards compatibility but ignored
        — always uses the chat format (delta.content).
        """
        if "choices" in chunk and len(chunk["choices"]) > 0:
            delta = chunk["choices"][0].get("delta", {})
            return delta.get("content", ""), chunk["choices"][0].get("finish_reason")
        return "", None

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

    def stream_request(self, request, api_type, append_callback):
        """
        Stream a completion/chat response and append incremental chunks via
        the provided callback.  The HTTP I/O runs in a background thread so
        the LibreOffice UI stays responsive (processEventsToIdle pumped on
        the main thread).
        """
        import queue as _queue

        toolkit = self.ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.awt.Toolkit", self.ctx
        )
        ssl_context = self.get_ssl_context()
        try:
            request_timeout = int(self.get_config("llm_request_timeout_seconds", 45))
        except Exception:
            request_timeout = 45
        if request_timeout < 5:
            request_timeout = 5

        log_to_file("=== Starting stream request ===")
        log_to_file(f"Request URL: {request.full_url}")
        log_to_file(f"Request timeout: {request_timeout}s")

        _DONE = object()          # sentinel
        _ERROR_401 = object()     # sentinel for auth error
        _ERROR_403 = object()     # sentinel for permission error (token not yet synced)
        _ERROR_429 = object()     # sentinel for quota exceeded (paired with retry_after)
        chunk_queue = _queue.Queue()

        def _network_thread():
            """Runs in background – reads HTTP stream, pushes chunks."""
            try:
                with self._urlopen(request, context=ssl_context,
                                   timeout=request_timeout) as response:
                    log_to_file(f"Response status: {response.status}")
                    _line_count = 0
                    _data_count = 0
                    for line in response:
                        _line_count += 1
                        try:
                            if line.strip() and line.startswith(b"data: "):
                                _data_count += 1
                                payload = line[len(b"data: "):].decode("utf-8").strip()
                                if payload == "[DONE]":
                                    log_to_file(f"[stream] [DONE] after {_line_count} lines, {_data_count} data")
                                    break
                                chunk = json.loads(payload)
                                content, finish_reason = \
                                    self.extract_content_from_response(chunk, api_type)
                                if _data_count <= 2:
                                    log_to_file(f"[stream] sample chunk keys={list(chunk.keys())} content={content!r} finish={finish_reason}")
                                if content:
                                    chunk_queue.put(content)
                                if finish_reason:
                                    log_to_file(f"[stream] finish_reason={finish_reason} after {_data_count} data chunks")
                                    break
                        except Exception as e:
                            log_to_file(f"Error processing line: {str(e)}")
                            chunk_queue.put(str(e))
                    else:
                        log_to_file(f"[stream] stream ended: {_line_count} lines, {_data_count} data chunks")
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    body = ""
                error_code, retry_after = self._parse_llm_error(e.code, body, e.headers)
                request_id = ""
                try:
                    if e.headers:
                        request_id = str(e.headers.get("X-Request-Id", "") or "")
                except Exception:
                    pass
                if e.code == 429:
                    chunk_queue.put((_ERROR_429, retry_after))
                elif e.code == 401 or ("\"401\"" in body or "status\":401" in body
                                       or "code\":401" in body):
                    chunk_queue.put(_ERROR_401)
                elif e.code == 403:
                    chunk_queue.put(_ERROR_403)
                # Vue « parc côté client » : journalisation fonctionnelle de
                # l'erreur relais (429/401/403/5xx), corrélée à l'audit serveur
                # par X-Request-Id — jamais de contenu (protocole DM § 8 bis).
                self._send_llm_relay_error(
                    e.code, error_code, retry_after=retry_after,
                    request_id=request_id, will_retry=(e.code == 403))
                log_to_file(
                    f"ERROR in stream_request: HTTP {e.code} {e.reason} "
                    f"request_id={request_id} body={body[:2000]}")
            except Exception as e:
                reason = str(e)
                self._send_llm_relay_error(
                    0, "timeout" if "timed out" in reason.lower() else "network_error")
                log_to_file(f"ERROR in stream_request: {reason}")
            finally:
                chunk_queue.put(_DONE)

        t = threading.Thread(target=_network_thread, daemon=True)
        t.start()

        # Show the thinking widget (plume icon + animated dots)
        self._show_thinking()
        _dots_tick = 0
        _got_first_chunk = False

        # Main-thread loop: drain queue, call callback, keep UI alive
        try:
            while True:
                try:
                    item = chunk_queue.get(timeout=0.05)
                except _queue.Empty:
                    # No data yet — animate dots and pump UI events
                    _dots_tick += 1
                    if _dots_tick % 6 == 0:  # ~every 300ms
                        self._update_thinking_dots()
                    pump_events(toolkit)
                    continue

                if item is _DONE:
                    break
                if item is _ERROR_401:
                    try:
                        self._show_message_and_open_settings(
                            "Token invalide",
                            "Votre token n'est plus valide.\n\n"
                            "Voulez-vous ouvrir les préférences pour le vérifier ?"
                        )
                    except Exception:
                        pass
                    continue
                if item is _ERROR_403:
                    log_to_file("[stream] 403 received — caller should retry after config refresh")
                    continue
                if isinstance(item, tuple) and len(item) == 2 and item[0] is _ERROR_429:
                    # Quota atteint : respecter retry_after (pas de réessai
                    # automatique) et l'afficher à l'utilisateur.
                    try:
                        delay = f"{int(item[1])} secondes" if item[1] else "quelques instants"
                    except (TypeError, ValueError):
                        delay = "quelques instants"
                    try:
                        self._show_message(
                            "Quota de requêtes atteint",
                            "Le quota de requêtes vers l'assistant IA est atteint "
                            "pour le moment.\n\n"
                            f"Merci de réessayer dans {delay}.")
                    except Exception:
                        pass
                    continue

                # Close thinking widget on first real chunk
                if not _got_first_chunk:
                    _got_first_chunk = True
                    self._close_thinking()

                append_callback(item)
                pump_events(toolkit)
        finally:
            self._close_thinking()

    #retrieved from https://wiki.documentfoundation.org/Macros/General/IO_to_Screen
    #License: Creative Commons Attribution-ShareAlike 3.0 Unported License,
    #License: The Document Foundation  https://creativecommons.org/licenses/by-sa/3.0/
    #begin sharealike section 
    def input_box(self,message, title="", default="", x=None, y=None, ok_label="OK", cancel_label="Annuler", always_on_top=False):
        """ Shows dialog with input box.
            @param message message to show on the dialog
            @param title window title
            @param default default value
            @param x optional dialog position in twips
            @param y optional dialog position in twips
            @return string if OK button pushed, otherwise zero length string
        """
        WIDTH = 720
        HORI_MARGIN = VERT_MARGIN = 8
        BUTTON_WIDTH = 100
        BUTTON_HEIGHT = 30
        HORI_SEP = VERT_SEP = 8
        LABEL_HEIGHT = 26
        EDIT_HEIGHT = 80
        HEIGHT = VERT_MARGIN * 2 + LABEL_HEIGHT + VERT_SEP + EDIT_HEIGHT + VERT_SEP + BUTTON_HEIGHT + VERT_MARGIN
        import uno
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
            dialog_model.BackgroundColor = 0xFFFFFF
        except Exception:
            pass
        if always_on_top:
            try:
                dialog_model.AlwaysOnTop = True
            except Exception:
                pass
            try:
                dialog_model.Closeable = True
            except Exception:
                pass
        dialog.setVisible(False)
        dialog.setTitle(title)
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
        try:
            dialog.getModel().Sizeable = True
        except Exception:
            pass
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

        edit_y = VERT_MARGIN + LABEL_HEIGHT + VERT_SEP
        btn_y = edit_y + EDIT_HEIGHT + VERT_SEP
        try:
            dialog_model.BackgroundColor = _UI["bg"]
        except Exception:
            pass
        add("label", "FixedText", HORI_MARGIN, VERT_MARGIN, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": str(message), "NoLabel": True,
            "FontHeight": _UI["font_label"],
            "TextColor": _UI["text"],
        })
        add("edit", "Edit", HORI_MARGIN, edit_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
            "Text": str(default), "MultiLine": True,
            "BackgroundColor": _UI["bg_input"],
        })
        add("btn_ok", "Button", WIDTH - HORI_MARGIN - BUTTON_WIDTH, btn_y,
                BUTTON_WIDTH, BUTTON_HEIGHT, {"PushButtonType": OK, "DefaultButton": True, "Label": ok_label})
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
        edit = dialog.getControl("edit")
        edit.setSelection(uno.createUnoStruct("com.sun.star.awt.Selection", 0, len(str(default))))
        edit.setFocus()
        ret = edit.getModel().Text if dialog.execute() else ""
        dialog.dispose()
        return ret

    def _chunk_doc_paragraphs(self, doc):
        """Enumerate paragraphs and group into smart chunks for LLM processing.

        Break points (priority): page break > style change > empty paragraph >
        end-of-sentence punctuation > any paragraph boundary when over limit.
        """
        chunk_max = int(self.get_config("edit_chunk_max_chars", 3000))
        paragraphs = []
        enum = doc.Text.createEnumeration()
        while enum.hasMoreElements():
            para = enum.nextElement()
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
            p_text = para.getString()
            p_style = ""
            p_break = False
            try:
                p_style = para.getPropertyValue("ParaStyleName")
            except Exception:
                pass
            try:
                bt = para.getPropertyValue("BreakType")
                # PAGE_BEFORE=4, PAGE_AFTER=5, PAGE_BOTH=6
                if hasattr(bt, 'value'):
                    p_break = bt.value in ("PAGE_BEFORE", "PAGE_AFTER", "PAGE_BOTH")
                else:
                    p_break = bt in (4, 5, 6)
            except Exception:
                pass
            paragraphs.append({
                "text": p_text, "style": p_style,
                "page_break": p_break, "obj": para,
            })

        if not paragraphs:
            return []

        chunks = []
        current_chunk = []
        current_len = 0
        prev_style = paragraphs[0]["style"]

        for p in paragraphs:
            p_len = len(p["text"]) + 1  # +1 for separator

            should_break = False
            if current_len > 0:
                if p["page_break"]:
                    should_break = True
                elif current_len + p_len > chunk_max:
                    should_break = True
                elif current_len > chunk_max * 0.6:
                    if p["style"] != prev_style:
                        should_break = True
                    elif p["text"].strip() == "":
                        should_break = True
                    elif current_chunk and current_chunk[-1]["text"].rstrip().endswith(
                            (".", "!", "?", "\u2026", ";")):
                        should_break = True

            if should_break and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_len = 0

            current_chunk.append(p)
            current_len += p_len
            prev_style = p["style"]

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @staticmethod
    def _parse_find_replace(text):
        """Parse <<<FIND>>>...<<<REPLACE>>>...<<<END>>> blocks from LLM output.

        Handles multiline FIND blocks by splitting them into per-line pairs
        (UNO's findFirst cannot match across paragraph boundaries).
        Also strips [Pn] markers the LLM may echo back from the prompt.
        """
        import re
        raw_blocks = re.findall(
            r'<<<FIND>>>\s*\n?(.*?)<<<REPLACE>>>\s*\n?(.*?)<<<END>>>',
            text, re.DOTALL,
        )
        # Strip [Pn] markers that may be echoed by the LLM
        _strip_pn = re.compile(r'^\[P\d+\]\s*', re.MULTILINE)
        result = []
        for f_raw, r_raw in raw_blocks:
            f_clean = _strip_pn.sub('', f_raw).strip()
            r_clean = _strip_pn.sub('', r_raw).strip()
            if not f_clean:
                continue
            # If FIND spans multiple lines, split into per-line pairs
            f_lines = f_clean.split('\n')
            r_lines = r_clean.split('\n')
            if len(f_lines) > 1:
                # Pair each FIND line with corresponding REPLACE line
                for i, fl in enumerate(f_lines):
                    fl = fl.strip()
                    if not fl:
                        continue
                    rl = r_lines[i].strip() if i < len(r_lines) else fl
                    result.append((fl, rl))
            else:
                result.append((f_clean, r_clean))
        return result

    def _run_whole_doc_edit(self, doc, user_input):
        """Edit the whole document chunk-by-chunk with surgical FIND/REPLACE."""
        chunks = self._chunk_doc_paragraphs(doc)
        if not chunks:
            self._show_message("Modification", "Document vide.")
            return

        log_to_file(f"WholeDocEdit: {len(chunks)} chunk(s)")

        system_prompt = (
            "Tu es un éditeur de texte professionnel. "
            "Tu appliques les instructions sans poser de question. "
            "Tu réponds UNIQUEMENT avec des blocs <<<FIND>>>...<<<REPLACE>>>...<<<END>>>. "
            "Si aucune modification n'est nécessaire, réponds uniquement : <<<NOCHANGE>>>"
        )
        api_type = str(self.get_config("api_type", "completions")).lower()

        # ── Wait dialog ──────────────────────────────────────────────────
        wait_dialog = {"dialog": None, "bg": None, "label": None, "toolkit": None}
        cancelled = {"value": False}

        def _show_progress(chunk_idx, total):
            try:
                from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
                WIDTH, HEIGHT = 420, 160
                ctx = uno.getComponentContext()
                def _cr(n):
                    return ctx.getServiceManager().createInstanceWithContext(n, ctx)
                if not wait_dialog["dialog"]:
                    dlg = _cr("com.sun.star.awt.UnoControlDialog")
                    dlg_m = _cr("com.sun.star.awt.UnoControlDialogModel")
                    dlg.setModel(dlg_m)
                    dlg.setVisible(False)
                    dlg.setTitle("MIrAI – Édition du document")
                    dlg.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
                    try:
                        dlg_m.BackgroundColor = _UI["bg_accent"]
                    except Exception:
                        pass
                    def _add(name, typ, x, y, w, h, props):
                        m = dlg_m.createInstance("com.sun.star.awt.UnoControl" + typ + "Model")
                        dlg_m.insertByName(name, m)
                        c = dlg.getControl(name)
                        c.setPosSize(x, y, w, h, POSSIZE)
                        for k, v in props.items():
                            try:
                                setattr(m, k, v)
                            except Exception:
                                pass
                        return c
                    lbl = _add("lbl_progress", "FixedText", 8, 8, WIDTH - 16, 20, {
                        "Label": f"Bloc {chunk_idx + 1} / {total}...",
                        "FontHeight": _UI["font_label"],
                        "TextColor": _UI["primary"],
                    })
                    bg = _add("edit_stream", "Edit", 8, 34, WIDTH - 16, 80, {
                        "Text": "", "MultiLine": True, "ReadOnly": True,
                        "BackgroundColor": _UI["bg_accent"],
                        "TextColor": _UI["primary"],
                        "FontHeight": 7, "Border": 0,
                    })
                    btn = _add("btn_cancel", "Button", WIDTH // 2 - 50, 122, 100, 26, {
                        "Label": "Annuler",
                    })
                    class _CL(unohelper.Base, XActionListener):
                        def actionPerformed(self, ev):
                            cancelled["value"] = True
                        def disposing(self, ev):
                            pass
                    btn.addActionListener(_CL())
                    frame = _cr("com.sun.star.frame.Desktop").getCurrentFrame()
                    window = frame.getContainerWindow() if frame else None
                    toolkit = _cr("com.sun.star.awt.Toolkit")
                    dlg.createPeer(toolkit, window)
                    if window:
                        ps = window.getPosSize()
                        dlg.setPosSize(ps.Width // 2 - WIDTH // 2 + int(ps.Width * 0.15),
                                       ps.Height // 2 - HEIGHT // 2, 0, 0, POS)
                    dlg.setVisible(True)
                    wait_dialog["dialog"] = dlg
                    wait_dialog["bg"] = bg
                    wait_dialog["label"] = lbl
                    wait_dialog["toolkit"] = toolkit
                else:
                    wait_dialog["label"].getModel().Label = f"Bloc {chunk_idx + 1} / {total}..."
                    wait_dialog["bg"].getModel().Text = ""
                if wait_dialog["toolkit"]:
                    pump_events(wait_dialog["toolkit"])
            except Exception:
                pass

        stream_buf = {"text": ""}

        def _update_stream(chunk_text):
            if cancelled["value"]:
                return
            stream_buf["text"] += chunk_text
            if len(stream_buf["text"]) > 1200:
                stream_buf["text"] = stream_buf["text"][-1200:]
            try:
                if wait_dialog["bg"]:
                    wait_dialog["bg"].getModel().Text = stream_buf["text"]
                    # Auto-scroll to bottom
                    try:
                        end_pos = len(stream_buf["text"])
                        sel = uno.createUnoStruct("com.sun.star.awt.Selection", end_pos, end_pos)
                        wait_dialog["bg"].setSelection(sel)
                    except Exception:
                        pass
                if wait_dialog["toolkit"]:
                    pump_events(wait_dialog["toolkit"])
            except Exception:
                pass

        def _close_progress():
            try:
                if wait_dialog["dialog"]:
                    wait_dialog["dialog"].setVisible(False)
                    wait_dialog["dialog"].dispose()
            except Exception:
                pass

        # ── Process each chunk ───────────────────────────────────────────
        total_replacements = 0
        total_chunks = len(chunks)

        try:
            for chunk_idx, chunk in enumerate(chunks):
                if cancelled["value"]:
                    break
                # Build numbered paragraph list (skip empty paragraphs)
                numbered_lines = []
                for pi, p in enumerate(chunk):
                    if p["text"].strip():
                        numbered_lines.append(f"[P{pi + 1}] {p['text']}")
                if not numbered_lines:
                    continue
                chunk_text = "\n".join(numbered_lines)

                _show_progress(chunk_idx, total_chunks)
                stream_buf["text"] = ""

                prompt = (
                    f"TEXTE À MODIFIER (bloc {chunk_idx + 1}/{total_chunks}) :\n"
                    f"{chunk_text}\n\n"
                    f"INSTRUCTIONS : {user_input}\n\n"
                    "RÈGLES STRICTES :\n"
                    "- Chaque [Pn] est un paragraphe SÉPARÉ\n"
                    "- Produis UN bloc <<<FIND>>>...<<<REPLACE>>>...<<<END>>> PAR PARAGRAPHE modifié\n"
                    "- Dans <<<FIND>>>, mets le texte EXACT et COMPLET du paragraphe (sans le [Pn])\n"
                    "- Dans <<<REPLACE>>>, mets le texte de remplacement\n"
                    "- Ne fusionne JAMAIS plusieurs paragraphes dans un seul bloc FIND\n"
                    "- Ne pose AUCUNE question, n'ajoute AUCUN commentaire\n"
                    "- Si aucune modification nécessaire : <<<NOCHANGE>>>\n"
                )

                accumulated = ""
                def _append(t):
                    nonlocal accumulated
                    accumulated += t
                    _update_stream(t)

                max_tokens = len(chunk_text) + int(
                    self.get_config("edit_selection_max_new_tokens", 15000))
                request = self.make_api_request(
                    prompt, system_prompt, max_tokens, api_type=api_type)
                self.stream_request(request, api_type, _append)

                if cancelled["value"]:
                    break

                if "<<<NOCHANGE>>>" in accumulated:
                    log_to_file(f"WholeDocEdit: chunk {chunk_idx + 1} – no changes")
                    continue

                replacements = self._parse_find_replace(accumulated)
                log_to_file(
                    f"WholeDocEdit: chunk {chunk_idx + 1} → "
                    f"{len(replacements)} replacement(s)")

                for find_text, replace_text in replacements:
                    try:
                        search = doc.createSearchDescriptor()
                        search.SearchRegularExpression = False
                        search.SearchString = find_text
                        found = doc.findFirst(search)
                        if found:
                            found.setString(replace_text)
                            total_replacements += 1
                        else:
                            log_to_file(
                                f"WholeDocEdit: not found: "
                                f"{find_text[:60]}...")
                    except Exception as e:
                        log_to_file(f"WholeDocEdit: replace error: {e}")
        finally:
            _close_progress()

        log_to_file(f"WholeDocEdit: done – {total_replacements} replacement(s)")
        if cancelled["value"]:
            return
        if total_replacements == 0:
            self._show_message(
                "Modification",
                "Aucune modification applicable trouvée dans le document.")

    def _run_edit_selection(self, text, text_range, user_input):
        original_text = text_range.getString()
        if len(original_text.strip()) == 0:
            # No selection → whole-document chunked edit (preserves styles)
            try:
                desktop = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx)
                doc = desktop.getCurrentComponent()
                if doc and hasattr(doc, "Text"):
                    log_to_file("EditSelection: no selection → whole-doc edit mode")
                    self._run_whole_doc_edit(doc, user_input)
                    return
            except Exception as e:
                log_to_file(f"EditSelection: whole-doc edit failed, fallback: {e}")

        wait_dialog = {"dialog": None, "bg": None, "toolkit": None}
        wait_buffer = {"text": "Contacte MIrAI..."}
        cancelled = {"value": False}
        def _show_wait():
            try:
                from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
                WIDTH = 420
                BUTTON_HEIGHT = 26
                HORI_MARGIN = VERT_MARGIN = 8
                LABEL_HEIGHT = 18
                VERT_SEP = 8
                BG_HEIGHT = 96
                HEIGHT = VERT_MARGIN * 2 + LABEL_HEIGHT + VERT_SEP + BG_HEIGHT + VERT_SEP + BUTTON_HEIGHT
                ctx = uno.getComponentContext()
                def create(name):
                    return ctx.getServiceManager().createInstanceWithContext(name, ctx)
                dialog = create("com.sun.star.awt.UnoControlDialog")
                dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
                dialog.setModel(dialog_model)
                dialog.setVisible(False)
                dialog.setTitle("MIrAI")
                dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
                try:
                    dialog_model.BackgroundColor = _UI["bg_accent"]
                except Exception:
                    pass
                def add(name, type, x_, y_, width_, height_, props):
                    try:
                        model = dialog_model.createInstance("com.sun.star.awt.UnoControl" + type + "Model")
                        dialog_model.insertByName(name, model)
                        control = dialog.getControl(name)
                        control.setPosSize(x_, y_, width_, height_, POSSIZE)
                        for key, value in props.items():
                            try:
                                setattr(model, key, value)
                            except Exception:
                                pass
                        return control
                    except Exception:
                        return None
                add("label_wait", "FixedText", HORI_MARGIN, VERT_MARGIN,
                    WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT,
                    {"Label": "MIrAI réfléchit...", "NoLabel": True,
                     "FontHeight": _UI["font_label"],
                     "TextColor": _UI["primary"],
                    })
                bg_y = VERT_MARGIN + LABEL_HEIGHT + VERT_SEP
                bg = add("edit_wait_bg", "Edit", HORI_MARGIN, bg_y,
                    WIDTH - HORI_MARGIN * 2, BG_HEIGHT,
                    {"Text": wait_buffer["text"], "MultiLine": True, "ReadOnly": True})
                if bg:
                    try:
                        bg.getModel().BackgroundColor = _UI["bg_accent"]
                        bg.getModel().TextColor = _UI["primary"]
                        bg.getModel().FontHeight = 7
                        bg.getModel().Border = 0
                    except Exception:
                        pass
                btn_cancel_y = bg_y + BG_HEIGHT + VERT_SEP
                CANCEL_BTN_WIDTH = 100
                btn_cancel_wait = add(
                    "btn_cancel_wait", "Button",
                    WIDTH // 2 - CANCEL_BTN_WIDTH // 2, btn_cancel_y,
                    CANCEL_BTN_WIDTH, BUTTON_HEIGHT,
                    {"Label": "Annuler"}
                )

                class CancelWaitListener(unohelper.Base, XActionListener):
                    def actionPerformed(self, event):
                        cancelled["value"] = True
                    def disposing(self, event):
                        return

                if btn_cancel_wait:
                    try:
                        btn_cancel_wait.addActionListener(CancelWaitListener())
                    except Exception:
                        pass

                frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
                window = frame.getContainerWindow() if frame else None
                toolkit = create("com.sun.star.awt.Toolkit")
                dialog.createPeer(toolkit, window)
                if window:
                    ps = window.getPosSize()
                    _x = ps.Width / 2 - WIDTH / 2 + int(ps.Width * 0.15)
                    _y = ps.Height / 2 - HEIGHT / 2
                    dialog.setPosSize(_x, _y, 0, 0, POS)
                dialog.setVisible(True)
                wait_dialog["dialog"] = dialog
                wait_dialog["bg"] = bg
                wait_dialog["toolkit"] = toolkit
                if bg:
                    try:
                        bg.getModel().Text = wait_buffer["text"]
                    except Exception:
                        pass
                pump_events(toolkit)
                time.sleep(0.05)
            except Exception:
                pass

        def _update_wait(chunk_text):
            if not wait_dialog["bg"]:
                return
            try:
                wait_buffer["text"] += chunk_text
                if len(wait_buffer["text"]) > 1200:
                    wait_buffer["text"] = wait_buffer["text"][-1200:]
                wait_dialog["bg"].getModel().Text = wait_buffer["text"]
                # Auto-scroll to bottom
                try:
                    end_pos = len(wait_buffer["text"])
                    sel = uno.createUnoStruct("com.sun.star.awt.Selection", end_pos, end_pos)
                    wait_dialog["bg"].setSelection(sel)
                except Exception:
                    pass
                if wait_dialog.get("toolkit"):
                    pump_events(wait_dialog["toolkit"])
                time.sleep(0.01)
            except Exception:
                pass

        def _close_wait():
            try:
                if wait_dialog["dialog"]:
                    wait_dialog["dialog"].setVisible(False)
                    wait_dialog["dialog"].dispose()
            except Exception:
                pass

        try:
            path_settings = self.sm.createInstanceWithContext('com.sun.star.util.PathSettings', self.ctx)
            user_config_path = getattr(path_settings, "UserConfig")
            if user_config_path.startswith('file://'):
                user_config_path = str(uno.fileUrlToSystemPath(user_config_path))
            prompt_log_path = os.path.join(user_config_path, "prompt.txt")
            with open(prompt_log_path, "a", encoding="utf-8") as f:
                f.write(user_input.strip() + "\n")
                f.write("-" * 40 + "\n")
        except Exception:
            pass

        system_prompt = self.get_config(
            "edit_selection_system_prompt",
            "Tu es un éditeur de texte. Tu dois appliquer les instructions sans poser de questions. Interdiction totale de poser une question, de demander des précisions ou de commenter. Tu dois produire uniquement le texte modifié, sans préambule, sans explication et sans guillemets. Ne répète pas les instructions."
        )
        api_type = str(self.get_config("api_type", "completions")).lower()

        # If selection is empty, insert at the current cursor position
        try:
            if text_range.getString() == "":
                model = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx
                ).getCurrentComponent()
                controller = model.getCurrentController() if model else None
                view_cursor = controller.getViewCursor() if controller else None
                if view_cursor:
                    text_range = view_cursor
        except Exception:
            pass

        accumulated_text = ""
        stop_phrases = [
            "end of document",
            "end of the document",
            "[END]",
            "---END---"
        ]
        question_patterns = [
            "would you like",
            "do you want",
            "should i",
            "can i help",
            "what would you prefer",
            "could you clarify",
            "please specify",
            "here is",
            "here's",
            "i've made",
            "i have made",
            "voulez-vous",
            "souhaitez-vous",
            "aimeriez-vous",
            "préférez-vous",
            "dois-je",
            "devrais-je",
            "puis-je",
            "est-ce que vous",
            "pouvez-vous préciser",
            "pourriez-vous clarifier",
            "veuillez préciser",
            "voici",
            "voilà",
            "j'ai modifié",
            "j'ai changé",
            "j'ai fait",
            "que souhaitez",
            "quelle version",
            "quel style"
        ]

        aborted = {"value": False}

        def append_text(chunk_text):
            nonlocal accumulated_text
            if cancelled["value"]:
                return
            accumulated_text += chunk_text
            _update_wait(chunk_text)
            lower_text = accumulated_text.lower()
            for pattern in question_patterns:
                if pattern in lower_text:
                    aborted["value"] = True
                    return
            for stop_phrase in stop_phrases:
                if stop_phrase.lower() in accumulated_text.lower():
                    pos = accumulated_text.lower().find(stop_phrase.lower())
                    accumulated_text = accumulated_text[:pos].rstrip()
                    return

        def _edit_segment(segment_text):
            prompt = """ORIGINAL VERSION:
""" + segment_text + """

INSTRUCTIONS: """ + user_input + """

IMPORTANT RULES:
- Do NOT ask any questions
- Do NOT add explanations or comments
- Do NOT include phrases like "Here is..." or "I've made..."
- Output ONLY the edited text directly
- Start immediately with the edited content
- Edit ONLY the ORIGINAL VERSION. Do not add any extra text.

EDITED VERSION:
"""
            max_tokens = len(segment_text) + self.get_config("edit_selection_max_new_tokens", 15000)
            request = self.make_api_request(prompt, system_prompt, max_tokens, api_type=api_type)
            return request

        try:
            text_obj = text_range.getText()
            start = text_range.getStart()
            end = text_range.getEnd()
            old_len = len(original_text)
            base_char_style = ""
            base_para_style = ""
            try:
                base_char_style = text_range.getPropertyValue("CharStyleName")
            except Exception:
                pass
            try:
                base_para_style = text_range.getPropertyValue("ParaStyleName")
            except Exception:
                pass

            # Edit selection as a single block (no segmentation)
            # Retry once on empty result (handles 403 after fresh enrollment —
            # first attempt fails, we force a blocking config refresh to sync
            # the LLM token from the relay, then retry).
            _show_wait()
            for _attempt in range(2):
                accumulated_text = ""
                aborted["value"] = False
                request = _edit_segment(original_text)
                self.stream_request(request, api_type, append_text)
                if accumulated_text.strip() or cancelled["value"] or aborted["value"]:
                    break
                if _attempt == 0:
                    log_to_file("[edit] empty result on first attempt, forcing config refresh")
                    try:
                        self._fetch_config(force=True)
                    except Exception:
                        pass
                    # Verify token is now available — read directly from disk
                    try:
                        _cfg_path = os.path.join(self._get_user_config_dir(), "config.json")
                        with open(_cfg_path, "r", encoding="utf-8") as _f:
                            _disk = json.load(_f)
                        token_check = str(_disk.get("llm_api_tokens", "") or "").strip()
                    except Exception:
                        token_check = str(self._get_config_from_file("llm_api_tokens", "") or "").strip()
                    log_to_file(f"[edit] after refresh: llm_api_tokens={'present' if token_check else 'still empty'}")
                    if not token_check:
                        break  # no point retrying without a token
            _close_wait()
            if cancelled["value"]:
                return
            if aborted["value"]:
                self._show_message(
                    "Modification",
                    "Le modèle a tenté de poser une question. Reformulez la demande de manière plus directive."
                )
                return
            # Strip think/reasoning blocks (e.g. deepseek-r1)
            import re as _re
            accumulated_text = _re.sub(r"<think>.*?</think>", "", accumulated_text, flags=_re.DOTALL | _re.IGNORECASE)
            accumulated_text = _re.sub(r"^.*?</think>", "", accumulated_text, flags=_re.DOTALL | _re.IGNORECASE)
            accumulated_text = _re.sub(r"<think>.*$", "", accumulated_text, flags=_re.DOTALL | _re.IGNORECASE)
            accumulated_text = accumulated_text.strip()

            if not accumulated_text.strip():
                self._show_message(
                    "Modification",
                    "Aucune réponse reçue du modèle. Vérifiez le token et réessayez."
                )
                return

            new_len = len(accumulated_text)

            log_to_file(f"EditSelection insert: old_len={old_len} new_len={new_len} segments=1")
            # Delete original selection (if any)
            delete_cursor = text_obj.createTextCursorByRange(start)
            delete_cursor.gotoRange(end, True)
            delete_cursor.setString("")
            insert_point = delete_cursor.getStart()

            # Insert new text at cursor position
            insert_cursor = text_obj.createTextCursorByRange(insert_point)
            if original_text == "":
                try:
                    insert_cursor.setPropertyValue("CharStyleName", "Default")
                except Exception:
                    pass
                try:
                    insert_cursor.setPropertyValue("ParaStyleName", "Standard")
                except Exception:
                    pass
            doc = None
            try:
                doc = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx
                ).getCurrentComponent()
            except Exception:
                pass

            insert_formatted(doc, text_obj, insert_cursor, accumulated_text, base_char_style, base_para_style)
            log_to_file("EditSelection insert: done")

            # Reselect inserted text
            try:
                controller = doc.getCurrentController() if doc else None
                if controller:
                    sel_cursor = text_obj.createTextCursorByRange(insert_point)
                    sel_cursor.gotoRange(insert_cursor.getEnd(), True)
                    controller.select(sel_cursor)
            except Exception:
                pass
        except Exception as e:
            log_to_file(f"EditSelection insert failed: {str(e)}")

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
        dialog.setTitle("À propos de l'IA'ssistant MIrAI")
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

        # Logo — search in multiple locations
        logo_url = ""
        _candidates = [
            # Installed extension: entrypoint.py is at .../mirai.oxt/src/mirai/entrypoint.py
            # logo is at .../mirai.oxt/assets/logo.png
            os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png"),
            # Dev: from src/mirai/ → oxt/assets/
            os.path.join(os.path.dirname(__file__), "..", "..", "oxt", "assets", "logo.png"),
        ]
        for _lp in _candidates:
            _lp = os.path.normpath(_lp)
            if os.path.exists(_lp):
                logo_url = uno.systemPathToFileUrl(_lp)
                break

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
            {"Label": "MIrAI — IA'ssistant LibreOffice",
             "FontHeight": 16, "FontWeight": 200,
             "TextColor": _UI["primary"], "Align": 1})
        y += 26

        # Version
        version = self._get_extension_version() or "0.1.0"
        add("about_version", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 16,
            {"Label": f"Version {version}",
             "FontHeight": _UI["font_label"],
             "TextColor": _UI["text_secondary"], "Align": 1})
        y += 22

        # Separator
        add("about_sep1", "FixedLine",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 6, {})
        y += 12

        # Description (non-editable label, smaller text, white bg)
        desc_line1 = (
            "Extension LibreOffice intégrant un assistant IA dans Writer et Calc. "
            "Sélectionnez du texte et utilisez le menu MIrAI pour générer, modifier, "
            "résumer, reformuler ou ajuster la longueur de vos documents."
        )
        add("about_desc1", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 30,
            {"Label": desc_line1, "NoLabel": True, "MultiLine": True,
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"]})
        y += 32
        add("about_desc2", "FixedText",
            HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, 12,
            {"Label": "Programme MIrAI — Ministère de l'Intérieur", "NoLabel": True,
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
            {"Label": "Derniers ajouts",
             "FontHeight": _UI["font_section"], "FontWeight": 150,
             "TextColor": _UI["primary"]})
        y += 20

        changelog = (
            "• Ajuster la longueur — mini-dialogue − / + pour réduire ou développer\n"
            "• Suggestions IA contextuelles dans le dialogue d'édition\n"
            "• Analyse de plage Calc avec nettoyage markdown\n"
            "• Déploiement automatisé avec rollout progressif\n"
            "• Notice utilisateur double persona (novice / expert)\n"
            "• Filtrage robuste du raisonnement LLM (blocs <think>)"
        )
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
            "Label": "  Mises à jour",
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
            {"Label": "Fermer",
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"],
             "BackgroundColor": _UI["bg_section"]})

        # Diagnostic : ouvrir le dossier de mise à jour dans l'explorateur/Finder,
        # en natif (SystemShellExecute, sans cmd.exe) — même mécanisme que le bouton
        # de la boîte « mise à jour bloquée ». Utile pour tester localement (Mac inclus).
        btn_open_folder = add("about_btn_open_folder", "Button",
            (HORI_MARGIN + BTN_WIDTH + WIDTH - HORI_MARGIN - BTN_WIDTH) // 2 - 48,
            btn_y, 96, BTN_HEIGHT,
            {"Label": "Ouvrir dossier",
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
                            update_status.getModel().Label = "Vérification en cours..."
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
                                    pend = os.path.join(
                                        about_self._get_user_config_dir(), "pending_update"
                                    )
                                    os.makedirs(pend, exist_ok=True)
                                    open(os.path.join(pend, "mirai_update.oxt"), "a").close()
                                    if update_status:
                                        update_status.getModel().Label = (
                                            "Self-test : boîte « mise à jour bloquée »"
                                        )
                                        update_status.getModel().TextColor = _UI["info"]
                                    about_self._notify_update_blocked(
                                        "SELFTEST", os.path.join(pend, "mirai_update.bat")
                                    )
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
                                update_status.getModel().Label = f"Version {target} disponible. Mise à jour lancée..."
                                update_status.getModel().TextColor = _UI["info"]
                                # Wait for update to finish (max 60s)
                                for _ in range(120):
                                    time.sleep(0.5)
                                    if not MainJob._update_in_progress_cls:
                                        break
                                if MainJob._update_in_progress_cls:
                                    update_status.getModel().Label = f"Téléchargement de la v{target} en cours..."
                                    update_status.getModel().TextColor = _UI["info"]
                                else:
                                    new_ver = about_self._get_extension_version() or "?"
                                    if new_ver == target:
                                        update_status.getModel().Label = f"v{target} installée. Redémarrez LibreOffice."
                                        update_status.getModel().TextColor = _UI["success"]
                                    else:
                                        update_status.getModel().Label = f"Échec du téléchargement de la v{target}."
                                        update_status.getModel().TextColor = _UI["error"]
                            else:
                                current = about_self._get_extension_version() or "?"
                                update_status.getModel().Label = f"Version {current} — à jour."
                                update_status.getModel().TextColor = _UI["success"]
                        except Exception as e:
                            if update_status:
                                try:
                                    update_status.getModel().Label = f"Erreur : {str(e)[:50]}"
                                    update_status.getModel().TextColor = _UI["error"]
                                except Exception:
                                    pass
                    threading.Thread(target=_check_update_bg, daemon=True).start()
                elif source == btn_open_folder:
                    # Ouvre le dossier de MAJ en natif (Finder/Explorer, sans cmd.exe).
                    try:
                        folder = os.path.join(
                            about_self._get_user_config_dir(), "pending_update"
                        )
                        if not os.path.isdir(folder):
                            folder = about_self._get_user_config_dir()
                        ok = about_self._open_folder_native(folder)
                        if update_status:
                            update_status.getModel().Label = (
                                "Dossier ouvert." if ok
                                else "Impossible d'ouvrir le dossier."
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
                def mousePressed(self, e): return
                def mouseReleased(self, e): return
                def mouseEntered(self, e):
                    try: btn_update.getModel().BackgroundColor = _UI["primary_hover"]
                    except: pass
                def mouseExited(self, e):
                    try: btn_update.getModel().BackgroundColor = _UI["btn_primary_bg"]
                    except: pass
                def disposing(self, e): return
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
                except: pass
            def windowOpened(self, e): return
            def windowClosed(self, e): return
            def windowMinimized(self, e): return
            def windowNormalized(self, e): return
            def windowActivated(self, e): return
            def windowDeactivated(self, e): return
            def disposing(self, e): return

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

    def _show_resize_dialog(self, text, text_range, controller=None, model=None):
        """Mini floating dialog with − / + buttons to shrink or expand selected text."""
        # Singleton: reuse if already open
        if self._resize_dialog:
            try:
                self._resize_dialog.setVisible(True)
                return
            except Exception:
                self._resize_dialog = None

        from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE

        WIDTH = 320
        HORI_MARGIN = 14
        VERT_MARGIN = 12
        BTN_SIZE = 50
        BTN_GAP = 20
        LABEL_HEIGHT = 20
        PREVIEW_HEIGHT = 80
        HEIGHT = VERT_MARGIN * 2 + LABEL_HEIGHT + 8 + BTN_SIZE + 8 + PREVIEW_HEIGHT

        ctx = uno.getComponentContext()
        def create(name):
            return ctx.getServiceManager().createInstanceWithContext(name, ctx)

        dialog = create("com.sun.star.awt.UnoControlDialog")
        dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
        dialog.setModel(dialog_model)
        dialog.setVisible(False)
        dialog.setTitle("MIrAI — Ajuster la longueur")
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
        try:
            dialog_model.BackgroundColor = _UI["bg"]
        except Exception:
            pass
        try:
            dialog_model.Sizeable = False
        except Exception:
            pass
        try:
            dialog_model.Closeable = True
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

        # Status label
        status_label = add(
            "resize_status", "FixedText",
            HORI_MARGIN, VERT_MARGIN,
            WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT,
            {"Label": "Sélectionnez du texte puis cliquez − ou +",
             "NoLabel": True,
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"],
             "Align": 1,
            }
        )

        # − button (reduce)
        btn_y = VERT_MARGIN + LABEL_HEIGHT + 8
        center_x = WIDTH // 2
        btn_minus = add(
            "btn_resize_minus", "Button",
            center_x - BTN_SIZE - BTN_GAP // 2, btn_y,
            BTN_SIZE, BTN_SIZE,
            {"Label": "−",
             "FontHeight": 22,
             "FontWeight": 200,
             "TextColor": _UI["btn_primary_fg"],
             "BackgroundColor": _UI["btn_primary_bg"],
            }
        )
        # + button (expand)
        btn_plus = add(
            "btn_resize_plus", "Button",
            center_x + BTN_GAP // 2, btn_y,
            BTN_SIZE, BTN_SIZE,
            {"Label": "+",
             "FontHeight": 22,
             "FontWeight": 200,
             "TextColor": _UI["btn_primary_fg"],
             "BackgroundColor": _UI["btn_primary_bg"],
            }
        )

        # Preview area — shows streaming LLM output (including reasoning)
        preview_y = btn_y + BTN_SIZE + 8
        preview_control = add(
            "resize_preview", "Edit",
            HORI_MARGIN, preview_y,
            WIDTH - HORI_MARGIN * 2, PREVIEW_HEIGHT,
            {"Text": "", "MultiLine": True, "ReadOnly": True, "VScroll": True,
             "BackgroundColor": _UI["bg_section"],
             "FontHeight": 7,
             "TextColor": _UI["text_secondary"],
             "Border": 1,
             "BorderColor": _UI["border"],
            }
        )

        resize_self = self

        def _get_current_selection():
            """Grab the live selection from the document."""
            try:
                desktop = resize_self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", resize_self.ctx)
                doc = desktop.getCurrentComponent()
                if doc and hasattr(doc, "Text"):
                    sel = doc.CurrentController.getSelection()
                    if sel and sel.getCount() > 0:
                        return doc.Text, sel.getByIndex(0), doc.CurrentController, doc
            except Exception:
                pass
            return text, text_range, controller, model

        def _do_resize(direction):
            """Run the resize LLM call. direction: 'reduce' or 'expand'."""
            txt, rng, ctrl, mdl = _get_current_selection()
            original = rng.getString()
            if not original or len(original.strip()) < 5:
                if status_label:
                    try:
                        status_label.getModel().Label = "Sélectionnez du texte à ajuster."
                        status_label.getModel().TextColor = _UI["warning"]
                    except Exception:
                        pass
                return

            # Update status label
            if status_label:
                try:
                    label = "Mirai réduit..." if direction == "reduce" else "Mirai développe..."
                    status_label.getModel().Label = label
                    status_label.getModel().TextColor = _UI["primary"]
                except Exception:
                    pass

            word_count = len(original.split())

            if direction == "reduce":
                target_words = max(5, int(word_count * 0.65))
                system = (
                    "Tu DOIS répondre dans la MÊME LANGUE que le texte fourni. "
                    "Si le texte est en français, réponds en français. "
                    "Si le texte est en anglais, réponds en anglais.\n"
                    "Tu es un rédacteur professionnel. Tu raccourcis le texte fourni "
                    "en conservant le sens, le ton et les informations essentielles. "
                    f"Le texte original fait {word_count} mots. "
                    f"Tu DOIS produire un texte de {target_words} mots MAXIMUM. "
                    "Produis UNIQUEMENT le texte raccourci, "
                    "sans introduction, sans explication, sans commentaire, sans guillemets."
                )
                prompt = (
                    f"Raccourcis ce texte à {target_words} mots maximum "
                    f"(actuellement {word_count} mots) :\n\n"
                    f"{original}\n\n"
                    f"TEXTE RACCOURCI ({target_words} mots max) :"
                )
            else:
                target_words = int(word_count * 1.4)
                system = (
                    "Tu DOIS répondre dans la MÊME LANGUE que le texte fourni. "
                    "Si le texte est en français, réponds en français. "
                    "Si le texte est en anglais, réponds en anglais.\n"
                    "Tu es un rédacteur professionnel. Tu développes le texte fourni "
                    "en ajoutant des détails, des précisions ou des formulations plus "
                    "riches tout en conservant le sens et le ton. "
                    f"Le texte original fait {word_count} mots. "
                    f"Tu DOIS produire un texte d'environ {target_words} mots. "
                    "Produis UNIQUEMENT le texte développé, "
                    "sans introduction, sans explication, sans commentaire, sans guillemets."
                )
                prompt = (
                    f"Développe ce texte à environ {target_words} mots "
                    f"(actuellement {word_count} mots) :\n\n"
                    f"{original}\n\n"
                    f"TEXTE DÉVELOPPÉ (~{target_words} mots) :"
                )

            try:
                api_type = str(resize_self.get_config("api_type", "completions")).lower()
                max_tokens = int(resize_self.get_config("edit_selection_max_new_tokens", 15000))
                request = resize_self.make_api_request(prompt, system, max_tokens, api_type=api_type)
                accumulated = []
                in_think = [False]  # track whether we're inside a <think> block
                # Clear preview
                if preview_control:
                    try:
                        preview_control.getModel().Text = ""
                    except Exception:
                        pass
                def _collect(chunk):
                    accumulated.append(chunk)
                    full = "".join(accumulated)
                    # Detect <think> opening
                    if not in_think[0] and "<think>" in full.lower():
                        in_think[0] = True
                    # Detect </think> closing
                    if in_think[0] and "</think>" in full.lower():
                        in_think[0] = False
                    # Show raw stream in preview (including think for transparency)
                    if preview_control:
                        try:
                            preview_control.getModel().Text = full
                            # Auto-scroll to bottom
                            sel = uno.createUnoStruct("com.sun.star.awt.Selection")
                            sel.Min = len(full)
                            sel.Max = len(full)
                            preview_control.setSelection(sel)
                        except Exception:
                            pass
                resize_self.stream_request(request, api_type, _collect)
                raw = "".join(accumulated).strip()
                # Strip think/reasoning blocks before applying to document
                import re as _re
                # 1. Remove complete <think>…</think> blocks
                raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
                # 2. Remove everything up to and including a dangling </think>
                raw = _re.sub(r"^.*?</think>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
                # 3. Remove a trailing unclosed <think>… block
                raw = _re.sub(r"<think>.*$", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
                raw = raw.strip()
                log_to_file(f"ResizeSelection cleaned result ({len(raw)} chars): {raw[:200]!r}")

                # Show cleaned result in preview
                if preview_control:
                    try:
                        preview_control.getModel().Text = raw
                    except Exception:
                        pass

                if not raw:
                    if status_label:
                        try:
                            status_label.getModel().Label = "Aucun résultat. Réessayez."
                            status_label.getModel().TextColor = _UI["warning"]
                        except Exception:
                            pass
                    return

                # Replace the selection in-place with undo grouping
                undo_label = "Réduire" if direction == "reduce" else "Développer"
                mgr = None
                try:
                    mgr = mdl.getUndoManager()
                    mgr.enterUndoContext(undo_label)
                except Exception:
                    mgr = None
                try:
                    text_obj = rng.getText()
                    resize_cursor = text_obj.createTextCursorByRange(rng)
                    resize_cursor.setString("")
                    insert_point = resize_cursor.getStart()
                    insert_formatted(mdl, text_obj, resize_cursor, raw)
                    if ctrl:
                        try:
                            # Select the newly inserted text so user can resize again
                            sel_cursor = text_obj.createTextCursorByRange(insert_point)
                            sel_cursor.gotoRange(resize_cursor.getEnd(), True)
                            ctrl.select(sel_cursor)
                        except Exception:
                            pass
                finally:
                    if mgr:
                        try:
                            mgr.leaveUndoContext()
                        except Exception:
                            pass

                new_word_count = len(raw.split())
                delta = new_word_count - word_count
                sign = "+" if delta > 0 else ""
                if status_label:
                    try:
                        status_label.getModel().Label = f"OK ({new_word_count} mots, {sign}{delta}). Ctrl+Z pour annuler."
                        status_label.getModel().TextColor = _UI["success"]
                    except Exception:
                        pass
            except Exception as e:
                log_to_file(f"ResizeSelection failed: {str(e)}")
                if status_label:
                    try:
                        status_label.getModel().Label = f"Erreur : {str(e)[:60]}"
                        status_label.getModel().TextColor = _UI["error"]
                    except Exception:
                        pass

        class ResizeActionListener(unohelper.Base, XActionListener):
            def actionPerformed(self, event):
                source = getattr(event, "Source", None)
                if source == btn_minus:
                    _do_resize("reduce")
                elif source == btn_plus:
                    _do_resize("expand")
            def disposing(self, event):
                return

        listener = ResizeActionListener()
        if btn_minus:
            try:
                btn_minus.addActionListener(listener)
            except Exception:
                pass
        if btn_plus:
            try:
                btn_plus.addActionListener(listener)
            except Exception:
                pass

        # Rollover effects
        def _add_btn_rollover(control):
            if not control:
                return
            class _Rollover(unohelper.Base, XMouseListener):
                def mousePressed(self, event):
                    return
                def mouseReleased(self, event):
                    return
                def mouseEntered(self, event):
                    try:
                        control.getModel().BackgroundColor = _UI["primary_hover"]
                    except Exception:
                        pass
                def mouseExited(self, event):
                    try:
                        control.getModel().BackgroundColor = _UI["btn_primary_bg"]
                    except Exception:
                        pass
                def disposing(self, event):
                    return
            try:
                control.addMouseListener(_Rollover())
            except Exception:
                pass

        _add_btn_rollover(btn_minus)
        _add_btn_rollover(btn_plus)

        # Window close handler
        class ResizeWindowListener(unohelper.Base, XTopWindowListener):
            def __init__(self, outer):
                self.outer = outer
            def windowClosing(self, event):
                try:
                    dialog.setVisible(False)
                    dialog.dispose()
                except Exception:
                    pass
                self.outer._resize_dialog = None
            def windowOpened(self, event):
                return
            def windowClosed(self, event):
                return
            def windowMinimized(self, event):
                return
            def windowNormalized(self, event):
                return
            def windowActivated(self, event):
                return
            def windowDeactivated(self, event):
                return
            def disposing(self, event):
                return

        # Position and show
        frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
        window = frame.getContainerWindow() if frame else None
        dialog.createPeer(create("com.sun.star.awt.Toolkit"), window)
        if window:
            ps = window.getPosSize()
            _x = ps.Width - WIDTH - 40
            _y = ps.Height // 2 - HEIGHT // 2
            dialog.setPosSize(_x, _y, 0, 0, POS)

        try:
            peer = dialog.getPeer()
            if peer:
                peer.addTopWindowListener(ResizeWindowListener(self))
        except Exception:
            pass

        dialog.setVisible(True)
        self._resize_dialog = dialog

    def _show_edit_selection_dialog(self, text, text_range):
        if self._edit_dialog:
            try:
                self._edit_dialog.setVisible(True)
            except Exception:
                pass
            return

        current_selection = {"range": text_range}

        WIDTH = 740
        HORI_MARGIN = 14
        VERT_MARGIN = 12
        BUTTON_WIDTH = 140
        BUTTON_HEIGHT = 30
        HORI_SEP = 10
        VERT_SEP = 8
        LABEL_HEIGHT = 22
        EDIT_HEIGHT = 120
        SUGGEST_LABEL_HEIGHT = 18
        SUGGEST_LIST_HEIGHT = 120
        SUGGEST_BTN_WIDTH = int((WIDTH - HORI_MARGIN * 2 - HORI_SEP) / 2)
        HEIGHT = (
            VERT_MARGIN * 2
            + LABEL_HEIGHT + VERT_SEP
            + EDIT_HEIGHT + VERT_SEP
            + BUTTON_HEIGHT + VERT_SEP
            + SUGGEST_LABEL_HEIGHT + VERT_SEP
            + SUGGEST_LIST_HEIGHT + VERT_SEP
            + BUTTON_HEIGHT
        )

        from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
        ctx = uno.getComponentContext()
        def create(name):
            return ctx.getServiceManager().createInstanceWithContext(name, ctx)

        dialog = create("com.sun.star.awt.UnoControlDialog")
        dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
        dialog.setModel(dialog_model)
        dialog.setVisible(False)
        dialog.setTitle("MIrAI — Modifier la sélection")
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
        try:
            dialog_model.BackgroundColor = _UI["bg"]
        except Exception:
            pass
        try:
            dialog_model.AlwaysOnTop = True
        except Exception:
            pass
        try:
            dialog_model.Sizeable = True
        except Exception:
            pass
        try:
            dialog_model.Closeable = True
        except Exception:
            pass

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

        def _refresh_selection_range():
            try:
                desktop = self.ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx)
                model = desktop.getCurrentComponent()
                if model is None or not hasattr(model, "Text"):
                    return
                selection = model.CurrentController.getSelection()
                if selection and selection.getCount() > 0:
                    current_selection["range"] = selection.getByIndex(0)
            except Exception:
                pass

        def _has_multiple_styles():
            try:
                selected = current_selection["range"].getString()
            except Exception:
                return False
            if not selected:
                return False
            try:
                text_obj = current_selection["range"].getText()
                cursor = text_obj.createTextCursorByRange(current_selection["range"].getStart())
                cursor.goRight(1, True)
                try:
                    base_char = cursor.getPropertyValue("CharStyleName")
                except Exception:
                    base_char = ""
                cursor.collapseToEnd()
                max_scan = min(len(selected), 2000)
                for _ in range(max_scan):
                    cursor.goRight(1, True)
                    try:
                        char_style = cursor.getPropertyValue("CharStyleName")
                    except Exception:
                        char_style = base_char
                    cursor.collapseToEnd()
                    if char_style != base_char:
                        return True
            except Exception:
                return False
            return False

        def _selection_info():
            _refresh_selection_range()
            try:
                selected = current_selection["range"].getString()
            except Exception:
                selected = ""
            if not selected:
                return "Sélectionner une portion de texte à modifier... ou placer le curseur à l'emplacement où vous souhaitez insérer le nouveau texte"
            snippet = " ".join(selected.split())
            max_len = 90
            if len(snippet) > max_len:
                head_len = (max_len - 9) // 2
                tail_len = max_len - 9 - head_len
                head = snippet[:head_len].rsplit(" ", 1)[0] or snippet[:head_len]
                tail = snippet[-tail_len:].split(" ", 1)[-1] or snippet[-tail_len:]
                snippet = head.rstrip() + " ... ... ... " + tail.lstrip()
            warning = " ⚠ plusieurs styles fusionnés" if _has_multiple_styles() else ""
            return f"Sélection {snippet}{warning}"

        PROMPT_BTN_WIDTH = 150
        label_max_width = WIDTH - HORI_MARGIN * 2 - PROMPT_BTN_WIDTH - HORI_SEP
        add("label_edit", "FixedText", HORI_MARGIN, VERT_MARGIN, label_max_width, LABEL_HEIGHT, {
            "Label": "Editer avec l'IA", "NoLabel": True,
            "FontHeight": _UI["font_section"],
            "TextColor": _UI["primary"],
            "FontWeight": 150,
        })
        OFFSET_BELOW = 20
        selection_width = label_max_width
        label_selection_control = add(
            "label_selection_info",
            "FixedText",
            HORI_MARGIN,
            VERT_MARGIN + LABEL_HEIGHT - 6 + OFFSET_BELOW,
            selection_width,
            SUGGEST_LABEL_HEIGHT,
            {"Label": _selection_info(), "NoLabel": True,
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_light"]}
        )
        if label_selection_control:
            try:
                if _has_multiple_styles():
                    label_selection_control.getModel().TextColor = _UI["warning"]
                else:
                    label_selection_control.getModel().TextColor = _UI["text_light"]
            except Exception:
                pass
        edit_control = add("edit_prompt", "Edit", HORI_MARGIN, VERT_MARGIN + LABEL_HEIGHT + VERT_SEP + OFFSET_BELOW,
            WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
                "Text": "", "MultiLine": True,
                "BackgroundColor": _UI["bg_input"],
                "FontHeight": _UI["font_label"],
            })
        if edit_control:
            try:
                edit_control.getModel().BackgroundColor = _UI["bg_section"]
            except Exception:
                pass

        send_y = VERT_MARGIN + LABEL_HEIGHT + VERT_SEP + OFFSET_BELOW + EDIT_HEIGHT + VERT_SEP

        # Mascot icon paths (shared by buttons)
        _mascot_path = os.path.join(os.path.dirname(__file__), "icons", "mascot16.png")
        _mascot_hover_path = os.path.join(os.path.dirname(__file__), "icons", "mascot16_hover.png")
        _mascot_url = ""
        _mascot_hover_url = ""
        try:
            if os.path.exists(_mascot_path):
                _mascot_url = uno.systemPathToFileUrl(_mascot_path)
            if os.path.exists(_mascot_hover_path):
                _mascot_hover_url = uno.systemPathToFileUrl(_mascot_hover_path)
        except Exception:
            pass

        def _add_rollover(control, normal_bg, hover_bg, icon_url="", icon_hover_url=""):
            """Attach a mouse listener for rollover effect on a button."""
            if not control:
                return
            class _RolloverListener(unohelper.Base, XMouseListener):
                def mousePressed(self, event):
                    return
                def mouseReleased(self, event):
                    return
                def mouseEntered(self, event):
                    try:
                        m = control.getModel()
                        m.BackgroundColor = hover_bg
                        m.FontWeight = 200
                        if icon_hover_url:
                            m.ImageURL = icon_hover_url
                    except Exception:
                        pass
                def mouseExited(self, event):
                    try:
                        m = control.getModel()
                        m.BackgroundColor = normal_bg
                        m.FontWeight = 150
                        if icon_url:
                            m.ImageURL = icon_url
                    except Exception:
                        pass
                def disposing(self, event):
                    return
            try:
                control.addMouseListener(_RolloverListener())
            except Exception:
                pass

        # Send button with mascot icon
        send_btn_props = {
            "Label": "  Envoyer",
            "FontHeight": _UI["font_label"],
            "FontWeight": 150,
            "TextColor": _UI["btn_primary_fg"],
            "BackgroundColor": _UI["btn_primary_bg"],
        }
        if _mascot_url:
            send_btn_props["ImageURL"] = _mascot_url
            send_btn_props["ImagePosition"] = 0
            send_btn_props["ImageAlign"] = 0
        btn_send = add(
            "btn_send",
            "Button",
            WIDTH - HORI_MARGIN - BUTTON_WIDTH,
            send_y,
            BUTTON_WIDTH,
            BUTTON_HEIGHT + 4,
            send_btn_props,
        )
        _add_rollover(btn_send, _UI["btn_primary_bg"], _UI["primary_hover"],
                       _mascot_url, _mascot_hover_url)

        suggest_y = send_y + BUTTON_HEIGHT + VERT_SEP + 4
        add(
            "line_suggestions",
            "FixedLine",
            HORI_MARGIN,
            suggest_y - (VERT_SEP // 2),
            WIDTH - HORI_MARGIN * 2,
            6,
            {}
        )
        label_suggestions_control = add(
            "label_suggestions",
            "FixedText",
            HORI_MARGIN,
            suggest_y + 12,
            WIDTH - HORI_MARGIN * 2,
            SUGGEST_LABEL_HEIGHT,
            {"Label": "Suggestions...", "NoLabel": True,
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_secondary"],
             "FontSlant": 2,
            }
        )
        suggest_y += SUGGEST_LABEL_HEIGHT + VERT_SEP + 5

        # Visible list (not dropdown) — shows all suggestions at once
        REGEN_BTN_WIDTH = 180
        suggestions_list = add(
            "list_suggestions",
            "ListBox",
            HORI_MARGIN,
            suggest_y,
            WIDTH - HORI_MARGIN * 2 - REGEN_BTN_WIDTH - HORI_SEP,
            SUGGEST_LIST_HEIGHT,
            {"Dropdown": False,
             "BackgroundColor": _UI["bg_section"],
             "FontHeight": _UI["font_small"],
             "TextColor": _UI["text_light"],
             "Border": 1,
             "BorderColor": _UI["border"],
            }
        )

        # Regen button aligned to the right of the list, with mascot
        regen_props = {
            "Label": "  Nouvelles suggestions",
            "FontHeight": _UI["font_small"],
            "FontWeight": 150,
            "TextColor": _UI["text_secondary"],
            "BackgroundColor": _UI["bg_section"],
        }
        if _mascot_url:
            regen_props["ImageURL"] = _mascot_url
            regen_props["ImagePosition"] = 0
            regen_props["ImageAlign"] = 0
        btn_regen_suggestions = add(
            "btn_regen_suggestions",
            "Button",
            WIDTH - HORI_MARGIN - REGEN_BTN_WIDTH,
            suggest_y,
            REGEN_BTN_WIDTH,
            BUTTON_HEIGHT,
            regen_props,
        )
        _add_rollover(btn_regen_suggestions, _UI["bg_section"], _UI["bg_accent"],
                       _mascot_url, _mascot_hover_url)

        # Rollover on the list: highlight selected item color
        if suggestions_list:
            class SuggestionsMouseListener(unohelper.Base, XMouseListener):
                def mousePressed(self, event):
                    return
                def mouseReleased(self, event):
                    return
                def mouseEntered(self, event):
                    try:
                        suggestions_list.getModel().BackgroundColor = _UI["bg_accent"]
                    except Exception:
                        pass
                def mouseExited(self, event):
                    try:
                        suggestions_list.getModel().BackgroundColor = _UI["bg_section"]
                    except Exception:
                        pass
                def disposing(self, event):
                    return
            try:
                suggestions_list.addMouseListener(SuggestionsMouseListener())
            except Exception:
                pass

        link_control = add(
            "link_prompt_file",
            "Button",
            WIDTH - HORI_MARGIN - PROMPT_BTN_WIDTH,
            VERT_MARGIN + 4,
            PROMPT_BTN_WIDTH,
            LABEL_HEIGHT,
            {"Label": "Ouvrir prompt.txt",
             "FontHeight": _UI["font_small"],
             "Tabstop": True,
             "TextColor": _UI["text_secondary"],
            }
        )
        if link_control is None:
            log_to_file("Open prompt button not created")

        frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
        window = frame.getContainerWindow() if frame else None
        dialog.createPeer(create("com.sun.star.awt.Toolkit"), window)
        if window:
            ps = window.getPosSize()
            saved_x = self.get_config("edit_dialog_x", None)
            saved_y = self.get_config("edit_dialog_y", None)
            if isinstance(saved_x, (int, float)) and isinstance(saved_y, (int, float)):
                _x = int(saved_x)
                _y = int(saved_y)
            else:
                _x = ps.Width / 2 - WIDTH / 2
                _y = ps.Height / 2 - HEIGHT / 2
            dialog.setPosSize(_x, _y, 0, 0, POS)

        def _extract_snippet(text_value, limit=180):
            value = " ".join((text_value or "").split())
            return value[:limit].rstrip()

        _FALLBACK_PROMPTS = [
            "Corrige l’orthographe et la grammaire.",
            "Reformule en style formel et concis.",
            "Simplifie pour un public non spécialiste.",
            "Rends le texte plus clair avec des phrases courtes.",
            "Transforme en style administratif.",
            "Rends la formulation plus positive et professionnelle.",
            "Réorganise pour améliorer la logique et la structure.",
            "Supprime les répétitions et les tournures lourdes.",
            "Rends le texte plus convaincant sans changer le sens.",
            "Résume le contenu en gardant l’essentiel.",
        ]

        def _generate_prompt_suggestions(text_value):
            """Generate contextual suggestions via the LLM, fallback to static list."""
            snippet = _extract_snippet(text_value, limit=1500)
            if not snippet or len(snippet.strip()) < 10:
                return list(_FALLBACK_PROMPTS)
            try:
                system = (
                    "LANGUE OBLIGATOIRE : français. Tu ne dois JAMAIS répondre en anglais "
                    "ni dans aucune autre langue que le français.\n"
                    "Tu es un assistant qui propose des instructions d’édition de texte. "
                    "Réponds UNIQUEMENT avec une liste numérotée de 8 instructions courtes "
                    "en français (une par ligne, format: ‘1. instruction’). "
                    "Chaque instruction doit être une consigne d’édition concrète et directe "
                    "(verbe à l’impératif en français). "
                    "Adapte les suggestions au contenu, au style et au domaine du texte. "
                    "Ne répète pas le texte. Pas de commentaire. Pas d’explication."
                )
                prompt = (
                    f"Voici un extrait de texte sélectionné par l’utilisateur :\n\n"
                    f"«{snippet}»\n\n"
                    f"Propose 8 instructions d’édition pertinentes pour ce texte.\n\n"
                    f"Exemple de format attendu :\n"
                    f"1. Corrige les fautes d’orthographe et de grammaire.\n"
                    f"2. Reformule en style plus concis.\n"
                    f"3. Simplifie le vocabulaire technique.\n\n"
                    f"Tes 8 instructions en français :"
                )
                api_type = str(self.get_config("api_type", "completions")).lower()
                # Use non-streaming HTTP call — this runs in a background thread
                # and stream_request must NOT be called from background threads
                # (processEventsToIdle crashes LibreOffice).
                request = self.make_api_request(prompt, system, max_tokens=600, api_type=api_type)
                # Override stream=false for a synchronous call
                import copy as _copy
                req_data = json.loads(request.data.decode("utf-8"))
                req_data["stream"] = False
                request.data = json.dumps(req_data).encode("utf-8")
                try:
                    ssl_ctx = self.get_ssl_context()
                    timeout = int(self.get_config("llm_request_timeout_seconds", 45))
                    with self._urlopen(request, context=ssl_ctx, timeout=timeout) as resp:
                        body = resp.read().decode("utf-8")
                    result = json.loads(body)
                    choices = result.get("choices", [])
                    raw = ""
                    if choices:
                        raw = choices[0].get("message", {}).get("content", "")
                except Exception as e:
                    log_to_file(f"AI suggestions HTTP error: {e}")
                    raw = ""
                raw = raw.strip()
                if not raw:
                    return list(_FALLBACK_PROMPTS)
                # Strip chain-of-thought blocks (<think>…</think>)
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).lstrip("\n")
                # Parse numbered lines only: "1. ...", "2. ...", etc.
                # This filters out reasoning/thinking text the model may produce.
                lines = []
                for line in raw.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if not re.match(r"^\d+[\.\)\-]\s", line):
                        continue
                    # Remove leading number + dot/parenthesis
                    cleaned = re.sub(r"^\d+[\.\)\-]\s*", "", line).strip()
                    if cleaned and len(cleaned) > 5:
                        lines.append(cleaned)
                if len(lines) >= 3:
                    log_to_file(f"AI suggestions generated: {len(lines)} items")
                    return lines[:10]
                log_to_file(f"AI suggestions too few ({len(lines)}), using fallback")
                return list(_FALLBACK_PROMPTS)
            except Exception as e:
                log_to_file(f"AI suggestion generation failed: {str(e)}")
                return list(_FALLBACK_PROMPTS)

        # Loading animation state
        _loading_anim = {"active": False, "thread": None}

        def _start_loading_animation():
            """Animate the suggestions label while LLM generates."""
            _loading_anim["active"] = True
            frames = [
                "Mirai prépare des suggestions",
                "Mirai prépare des suggestions .",
                "Mirai prépare des suggestions . .",
                "Mirai prépare des suggestions . . .",
            ]
            def _animate():
                idx = 0
                while _loading_anim["active"]:
                    try:
                        if label_suggestions_control:
                            label_suggestions_control.getModel().Label = frames[idx % len(frames)]
                    except Exception:
                        break
                    idx += 1
                    import time
                    time.sleep(0.5)
                # Restore default label when done
                try:
                    if label_suggestions_control:
                        label_suggestions_control.getModel().Label = "Suggestions"
                except Exception:
                    pass
            t = threading.Thread(target=_animate, daemon=True)
            _loading_anim["thread"] = t
            t.start()

        def _stop_loading_animation():
            """Stop the loading animation."""
            _loading_anim["active"] = False

        def _load_suggestions(use_ai=False):
            """Load suggestions into the list. use_ai=True triggers LLM generation."""
            if suggestions_list:
                try:
                    suggestions_list.removeItems(0, suggestions_list.getItemCount())
                except Exception:
                    pass
            if use_ai:
                # Show loading animation
                _start_loading_animation()
                if suggestions_list:
                    try:
                        suggestions_list.addItems(("Génération en cours...",), 0)
                    except Exception:
                        pass
                text_value = ""
                try:
                    _refresh_selection_range()
                    text_value = current_selection["range"].getString()
                except Exception:
                    pass
                # If no selection, grab document body (capped for LLM context)
                if not text_value or len(text_value.strip()) < 10:
                    try:
                        desktop = self.ctx.ServiceManager.createInstanceWithContext(
                            "com.sun.star.frame.Desktop", self.ctx)
                        doc = desktop.getCurrentComponent()
                        if doc and hasattr(doc, "Text"):
                            full_text = doc.Text.getString()
                            # Cap at ~2000 chars to stay within LLM context
                            if len(full_text) > 2000:
                                text_value = full_text[:1000] + "\n[...]\n" + full_text[-800:]
                            else:
                                text_value = full_text
                            log_to_file(f"Suggestions: no selection, using document body ({len(full_text)} chars)")
                    except Exception as e:
                        log_to_file(f"Suggestions: failed to read document body: {str(e)}")
                suggestions = _generate_prompt_suggestions(text_value)
                _stop_loading_animation()
            else:
                suggestions = list(_FALLBACK_PROMPTS)
            if suggestions_list:
                try:
                    suggestions_list.removeItems(0, suggestions_list.getItemCount())
                except Exception:
                    pass
                if suggestions:
                    try:
                        suggestions_list.addItems(tuple(suggestions), 0)
                    except Exception:
                        pass

        # Show static suggestions immediately, then generate AI suggestions in background
        _load_suggestions(use_ai=False)
        def _bg_load_ai_suggestions():
            try:
                _load_suggestions(use_ai=True)
            except Exception:
                _stop_loading_animation()
        threading.Thread(target=_bg_load_ai_suggestions, daemon=True).start()

        def _refresh_selection_label():
            if label_selection_control:
                try:
                    label_selection_control.getModel().Label = _selection_info()
                    if _has_multiple_styles():
                        label_selection_control.getModel().TextColor = _UI["warning"]
                    else:
                        label_selection_control.getModel().TextColor = _UI["text_light"]
                except Exception:
                    pass

        class SuggestionsItemListener(unohelper.Base, XItemListener):
            def __init__(self, outer):
                self.outer = outer
            def itemStateChanged(self, event):
                try:
                    suggestion = suggestions_list.getSelectedItem() if suggestions_list else ""
                    if suggestion:
                        edit_control.getModel().Text = suggestion
                except Exception:
                    pass
            def disposing(self, event):
                return

        class EditDialogListener(unohelper.Base, XActionListener):
            def actionPerformed(self, event):
                source = getattr(event, "Source", None)
                if source == btn_send:
                    _refresh_selection_range()
                    try:
                        user_input = edit_control.getModel().Text.strip()
                    except Exception:
                        user_input = ""
                    if not user_input:
                        return
                    try:
                        self.outer._run_edit_selection(text, current_selection["range"], user_input)
                    except Exception as e:
                        log_to_file(f"EditSelection dialog failed: {str(e)}")
                elif source == btn_regen_suggestions:
                    _refresh_selection_label()
                    _load_suggestions(use_ai=True)

            def __init__(self, outer):
                self.outer = outer

            def disposing(self, event):
                return

        listener = EditDialogListener(self)
        if btn_send:
            try:
                btn_send.addActionListener(listener)
            except Exception:
                pass
        if btn_regen_suggestions:
            try:
                btn_regen_suggestions.addActionListener(listener)
            except Exception:
                pass
        if suggestions_list:
            try:
                self._suggestions_item_listener = SuggestionsItemListener(self)
                suggestions_list.addItemListener(self._suggestions_item_listener)
            except Exception:
                pass

        class EditDialogWindowListener(unohelper.Base, XWindowListener):
            def __init__(self, outer):
                self.outer = outer
            def _save_pos(self):
                try:
                    ps = dialog.getPosSize()
                    self.outer.set_config("edit_dialog_x", int(ps.X))
                    self.outer.set_config("edit_dialog_y", int(ps.Y))
                except Exception:
                    pass
            def windowClosing(self, event):
                try:
                    self._save_pos()
                    dialog.setVisible(False)
                    dialog.dispose()
                except Exception:
                    pass
                self.outer._edit_dialog = None
            def windowOpened(self, event):
                return
            def windowClosed(self, event):
                return
            def windowMinimized(self, event):
                return
            def windowNormalized(self, event):
                return
            def windowActivated(self, event):
                _refresh_selection_label()
            def windowDeactivated(self, event):
                return
            def disposing(self, event):
                return

        class EditDialogTopWindowListener(unohelper.Base, XTopWindowListener):
            def __init__(self, outer):
                self.outer = outer
            def _save_pos(self):
                try:
                    ps = dialog.getPosSize()
                    self.outer.set_config("edit_dialog_x", int(ps.X))
                    self.outer.set_config("edit_dialog_y", int(ps.Y))
                except Exception:
                    pass
            def windowClosing(self, event):
                try:
                    self._save_pos()
                    dialog.setVisible(False)
                    dialog.dispose()
                except Exception:
                    pass
                self.outer._edit_dialog = None
            def windowOpened(self, event):
                return
            def windowClosed(self, event):
                return
            def windowMinimized(self, event):
                return
            def windowNormalized(self, event):
                return
            def windowActivated(self, event):
                _refresh_selection_label()
            def windowDeactivated(self, event):
                return
            def disposing(self, event):
                return

        try:
            dialog.addWindowListener(EditDialogWindowListener(self))
        except Exception:
            pass
        try:
            peer = dialog.getPeer()
            if peer:
                peer.addTopWindowListener(EditDialogTopWindowListener(self))
        except Exception:
            pass

        class PromptLinkActionListener(unohelper.Base, XActionListener):
            def __init__(self, outer):
                self.outer = outer
            def actionPerformed(self, event):
                try:
                    path_settings = self.outer.sm.createInstanceWithContext(
                        "com.sun.star.util.PathSettings", self.outer.ctx
                    )
                    user_config_path = getattr(path_settings, "UserConfig")
                    if user_config_path.startswith("file://") or user_config_path.startswith("file:"):
                        user_config_path = str(uno.fileUrlToSystemPath(user_config_path))
                    prompt_log_path = os.path.join(user_config_path, "prompt.txt")
                    if not os.path.exists(prompt_log_path):
                        with open(prompt_log_path, "a", encoding="utf-8") as f:
                            f.write("")
                    prompt_url = uno.systemPathToFileUrl(prompt_log_path)
                    shell = self.outer.ctx.getServiceManager().createInstanceWithContext(
                        "com.sun.star.system.SystemShellExecute", self.outer.ctx
                    )
                    shell.execute(prompt_url, "", 0)
                except Exception as e:
                    log_to_file(f"Failed to open prompt.txt: {str(e)}")
            def disposing(self, event):
                return

        if link_control:
            try:
                self._prompt_link_action_listener = PromptLinkActionListener(self)
                link_control.addActionListener(self._prompt_link_action_listener)
            except Exception:
                pass

        dialog.setVisible(True)
        self._edit_dialog = dialog

        def _selection_refresh_loop():
            while True:
                try:
                    if self._edit_dialog is None or not dialog.isVisible():
                        break
                except Exception:
                    break
                _refresh_selection_label()
                time.sleep(3)

        try:
            threading.Thread(target=_selection_refresh_loop, daemon=True).start()
        except Exception:
            pass

    # ── Calc-specific static suggestions (transform instructions) ──────────
    _FALLBACK_CALC_TRANSFORM_PROMPTS = [
        "Traduire en anglais",
        "Mettre la première lettre en majuscule",
        "Résumer en une phrase courte",
        "Extraire les mots-clés (séparés par des virgules)",
        "Classifier comme Positif / Négatif / Neutre",
        "Corriger l'orthographe et la grammaire",
        "Normaliser le format (ex: prénom nom → PRÉNOM NOM)",
        "Extraire le premier nombre trouvé",
        "Détecter la langue (ex: FR / EN / DE)",
        "Reformuler de façon plus formelle",
    ]

    def _show_calc_input_dialog(self, context_label="", title="MIrAI — Transformer les cellules", ok_label="Transformer", cell_content="") -> str:
        """DSFR-styled modal input dialog for Calc actions.

        Mirrors the visual structure of _show_edit_selection_dialog:
        section header in primary blue, selection-info label, text area,
        suggestions list with click-to-fill, Send + Close buttons.

        Returns the instruction string entered by the user, or "" on cancel.
        """
        WIDTH = 740
        HORI_MARGIN = 14
        VERT_MARGIN = 12
        BUTTON_WIDTH = 140
        BUTTON_HEIGHT = 30
        HORI_SEP = 10
        VERT_SEP = 8
        LABEL_HEIGHT = 22
        EDIT_HEIGHT = 120
        SUGGEST_LABEL_HEIGHT = 18
        SUGGEST_LIST_HEIGHT = 120
        REGEN_BTN_WIDTH = 180
        HEIGHT = (
            VERT_MARGIN * 2
            + LABEL_HEIGHT + VERT_SEP
            + EDIT_HEIGHT + VERT_SEP
            + BUTTON_HEIGHT + VERT_SEP
            + SUGGEST_LABEL_HEIGHT + VERT_SEP
            + SUGGEST_LIST_HEIGHT + VERT_MARGIN
        )

        from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
        ctx = uno.getComponentContext()
        def create(name):
            return ctx.getServiceManager().createInstanceWithContext(name, ctx)

        dialog = create("com.sun.star.awt.UnoControlDialog")
        dialog_model = create("com.sun.star.awt.UnoControlDialogModel")
        dialog.setModel(dialog_model)
        dialog.setVisible(False)
        dialog.setTitle(title)
        dialog.setPosSize(0, 0, WIDTH, HEIGHT, SIZE)
        try:
            dialog_model.BackgroundColor = _UI["bg"]
        except Exception:
            pass
        try:
            dialog_model.AlwaysOnTop = True
        except Exception:
            pass
        try:
            dialog_model.Sizeable = True
        except Exception:
            pass
        try:
            dialog_model.Closeable = True
        except Exception:
            pass

        def add(name, ctrl_type, x_, y_, width_, height_, props):
            try:
                m = dialog_model.createInstance("com.sun.star.awt.UnoControl" + ctrl_type + "Model")
            except Exception as e:
                log_to_file(f"_show_calc_input_dialog: unsupported control {name}/{ctrl_type}: {e}")
                return None
            try:
                dialog_model.insertByName(name, m)
            except Exception as e:
                log_to_file(f"_show_calc_input_dialog: insert failed {name}: {e}")
                return None
            ctrl = dialog.getControl(name)
            try:
                ctrl.setPosSize(x_, y_, width_, height_, POSSIZE)
            except Exception:
                pass
            for k, v in props.items():
                try:
                    setattr(m, k, v)
                except Exception:
                    pass
            return ctrl

        OFFSET_BELOW = 20
        label_max_width = WIDTH - HORI_MARGIN * 2

        # Section header
        add("label_title", "FixedText", HORI_MARGIN, VERT_MARGIN, label_max_width, LABEL_HEIGHT, {
            "Label": ok_label + " les cellules", "NoLabel": True,
            "FontHeight": _UI["font_section"],
            "TextColor": _UI["primary"],
            "FontWeight": 150,
        })

        # Selection-info label (cell range + count)
        add("label_context", "FixedText",
            HORI_MARGIN, VERT_MARGIN + LABEL_HEIGHT - 6 + OFFSET_BELOW,
            label_max_width, SUGGEST_LABEL_HEIGHT, {
            "Label": context_label, "NoLabel": True,
            "FontHeight": _UI["font_small"],
            "TextColor": _UI["text_light"],
        })

        # Instruction edit area
        edit_y = VERT_MARGIN + LABEL_HEIGHT + VERT_SEP + OFFSET_BELOW
        edit_control = add("edit_instruction", "Edit",
            HORI_MARGIN, edit_y, WIDTH - HORI_MARGIN * 2, EDIT_HEIGHT, {
            "Text": "", "MultiLine": True,
            "BackgroundColor": _UI["bg_section"],
            "FontHeight": _UI["font_label"],
        })

        # Send button with mascot icon
        send_y = edit_y + EDIT_HEIGHT + VERT_SEP
        _mascot_path = os.path.join(os.path.dirname(__file__), "icons", "mascot16.png")
        _mascot_hover_path = os.path.join(os.path.dirname(__file__), "icons", "mascot16_hover.png")
        _mascot_url = ""
        _mascot_hover_url = ""
        try:
            if os.path.exists(_mascot_path):
                _mascot_url = uno.systemPathToFileUrl(_mascot_path)
            if os.path.exists(_mascot_hover_path):
                _mascot_hover_url = uno.systemPathToFileUrl(_mascot_hover_path)
        except Exception:
            pass

        send_btn_props = {
            "Label": f"  {ok_label}",
            "FontHeight": _UI["font_label"],
            "FontWeight": 150,
            "TextColor": _UI["btn_primary_fg"],
            "BackgroundColor": _UI["btn_primary_bg"],
        }
        if _mascot_url:
            send_btn_props["ImageURL"] = _mascot_url
            send_btn_props["ImagePosition"] = 0
            send_btn_props["ImageAlign"] = 0
        btn_send = add("btn_send", "Button",
            WIDTH - HORI_MARGIN - BUTTON_WIDTH, send_y,
            BUTTON_WIDTH, BUTTON_HEIGHT + 4, send_btn_props)

        def _add_rollover(ctrl, normal_bg, hover_bg, icon_url="", icon_hover_url=""):
            if not ctrl:
                return
            class _RL(unohelper.Base, XMouseListener):
                def mousePressed(self, e): return
                def mouseReleased(self, e): return
                def mouseEntered(self, e):
                    try:
                        m = ctrl.getModel()
                        m.BackgroundColor = hover_bg
                        m.FontWeight = 200
                        if icon_hover_url:
                            m.ImageURL = icon_hover_url
                    except Exception:
                        pass
                def mouseExited(self, e):
                    try:
                        m = ctrl.getModel()
                        m.BackgroundColor = normal_bg
                        m.FontWeight = 150
                        if icon_url:
                            m.ImageURL = icon_url
                    except Exception:
                        pass
                def disposing(self, e): return
            try:
                ctrl.addMouseListener(_RL())
            except Exception:
                pass

        _add_rollover(btn_send, _UI["btn_primary_bg"], _UI["primary_hover"],
                      _mascot_url, _mascot_hover_url)

        # Separator + suggestions
        suggest_y = send_y + BUTTON_HEIGHT + VERT_SEP + 4
        add("line_sep", "FixedLine",
            HORI_MARGIN, suggest_y - VERT_SEP // 2, WIDTH - HORI_MARGIN * 2, 6, {})
        add("label_suggestions", "FixedText",
            HORI_MARGIN, suggest_y + 12, WIDTH - HORI_MARGIN * 2, SUGGEST_LABEL_HEIGHT, {
            "Label": "Suggestions...", "NoLabel": True,
            "FontHeight": _UI["font_small"],
            "TextColor": _UI["text_secondary"],
            "FontSlant": 2,
        })
        suggest_y += SUGGEST_LABEL_HEIGHT + VERT_SEP + 5

        suggestions_list = add("list_suggestions", "ListBox",
            HORI_MARGIN, suggest_y,
            WIDTH - HORI_MARGIN * 2 - REGEN_BTN_WIDTH - HORI_SEP, SUGGEST_LIST_HEIGHT, {
            "Dropdown": False,
            "BackgroundColor": _UI["bg_section"],
            "FontHeight": _UI["font_small"],
            "TextColor": _UI["text_light"],
            "Border": 1,
            "BorderColor": _UI["border"],
        })
        def _generate_calc_suggestions(content):
            """Generate contextual Calc transform suggestions via LLM, fallback to static list."""
            if not content or len(content.strip()) < 3:
                return list(self._FALLBACK_CALC_TRANSFORM_PROMPTS)
            try:
                system = (
                    "Tu es un assistant de transformation de données pour un tableur. "
                    "Réponds UNIQUEMENT avec une liste numérotée de 8 transformations courtes "
                    "(une par ligne, format: '1. transformation'). "
                    "Chaque transformation doit être une consigne concrète commençant par un verbe "
                    "à l'impératif, adaptée au type et au contenu des cellules fournies. "
                    "Pas de commentaire, pas d'explication."
                )
                prompt = (
                    "Voici des exemples de valeurs des cellules sélectionnées :\n\n"
                    f"«{content[:500]}»\n\n"
                    "Propose 8 transformations pertinentes pour ces données."
                )
                api_type = str(self.get_config("api_type", "completions")).lower()
                request = self.make_api_request(prompt, system, max_tokens=400, api_type=api_type)
                accumulated = []
                def _collect(chunk):
                    accumulated.append(chunk)
                self.stream_request(request, api_type, _collect)
                raw = "".join(accumulated).strip()
                if not raw:
                    return list(self._FALLBACK_CALC_TRANSFORM_PROMPTS)
                lines = []
                for line in raw.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    cleaned = re.sub(r"^\d+[\.\)\-]\s*", "", line).strip()
                    if cleaned and len(cleaned) > 5:
                        lines.append(cleaned)
                if len(lines) >= 3:
                    return lines[:10]
                return list(self._FALLBACK_CALC_TRANSFORM_PROMPTS)
            except Exception:
                return list(self._FALLBACK_CALC_TRANSFORM_PROMPTS)

        def _set_suggestions_ui(suggestions):
            if not suggestions_list:
                return
            try:
                suggestions_list.removeItems(0, suggestions_list.getItemCount())
            except Exception:
                pass
            if suggestions:
                try:
                    suggestions_list.addItems(tuple(suggestions), 0)
                except Exception:
                    pass

        def _load_cached_suggestions():
            try:
                cached = self._get_config_from_file("calc_transform_suggestions_cache", None)
                if isinstance(cached, list) and len(cached) >= 3:
                    return cached
            except Exception:
                pass
            return None

        cached = _load_cached_suggestions()
        _set_suggestions_ui(cached if cached else list(self._FALLBACK_CALC_TRANSFORM_PROMPTS))

        def _bg_ai_suggestions():
            try:
                suggestions = _generate_calc_suggestions(cell_content)
                if suggestions and suggestions != list(self._FALLBACK_CALC_TRANSFORM_PROMPTS):
                    try:
                        self.set_config("calc_transform_suggestions_cache", suggestions)
                    except Exception:
                        pass
                _set_suggestions_ui(suggestions)
            except Exception:
                pass
        threading.Thread(target=_bg_ai_suggestions, daemon=True).start()

        regen_props = {
            "Label": "  Nouvelles suggestions",
            "FontHeight": _UI["font_small"],
            "FontWeight": 150,
            "TextColor": _UI["text_secondary"],
            "BackgroundColor": _UI["bg_section"],
        }
        if _mascot_url:
            regen_props["ImageURL"] = _mascot_url
            regen_props["ImagePosition"] = 0
            regen_props["ImageAlign"] = 0
        btn_regen = add("btn_regen", "Button",
            WIDTH - HORI_MARGIN - REGEN_BTN_WIDTH, suggest_y,
            REGEN_BTN_WIDTH, BUTTON_HEIGHT, regen_props)
        _add_rollover(btn_regen, _UI["bg_section"], _UI["bg_accent"],
                      _mascot_url, _mascot_hover_url)

        # Position dialog
        frame = create("com.sun.star.frame.Desktop").getCurrentFrame()
        window = frame.getContainerWindow() if frame else None
        dialog.createPeer(create("com.sun.star.awt.Toolkit"), window)
        saved_x = self.get_config("calc_input_dialog_x", None)
        saved_y = self.get_config("calc_input_dialog_y", None)
        if window:
            ps = window.getPosSize()
            if isinstance(saved_x, (int, float)) and isinstance(saved_y, (int, float)):
                _x, _y = int(saved_x), int(saved_y)
            else:
                _x = ps.Width / 2 - WIDTH / 2
                _y = ps.Height / 2 - HEIGHT / 2
            dialog.setPosSize(_x, _y, 0, 0, POS)

        # State
        result = {"text": ""}

        def _save_pos():
            try:
                ps = dialog.getPosSize()
                self.set_config("calc_input_dialog_x", int(ps.X))
                self.set_config("calc_input_dialog_y", int(ps.Y))
            except Exception:
                pass

        # Listeners
        class SendListener(unohelper.Base, XActionListener):
            def actionPerformed(self, event):
                try:
                    result["text"] = edit_control.getModel().Text.strip()
                except Exception:
                    pass
                _save_pos()
                try:
                    dialog.endExecute()
                except Exception:
                    pass
            def disposing(self, event):
                return

        class RegenListener(unohelper.Base, XActionListener):
            def actionPerformed(self, event):
                try:
                    threading.Thread(
                        target=_bg_ai_suggestions,
                        daemon=True,
                    ).start()
                except Exception:
                    pass
            def disposing(self, event):
                return

        class SuggestItemListener(unohelper.Base, XItemListener):
            def itemStateChanged(self, event):
                try:
                    selected = suggestions_list.getSelectedItem() if suggestions_list else ""
                    if selected and edit_control:
                        edit_control.getModel().Text = selected
                except Exception:
                    pass
            def disposing(self, event):
                return

        if btn_send:
            try:
                btn_send.addActionListener(SendListener())
            except Exception:
                pass
        if btn_regen:
            try:
                btn_regen.addActionListener(RegenListener())
            except Exception:
                pass
        if suggestions_list:
            try:
                suggestions_list.addItemListener(SuggestItemListener())
            except Exception:
                pass

        if edit_control:
            try:
                edit_control.setFocus()
            except Exception:
                pass

        dialog.execute()
        try:
            dialog.dispose()
        except Exception:
            pass
        return result["text"]

    # ── Calc formula prompts persistence ─────────────────────────────────────
    def _prompts_calc_path(self):
        """Chemin du fichier d'historique des prompts Calc.

        L'historique se range à côté de config.json, dans le profil utilisateur
        LibreOffice. Si ce dossier est introuvable on rend "" — surtout pas un
        repli sur le HOME : ces lignes sont du contenu saisi par l'utilisateur,
        et les écrire en clair dans le dossier personnel est un défaut de
        confidentialité (issue #31). Les appelants traitent "" comme
        « pas d'historique disponible ».
        """
        config_dir = self._get_user_config_dir()
        if not config_dir:
            return ""
        return os.path.join(config_dir, "prompts_calc.txt")

    def _load_prompts_calc(self):
        """Load saved prompts (most-recent-first, max 100)."""
        path = self._prompts_calc_path()
        if not path:
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [l.rstrip("\n") for l in f if l.strip()]
            return lines[:100]
        except Exception:
            return []

    def _save_prompt_calc(self, prompt: str):
        """Prepend prompt to the history file (deduplicated, max 100 lines)."""
        path = self._prompts_calc_path()
        if not path:
            return
        try:
            existing = self._load_prompts_calc()
            deduped = [p for p in existing if p != prompt]
            lines = [prompt] + deduped
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines[:100]) + "\n")
        except Exception:
            pass

    def _show_formula_assistant_dialog(
        self,
        schema_context: str = "",
        history_lines: list = None,
        on_generate=None,
        on_apply=None,
        schema_builder=None,
        title: str = "MIrAI — Assistant Formule",
    ) -> None:
        """Non-modal multi-turn formula assistant dialog with preview.

        Layout (top→bottom):
          Input zone (label + textarea + Générer button)
          Context strip
          History zone (label + small utils + clickable listbox)

        on_generate(user_input) — called on Générer, returns new history lines.
          Does NOT apply the formula — only previews it.
        on_apply() — called on Appliquer, applies the previewed formula.
        schema_builder(raw_selection) — called on selection change, returns
          (on_generate_fn, schema_ctx_str, on_apply_fn). When provided, a
          XSelectionChangeListener keeps the context strip live.
        Closing the window (X) disposes the dialog.
        """
        if history_lines is None:
            history_lines = []

        WIDTH = 700
        HORI_MARGIN = 14
        VERT_MARGIN = 12
        VERT_SEP = 8
        LABEL_HEIGHT = 20
        CONTEXT_HEIGHT = 36       # 2 lines of schema info
        HISTORY_HEIGHT = 140      # clickable conversation history (listbox)
        DETAIL_HEIGHT = 80        # formula explanation + alternative
        INPUT_HEIGHT = 70         # user input area
        BUTTON_HEIGHT = 30
        BUTTON_WIDTH = 130

        HEIGHT = (
            VERT_MARGIN
            + LABEL_HEIGHT + VERT_SEP          # section header
            + LABEL_HEIGHT + VERT_SEP          # "Votre demande" label
            + INPUT_HEIGHT + VERT_SEP          # input textarea
            + BUTTON_HEIGHT + VERT_SEP         # Générer + Appliquer button row
            + DETAIL_HEIGHT + VERT_SEP         # formula detail (explanation + alt)
            + CONTEXT_HEIGHT + VERT_SEP        # schema context strip
            + LABEL_HEIGHT + VERT_SEP          # "Conversation" label row (with utils)
            + HISTORY_HEIGHT + VERT_MARGIN     # clickable conversation history
        )

        # PosSize constants: X=1 Y=2 WIDTH=4 HEIGHT=8 SIZE=12 POSSIZE=15
        _POSSIZE = 15
        _SIZE = 12
        from com.sun.star.awt import XActionListener, XItemListener

        self._log("[formula_dlg] creating dialog")
        ctx = uno.getComponentContext()
        sm = ctx.getServiceManager()

        def _cr(n):
            return sm.createInstanceWithContext(n, ctx)

        dlg = _cr("com.sun.star.awt.UnoControlDialog")
        dlg_m = _cr("com.sun.star.awt.UnoControlDialogModel")
        dlg.setModel(dlg_m)
        dlg.setVisible(False)
        dlg.setTitle(title)
        dlg.setPosSize(0, 0, WIDTH, HEIGHT, _SIZE)

        try:
            dlg_m.BackgroundColor = _UI["bg"]
        except Exception:
            pass

        def _add(name, ctrl_type, x, y, w, h, props):
            m = dlg_m.createInstance("com.sun.star.awt.UnoControl" + ctrl_type + "Model")
            dlg_m.insertByName(name, m)
            c = dlg.getControl(name)
            c.setPosSize(x, y, w, h, _POSSIZE)
            for k, v in props.items():
                try:
                    setattr(m, k, v)
                except Exception:
                    pass
            return c

        try:
            from com.sun.star.awt.FontWeight import BOLD
        except Exception:
            BOLD = 150

        y = VERT_MARGIN

        # ── Section header ─────────────────────────────────────────────
        _add("lbl_header", "FixedText", HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": "🤖 MIrAI — Assistant Formule",
            "FontHeight": _UI["font_section"],
            "FontWeight": BOLD,
            "TextColor": _UI["text_on_dark"],
            "BackgroundColor": _UI["bg_header"],
        })
        y += LABEL_HEIGHT + VERT_SEP

        # ── Input label ────────────────────────────────────────────────
        _add("lbl_input", "FixedText", HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
            "Label": "Votre demande :",
            "FontHeight": _UI["font_label"],
            "FontWeight": BOLD,
            "TextColor": _UI["text"],
        })
        y += LABEL_HEIGHT + VERT_SEP

        # ── Input text area ────────────────────────────────────────────
        _add("txt_input", "Edit", HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, INPUT_HEIGHT, {
            "Text": "",
            "MultiLine": True,
            "VScroll": False,
            "FontHeight": _UI["font_body"],
            "BackgroundColor": _UI["bg_input"],
            "Border": 1,
        })
        y += INPUT_HEIGHT + VERT_SEP

        # ── Générer + Appliquer buttons (right-aligned) ──────────────
        APPLY_WIDTH = 110
        btn_x_apply = WIDTH - HORI_MARGIN - APPLY_WIDTH
        btn_x_send = btn_x_apply - BUTTON_WIDTH - 8

        _add("btn_send", "Button", btn_x_send, y, BUTTON_WIDTH, BUTTON_HEIGHT, {
            "Label": "⚡ Prévisualiser",
            "PushButtonType": 0,
            "DefaultButton": True,
            "FontHeight": _UI["font_label"],
            "BackgroundColor": _UI["btn_primary_bg"],
            "TextColor": _UI["btn_primary_fg"],
        })
        _add("btn_apply", "Button", btn_x_apply, y, APPLY_WIDTH, BUTTON_HEIGHT, {
            "Label": "✓ Appliquer",
            "PushButtonType": 0,
            "FontHeight": _UI["font_label"],
            "BackgroundColor": _UI["success"],
            "TextColor": _UI["text_on_dark"],
        })
        y += BUTTON_HEIGHT + VERT_SEP

        # ── Formula detail zone (explanation + alternative) ─────────────
        _add("txt_detail", "Edit", HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, DETAIL_HEIGHT, {
            "Text": "La formule et son explication apparaîtront ici après la prévisualisation.",
            "MultiLine": True,
            "ReadOnly": True,
            "VScroll": True,
            "FontHeight": _UI["font_small"],
            "TextColor": _UI["text_secondary"],
            "BackgroundColor": _UI["bg_section"],
            "Border": 1,
            "BorderColor": _UI["border"],
        })
        y += DETAIL_HEIGHT + VERT_SEP

        # ── Schema context strip ────────────────────────────────────────
        _add("lbl_ctx", "FixedText", HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, CONTEXT_HEIGHT, {
            "Label": schema_context or "Aucun contexte disponible",
            "FontHeight": _UI["font_small"],
            "TextColor": _UI["text_secondary"],
            "BackgroundColor": _UI["bg_section"],
            "MultiLine": True,
        })
        y += CONTEXT_HEIGHT + VERT_SEP

        # ── History label row  (label + "Vider…" + "Ouvrir prompts…") ──
        lbl_hist_w = WIDTH - HORI_MARGIN * 2 - 90 - 8 - 120 - 8
        _add("lbl_hist", "FixedText", HORI_MARGIN, y, lbl_hist_w, LABEL_HEIGHT, {
            "Label": "Conversation :",
            "FontHeight": _UI["font_label"],
            "FontWeight": BOLD,
            "TextColor": _UI["text"],
        })
        btn_clear_x = HORI_MARGIN + lbl_hist_w + 8
        _add("btn_clear", "Button", btn_clear_x, y, 90, LABEL_HEIGHT, {
            "Label": "Vider…",
            "PushButtonType": 0,
            "FontHeight": _UI["font_small"],
        })
        btn_open_x = btn_clear_x + 90 + 8
        _add("btn_open_prompts", "Button", btn_open_x, y, 120, LABEL_HEIGHT, {
            "Label": "Ouvrir prompts…",
            "PushButtonType": 0,
            "FontHeight": _UI["font_small"],
        })
        y += LABEL_HEIGHT + VERT_SEP

        # ── History listbox (clickable — ▶ lines refill input) ────────
        _add("lst_history", "ListBox", HORI_MARGIN, y, WIDTH - HORI_MARGIN * 2, HISTORY_HEIGHT, {
            "StringItemList": tuple(history_lines),
            "FontHeight": _UI["font_body"],
            "BackgroundColor": _UI["bg_section"],
            "Border": 1,
            "MultiSelection": False,
            "Dropdown": False,
        })

        _job = self  # capture outer instance for inner class closures
        # Mutable state — on_generate replaced in-place on selection change
        _job._formula_dialog_state = {
            "on_generate": on_generate,
            "on_apply": on_apply,
            "history_lines": history_lines,
            "schema_builder": schema_builder,
            "sel_listener": None,   # (listener, controller) set after createPeer
        }

        class GenerateListener(unohelper.Base, XActionListener):
            def actionPerformed(self, _ev):
                source = getattr(_ev, "Source", None)
                state = _job._formula_dialog_state
                if state is None:
                    return

                def _set_busy(busy, label=""):
                    """Toggle busy state on the dialog."""
                    try:
                        btn = dlg.getControl("btn_send")
                        lbl = dlg.getControl("lbl_input")
                        if busy:
                            btn.getModel().Label = "⏳ Mirai réfléchit..."
                            btn.setEnable(False)
                            lbl.getModel().Label = label or "Mirai génère la formule..."
                            lbl.getModel().TextColor = _UI["primary"]
                        else:
                            btn.getModel().Label = "⚡ Prévisualiser"
                            btn.setEnable(True)
                            lbl.getModel().Label = "Votre demande :"
                            lbl.getModel().TextColor = _UI["text"]
                    except Exception:
                        pass

                # ── Appliquer button ──
                try:
                    apply_ctrl = dlg.getControl("btn_apply")
                except Exception:
                    apply_ctrl = None
                if source == apply_ctrl:
                    on_apply_fn = state.get("on_apply")
                    if on_apply_fn is None:
                        return
                    try:
                        _set_busy(True, "Application de la formule...")
                        result_lines = on_apply_fn()
                        state["history_lines"].extend(result_lines or [])
                        dlg.getControl("lst_history").getModel().StringItemList = tuple(state["history_lines"])
                        dlg.getControl("lst_history").selectItemPos(len(state["history_lines"]) - 1, True)
                    except Exception as e:
                        _job._log(f"[formula_dlg] apply error: {e}")
                    finally:
                        _set_busy(False)
                    return

                # ── Prévisualiser button ──
                try:
                    user_input = dlg.getControl("txt_input").getText().strip()
                except Exception:
                    return
                if not user_input:
                    return
                if state.get("on_generate") is None:
                    return
                try:
                    _set_busy(True)
                    result = state["on_generate"](user_input)
                    # on_generate returns (lines, detail_text) or just lines
                    if isinstance(result, tuple) and len(result) == 2:
                        new_lines, detail_text = result
                    else:
                        new_lines = result
                        detail_text = ""
                    state["history_lines"].extend(new_lines or [])
                    _job._save_prompt_calc(user_input)
                    dlg.getControl("lst_history").getModel().StringItemList = tuple(state["history_lines"])
                    dlg.getControl("lst_history").selectItemPos(len(state["history_lines"]) - 1, True)
                    dlg.getControl("txt_input").getModel().Text = ""
                    # Update detail zone
                    if detail_text:
                        try:
                            dlg.getControl("txt_detail").getModel().Text = detail_text
                            dlg.getControl("txt_detail").getModel().TextColor = _UI["text"]
                        except Exception:
                            pass
                except Exception as e:
                    _job._log(f"[formula_dlg] generate error: {e}")
                finally:
                    _set_busy(False)

        class ClearListener(unohelper.Base, XActionListener):
            def actionPerformed(self, _ev):
                try:
                    mb = _cr("com.sun.star.awt.Toolkit")
                    frame2 = _cr("com.sun.star.frame.Desktop").getCurrentFrame()
                    win2 = frame2.getContainerWindow() if frame2 else None
                    mbox = mb.createMessageBox(win2, 3, 3, "Confirmer", "Vider l'historique des demandes ?")
                    if mbox.execute() == 2:  # YES = 2
                        import os as _os
                        try:
                            _hist = _job._prompts_calc_path()
                            if _hist:
                                _os.remove(_hist)
                        except Exception:
                            pass
                        _job._formula_dialog_state["history_lines"].clear()
                        try:
                            dlg.getControl("lst_history").getModel().StringItemList = ()
                        except Exception:
                            pass
                except Exception:
                    pass

        class OpenPromptsListener(unohelper.Base, XActionListener):
            def actionPerformed(self, _ev):
                import subprocess as _sub
                import os as _os
                try:
                    path = _job._prompts_calc_path()
                    if not path:
                        return
                    if not _os.path.exists(path):
                        open(path, "w").close()
                    _sub.Popen(["open", path])
                except Exception:
                    pass

        class HistorySelectListener(unohelper.Base, XItemListener):
            def itemStateChanged(self, ev):
                try:
                    idx = ev.Selected
                    if idx >= 0:
                        items = dlg.getControl("lst_history").getModel().StringItemList
                        if idx < len(items) and items[idx].startswith("▶ "):
                            dlg.getControl("txt_input").getModel().Text = items[idx][2:]
                except Exception:
                    pass

        class FormulaDialogTopWindowListener(unohelper.Base, XTopWindowListener):
            def windowClosing(self, _ev):
                try:
                    ps = dlg.getPosSize()
                    _job.set_config("formula_dialog_x", int(ps.X))
                    _job.set_config("formula_dialog_y", int(ps.Y))
                except Exception:
                    pass
                try:
                    dlg.setVisible(False)
                    dlg.dispose()
                except Exception:
                    pass
                # Detach selection listener before disposing
                try:
                    lc = _job._formula_dialog_state.get("sel_listener") if _job._formula_dialog_state else None
                    if lc:
                        lc[1].removeSelectionChangeListener(lc[0])
                except Exception:
                    pass
                _job._formula_dialog = None
                _job._formula_dialog_state = None
            def windowOpened(self, _ev): return
            def windowClosed(self, _ev): return
            def windowMinimized(self, _ev): return
            def windowNormalized(self, _ev): return
            def windowActivated(self, _ev): return
            def windowDeactivated(self, _ev): return
            def disposing(self, _ev): return

        try:
            from com.sun.star.view import XSelectionChangeListener as _XSCListener
        except Exception:
            _XSCListener = None

        class FormulaSelectionListener(unohelper.Base, *([_XSCListener] if _XSCListener else [])):
            """Listens to cell selection changes and refreshes the dialog context."""
            def selectionChanged(self, ev):
                state = _job._formula_dialog_state
                if state is None or state.get("schema_builder") is None:
                    return
                try:
                    new_sel = ev.Source.getSelection()
                    build_result = state["schema_builder"](new_sel)
                    new_on_gen = build_result[0]
                    new_sc = build_result[1]
                    new_on_apply = build_result[2] if len(build_result) > 2 else None
                    if new_on_gen is None:
                        return
                    state["on_generate"] = new_on_gen
                    if new_on_apply is not None:
                        state["on_apply"] = new_on_apply
                    # Add separator to history so the user sees the context switch
                    hl = state["history_lines"]
                    if hl:
                        hl.append(f"── {new_sc.splitlines()[0]} ──")
                    try:
                        dlg.getControl("lbl_ctx").getModel().Label = new_sc
                        dlg.getControl("lst_history").getModel().StringItemList = tuple(hl)
                        if hl:
                            dlg.getControl("lst_history").selectItemPos(len(hl) - 1, True)
                    except Exception:
                        pass
                except Exception as e:
                    _job._log(f"[formula_dlg] selectionChanged error: {e}")
            def disposing(self, _ev): return

        _gen_listener = GenerateListener()
        dlg.getControl("btn_send").addActionListener(_gen_listener)
        dlg.getControl("btn_apply").addActionListener(_gen_listener)
        dlg.getControl("btn_clear").addActionListener(ClearListener())
        dlg.getControl("btn_open_prompts").addActionListener(OpenPromptsListener())
        try:
            dlg.getControl("lst_history").addItemListener(HistorySelectListener())
        except Exception:
            pass

        # Position dialog — remember last position
        _saved_x = self.get_config("formula_dialog_x", None)
        _saved_y = self.get_config("formula_dialog_y", None)
        toolkit = _cr("com.sun.star.awt.Toolkit")
        frame = _cr("com.sun.star.frame.Desktop").getCurrentFrame()
        window = frame.getContainerWindow() if frame else None
        dlg.createPeer(toolkit, window)

        try:
            peer = dlg.getPeer()
            if peer:
                peer.addTopWindowListener(FormulaDialogTopWindowListener())
        except Exception:
            pass

        # Register selection change listener on the Calc controller
        try:
            ctrl = frame.getController() if frame else None
            if ctrl and schema_builder is not None:
                sl = FormulaSelectionListener()
                ctrl.addSelectionChangeListener(sl)
                _job._formula_dialog_state["sel_listener"] = (sl, ctrl)
        except Exception as e:
            _job._log(f"[formula_dlg] sel_listener register error: {e}")

        if _saved_x is not None and _saved_y is not None:
            try:
                dlg.setPosSize(int(_saved_x), int(_saved_y), WIDTH, HEIGHT, _POSSIZE)
            except Exception:
                pass
        else:
            if window:
                ps = window.getPosSize()
                cx = ps.X + (ps.Width - WIDTH) // 2
                cy = ps.Y + (ps.Height - HEIGHT) // 4
                dlg.setPosSize(cx, cy, WIDTH, HEIGHT, _POSSIZE)

        # Select last item in history listbox
        try:
            if history_lines:
                dlg.getControl("lst_history").selectItemPos(len(history_lines) - 1, True)
        except Exception:
            pass

        dlg.setVisible(True)
        self._formula_dialog = dlg


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
        SECTION_PAD = 10  # inner padding for visual sections
        import uno
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
        dialog.setTitle(title or "MIrAI — Paramètres")

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
                label_model.Label = "Contacte MIrAI..."
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
                    label.getModel().Label = f"Contacte MIrAI{dots}"
                    pump_events(toolkit)
                except Exception:
                    pass
                time.sleep(delay)

        wait_dialog, wait_label, wait_toolkit = show_wait_dialog()
        try:
            animate_wait(wait_label, wait_toolkit, steps=4, delay=0.15)
            endpoint_value = str(self.get_config("llm_base_urls","http://127.0.0.1:5000/api"))
            api_key_value = str(self.get_config("llm_api_tokens",""))
            log_to_file(f"Settings open: llm_api_tokens length={len(api_key_value)}")
            current_model = str(self._get_config_from_file("llm_default_models","")).strip()
            is_openwebui = True
            models, model_descriptions = self._fetch_models_info(endpoint_value, api_key_value, is_openwebui)
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
            {"name": "endpoint", "label": "OWUI Endpoint:", "value": endpoint_value, "type": "text"},
            {"name": "api_key", "label": "Token OWUI:", "value": api_key_value, "type": "password"},
            {"name": "model", "label": "Model:", "value": current_model, "type": "list", "items": models},
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
                    WIDTH - HORI_MARGIN * 2 - 90, LABEL_HEIGHT, {
                        "Label": "Connexion", "NoLabel": True,
                        "FontHeight": _UI["font_section"],
                        "TextColor": _UI["primary"],
                        "FontWeight": 150,
                    })
                proxy_btn_width = 80
                proxy_btn_height = LABEL_HEIGHT + 4
                proxy_btn_x = WIDTH - HORI_MARGIN - proxy_btn_width
                add("btn_proxy", "Button", proxy_btn_x, current_y - 2,
                    proxy_btn_width, proxy_btn_height, {
                        "Label": "Proxy",
                        "Name": "proxy_settings",
                        "Tabstop": True,
                        "Enabled": True,
                        "FontHeight": _UI["font_small"],
                        "TextColor": _UI["text_secondary"],
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
                    "Label": "Révéler", "NoLabel": True,
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
                    "Label": "♻️ Rafraîchir le token", "Name": "test_token", "NoLabel": True,
                    "FontHeight": _UI["font_small"],
                })
                current_y += TEST_ROW_HEIGHT

        description_label = "Description du modèle :"
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
        anon_ok, auth_ok = self._api_status(endpoint_value, api_key_value, is_openwebui)

        def _status_style(anon_ok, auth_ok, email_value):
            if auth_ok:
                return ("Connecté", _UI["status_ok"])
            if anon_ok and not auth_ok:
                return ("Anonyme OK", _UI["status_warn"])
            if not anon_ok and not auth_ok and email_value is None:
                return ("Non testé", _UI["status_neutral"])
            return ("Non accessible", _UI["status_fail"])

        status_label, status_color = _status_style(anon_ok, auth_ok, email)
        status_text = f"{status_label}" + (f" ({email})" if email else "")

        # Status section header
        add("section_status", "FixedText", HORI_MARGIN, current_y,
            WIDTH - HORI_MARGIN * 2, LABEL_HEIGHT, {
                "Label": "État de la connexion", "NoLabel": True,
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
                "Label": "🔐 Login SSO", "Name": "keycloak_login",
                "Tabstop": True, "Enabled": True, "NoLabel": True,
                "FontHeight": _UI["font_small"],
            })
        add("btn_reload_config", "Button", HORI_MARGIN + keycloak_width + HORI_SEP,
            current_y, reload_width, BUTTON_HEIGHT, {
                "Label": "🔄 Recharger la configuration", "Name": "reload_config",
                "Tabstop": True, "Enabled": True, "NoLabel": True,
                "FontHeight": _UI["font_small"],
            })
        current_y += BUTTON_HEIGHT + VERT_SEP * 2

        # OK / Cancel row - right-aligned
        ok_cancel_width = BUTTON_WIDTH
        add("btn_ok", "Button", WIDTH - HORI_MARGIN - ok_cancel_width * 2 - HORI_SEP, current_y,
            ok_cancel_width, BUTTON_HEIGHT, {
                "PushButtonType": OK, "DefaultButton": True, "Label": "Enregistrer",
                "FontHeight": _UI["font_label"],
            })
        add("btn_cancel", "Button", WIDTH - HORI_MARGIN - ok_cancel_width, current_y,
            ok_cancel_width, BUTTON_HEIGHT, {
                "PushButtonType": CANCEL, "Label": "Annuler",
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
        if not api_key_plain_control:
            api_key_plain_control = dialog.getControl("edit_api_key_plain")
        btn_reload_config = dialog.getControl("btn_reload_config")
        btn_proxy = dialog.getControl("btn_proxy")
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
            effective_api_key = self._effective_api_token(api_key_val)
            log_to_file("Token test: start")
            conn_ok, conn_detail = self._endpoint_connectivity_status(endpoint_val, True)
            if not conn_ok:
                proxy_cfg = self._get_proxy_config()
                err = conn_detail.get("error", "inconnue")
                url = conn_detail.get("url", endpoint_val)
                if proxy_cfg.get("enabled"):
                    self._show_message(
                        "API",
                        "Endpoint OWUI injoignable via le proxy.\n\n"
                        f"URL testée: {url}\n"
                        f"Détail: {err}\n\n"
                        "Vérifiez le proxy (bouton Proxy > Tester connexion)."
                    )
                else:
                    self._show_message(
                        "API",
                        "Endpoint OWUI injoignable.\n\n"
                        f"URL testée: {url}\n"
                        f"Détail: {err}"
                    )
                log_to_file(f"Token test: connectivity failed url={url} err={err}")
                return
            anon_ok, auth_ok = _update_api_status_label(endpoint_val, effective_api_key)
            if not auth_ok:
                self._show_message(
                    "API",
                    "Token invalide, absent, ou refusé."
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
            models, model_descriptions_local = self._fetch_models_info(endpoint_val, effective_api_key, True)
            if not models:
                self._show_message(
                    "API",
                    "Aucun modèle disponible (vérifiez l'endpoint et le token)."
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
            desc = model_descriptions.get(selected) or f"ID: {selected}"
            try:
                model_desc_control.getModel().Text = desc
            except Exception:
                pass
            log_to_file(f"Token test: ok, models={len(models)}")

        class SettingsActionListener(unohelper.Base, XActionListener):
            def __init__(self, outer, model_control, desc_control, descriptions, endpoint_control, api_key_control, api_key_plain_control, toggle_control):
                self.outer = outer
                self.model_control = model_control
                self.desc_control = desc_control
                self.descriptions = descriptions
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
                        self.toggle_control.getModel().Label = "Révéler" if self.api_key_masked else "Masquer"
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
                        _read_api_key_value(),
                        email_value=email
                    )
                elif command == "toggle_api_key":
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
                        if self.toggle_control:
                            self.toggle_control.getModel().Label = "Révéler" if self.api_key_masked else "Masquer"
                    except Exception:
                        pass
                elif command == "test_token":
                    _test_token_and_refresh()
                elif command == "reload_config":
                    pass
                elif command == "proxy_settings":
                    try:
                        self.outer.proxy_settings_box("Proxy")
                    except Exception:
                        pass

        def _do_reload_config():
            log_to_file("Reload config: button clicked")
            cancel_flag = {"cancel": False}

            def _show_reload_dialog():
                try:
                    from com.sun.star.awt.PosSize import POS, SIZE, POSSIZE
                    ctx = uno.getComponentContext()
                    def create(name):
                        return ctx.getServiceManager().createInstanceWithContext(name, ctx)
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
                    label_model.Label = "Connexion à Mirai..."
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
                    btn_model.Label = "Annuler"
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
                        label.getModel().Label = f"Connexion à Mirai{dots}"
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
                    "Configuration",
                    "Impossible de recharger la configuration."
                )
                return
            try:
                endpoint_val = str(self.get_config("llm_base_urls", ""))
                api_key_val = str(self.get_config("llm_api_tokens", ""))
                model_val = str(self.get_config("llm_default_models", ""))
                is_openwebui = True
                models, model_descriptions_local = self._fetch_models_info(endpoint_val, api_key_val, is_openwebui)
                if not models:
                    self._show_message(
                        "API",
                        "Erreur lors de la récupération des modèles (vérifiez l'endpoint et le token)."
                    )
                if field_controls.get("endpoint"):
                    field_controls["endpoint"].getModel().Text = endpoint_val
                if field_controls.get("api_key"):
                    field_controls["api_key"].getModel().Text = api_key_val
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
                    desc = f"ID: {model_val}" if model_val else "Aucune description disponible"
                model_desc_control.getModel().Text = desc
            except Exception:
                pass

        class ReloadActionListener(unohelper.Base, XActionListener):
            def __init__(self, outer, model_control, desc_control, descriptions, endpoint_control, api_key_control):
                self.outer = outer
                self.model_control = model_control
                self.desc_control = desc_control
                self.descriptions = descriptions
                self.endpoint_control = endpoint_control
                self.api_key_control = api_key_control

            def actionPerformed(self, event):
                _do_reload_config()

            def disposing(self, event):
                return

        listener = SettingsActionListener(
            self,
            field_controls.get("model"),
            model_desc_control,
            model_descriptions,
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
                btn_reload_config.addActionListener(
                    ReloadActionListener(
                        self,
                        field_controls.get("model"),
                        model_desc_control,
                        model_descriptions,
                        field_controls.get("endpoint"),
                        field_controls.get("api_key"),
                    )
                )
                log_to_file("Reload config action listener attached")
            except Exception as e:
                log_to_file(f"Reload config action listener attach failed: {str(e)}")
            try:
                btn_reload_config.getModel().ActionCommand = "reload_config"
            except Exception as e:
                log_to_file(f"Reload config ActionCommand set failed: {str(e)}")

        if btn_proxy:
            try:
                btn_proxy.addActionListener(listener)
            except Exception:
                pass
            try:
                btn_proxy.getModel().ActionCommand = "proxy_settings"
            except Exception:
                pass

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
                    desc = f"ID: {selected}"
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
                                desc = f"ID: {value}"
                            self.desc_control.getModel().Text = desc
                        else:
                            self.desc_control.getModel().Text = "Aucune description disponible"
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
                    result[field["name"]] = control_text
        else:
            result = {}

        dialog.dispose()
        return result
    #end sharealike section 

    def _needs_first_enrollment(self):
        """Check if user needs first-time enrollment (not yet enrolled)."""
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
        try:
            self._schedule_config_refresh(force=True, reason="first_enrollment")
            time.sleep(1)
        except Exception:
            pass
        config_data = self._fetch_config(force=True)
        if not config_data:
            log_to_file("First enrollment: config fetch failed")
            return False
        try:
            self._sync_keycloak_from_config(config_data)
        except Exception:
            pass
        access_token = self._ensure_access_token(config_data, interactive=True)
        if access_token:
            log_to_file("First enrollment: auth succeeded")
            self._send_telemetry("EnrollSuccess", {"status": "ok"})
            return True
        log_to_file("First enrollment: auth flow canceled or failed")
        self._send_telemetry("EnrollFailed", {"status": "canceled"})
        return False

    def execute(self, args):
        """XJob.execute — called by Jobs framework on onFirstVisibleTask, onLoad, onNew."""
        log_to_file("=== XJob.execute called ===")
        try:
            # For onLoad/onNew, args contain the document frame — register directly on it
            frame = _extract_frame_from_job_args(args)
            if frame is not None:
                try:
                    controller = getattr(frame, 'Controller', None)
                    if controller is not None:
                        self._register_writer_context_menu_on(controller, "onDocEvent")
                    else:
                        log_to_file("[context-menu] execute: no controller on frame")
                except Exception as e:
                    log_to_file(f"[context-menu] frame-based registration failed: {e}")
            else:
                # onFirstVisibleTask: no frame yet, try current component + deferred fallback
                self._register_current_writer_context_menu("onStartup")
                self._schedule_context_menu_registration("onStartup", force=True)
        except Exception as e:
            log_to_file(f"[context-menu] execute failed: {e}")
        return

    # Actions qui ne portent ni sur le document ni sur une sélection : elles
    # doivent aboutir dans TOUS les contextes, Writer comme Calc, avec ou sans
    # sélection, et même sans document ouvert.
    _SHELL_ACTIONS = ("settings", "proxy_settings", "AboutDialog",
                      "Documentation", "OpenmiraiWebsite", "MenuSeparator",
                      "TestModel")

    def _handle_shell_action(self, action):
        """Traite les actions non textuelles. Retourne True si prise en charge."""
        if action not in self._SHELL_ACTIONS:
            return False
        if action == "MenuSeparator":
            return True
        try:
            if action == "settings":
                self._send_telemetry("OpenSettings", {"action": "open_settings"})
                from .menu_actions.shared import apply_settings_result
                apply_settings_result(self, self.settings_box("Settings"))
            elif action == "proxy_settings":
                self.proxy_settings_box()
            elif action == "AboutDialog":
                self._send_telemetry("AboutDialog", {"action": "about"})
                self._show_about_dialog()
            elif action == "Documentation":
                self._send_telemetry("OpenDocumentation",
                                     {"action": "open_documentation"})
                self._open_url_config("doc_url")
            elif action == "OpenmiraiWebsite":
                self._send_telemetry("OpenmiraiWebsite", {"action": "open_website"})
                self._open_url_config("portal_url")
            elif action == "TestModel":
                # Import paresseux : le moteur n'est chargé qu'à l'usage.
                from .core.entry import test_model_capabilities
                test_model_capabilities(self)
        except Exception as exc:
            # Une action de coquille qui échoue doit se VOIR : jusqu'ici la
            # panne était avalée et l'utilisateur concluait « ça ne marche pas ».
            log_to_file(f"[dispatch] action {action} en échec : {exc}")
            self._show_message(
                "Action impossible",
                f"« {action} » n'a pas pu s'exécuter.\n\n{exc}")
        return True

    def _open_url_config(self, key):
        """Ouvre l'URL d'une clé de configuration, avec repli sur le portail."""
        import webbrowser
        url = self.get_config(key, "") or self.get_config("portal_url", "")
        if not url:
            raise RuntimeError(f"aucune URL configurée ({key})")
        webbrowser.open(url)

    def trigger(self, args):
        # Parse &src= suffix if present (menu, toolbar, key)
        if "&src=" in args:
            action, source = args.split("&src=", 1)
        else:
            action, source = args, "user"
        self._trigger_source = source
        self._log(f"=== trigger called: action={action} src={source} ===")
        # Ensure the context menu interceptor is registered for this document.
        # This runs on the main thread (guaranteed by trigger()), so it's safe.
        try:
            self._register_current_writer_context_menu("trigger")
        except Exception:
            pass
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
        _enrollment_bypass = {"Documentation", "OpenmiraiWebsite", "settings", "proxy_settings", "AboutDialog"}
        if action not in _enrollment_bypass and not MainJob._enrollment_dismissed_cls and self._needs_first_enrollment():
            with MainJob._enrollment_wizard_lock_cls:
                if MainJob._enrollment_wizard_active_cls:
                    log_to_file("[ENROLL] Trigger: wizard already running, skipping")
                    return
                MainJob._enrollment_wizard_active_cls = True
            try:
                if not self._run_first_enrollment():
                    MainJob._enrollment_dismissed_cls = True
                    return
            finally:
                MainJob._enrollment_wizard_active_cls = False

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

        # Actions non textuelles : elles ne dépendent NI du type de document NI
        # d'une sélection. Elles doivent donc être traitées AVANT les handlers
        # par module. Le dispatch historique les faisait passer par
        # handle_writer_action, qui sortait sur `return True` dès que la
        # sélection était vide — un clic sur « Paramètres » ne produisait alors
        # rien du tout ; et en Calc, « Documentation » et « Site mirai »
        # n'étaient tout simplement pas branchées.
        if self._handle_shell_action(action):
            return

        if handle_writer_action(self, action, model):
            return

        if handle_calc_action(self, action, model):
            return

        # Aucune branche n'a traité l'action : le dire, plutôt que de rendre la
        # main en silence. Une action déclarée dans un manifeste mais non
        # implémentée produisait jusqu'ici un clic sans le moindre effet, sans
        # la moindre trace — impossible à diagnostiquer, pour l'utilisateur
        # comme pour le support.
        log_to_file(f"[dispatch] action non gérée : {action!r} "
                    f"(document={type(model).__name__}, src={source})")
        self._report_unhandled_action(action, model)
        self._show_message(
            "Action indisponible",
            f"L'action « {action} » n'est pas disponible ici.\n\n"
            "Ouvrez un document Writer ou Calc, puis réessayez.")

# Starting from Python IDE
def main():
    try:
        ctx = XSCRIPTCONTEXT
    except NameError:
        ctx = officehelper.bootstrap()
        if ctx is None:
            print("ERROR: Could not bootstrap default Office.")
            sys.exit(1)
    job = MainJob(ctx)
    job.trigger("hello")
# Starting from command line
if __name__ == "__main__":
    main()
# pythonloader loads a static g_ImplementationHelper variable
log_to_file("=== Loading mirai extension module ===")
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    MainJob,  # UNO object class
    "fr.gouv.interieur.mirai.do",  # implementation name
    ("com.sun.star.task.JobExecutor", "com.sun.star.task.Job"), )  # implemented services
log_to_file("=== mirai extension registered successfully ===")
# vim: set shiftwidth=4 softtabstop=4 expandtab:
