"""
calc_prompt_function.py — UNO CalcAddIn exposing =PROMPT() in LibreOffice Calc.

This module is intentionally self-contained: it does NOT import from entrypoint.py
so it can be deployed as a standalone UNO component.

Registration:
  - Declared in oxt/META-INF/manifest.xml as a Python UNO component.
  - Function metadata declared in oxt/CalcAddIn.xcu.

Usage in a spreadsheet cell:
  =PROMPT("Translate to English: Bonjour")
  =PROMPT("Summarize:"; "Be concise"; "my-model"; 500)
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request

import unohelper


def _log(message: str) -> None:
    logging.info("[calc_prompt_function] %s", message)


# ---------------------------------------------------------------------------
# SSL context — local reimplementation (no dependency on entrypoint.py).
# Logic mirrors MainJob.get_ssl_context(); never uses CERT_NONE unless the
# user explicitly enables proxy_allow_insecure_ssl in their config.
# ---------------------------------------------------------------------------

def _get_bundled_ca_path() -> str:
    """Return the path to the bundled Scaleway CA chain shipped with the extension."""
    return os.path.join(
        os.path.dirname(__file__),
        "CAbundle",
        "scaleway-bootstrap-ca-chain.pem",
    )


def build_ssl_context(config: dict) -> ssl.SSLContext:
    """
    Build an SSLContext using the bundled CA chain (or a user-configured one).

    Args:
        config: flat dict of merged config values (same keys as config.json).

    Returns:
        ssl.SSLContext — never CERT_NONE unless proxy_allow_insecure_ssl is True.
    """
    allow_insecure = bool(config.get("proxy_allow_insecure_ssl", False))
    ctx = ssl.create_default_context()
    if allow_insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _log("WARNING: insecure SSL enabled by proxy_allow_insecure_ssl config")
        return ctx

    configured_bundle = str(config.get("ca_bundle_path", "") or "").strip()
    candidate_paths: list[str] = []

    if configured_bundle:
        if os.path.isabs(configured_bundle):
            candidate_paths.append(configured_bundle)
        else:
            # Relative: try next to this module
            candidate_paths.append(
                os.path.join(os.path.dirname(__file__), configured_bundle)
            )

    # Always append the bundled CA as fallback
    candidate_paths.append(_get_bundled_ca_path())

    seen: set[str] = set()
    for path in candidate_paths:
        candidate = str(path or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not os.path.isfile(candidate):
            continue
        try:
            ctx.load_verify_locations(cafile=candidate)
            _log(f"SSL CA bundle loaded: {candidate}")
            return ctx
        except Exception as exc:
            _log(f"SSL CA bundle load failed ({candidate}): {exc}")

    # Fall through: use system CAs (ssl.create_default_context default)
    _log("No custom CA bundle loaded; using system CAs")
    return ctx


# ---------------------------------------------------------------------------
# Config reader — mirrors _get_config_from_file in entrypoint.py but is
# standalone (no UNO service manager required once user_config_path is known).
# ---------------------------------------------------------------------------

def _read_config_file(path: str) -> dict:
    """Read a JSON config file, returning an empty dict on any error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def load_config(ctx) -> dict:
    """
    Load merged config from the user profile config.json and the extension
    config.default.json, exactly like MainJob._get_config_from_file.

    Args:
        ctx: UNO component context (com.sun.star.uno.XComponentContext).

    Returns:
        Merged flat config dict.  Always returns a dict (never raises).
    """
    try:
        sm = ctx.getServiceManager()
        path_settings = sm.createInstanceWithContext(
            "com.sun.star.util.PathSettings", ctx
        )
        user_config_path = str(getattr(path_settings, "UserConfig", "") or "")
        if user_config_path.startswith("file://"):
            try:
                import uno  # available at runtime inside LibreOffice
                user_config_path = str(uno.fileUrlToSystemPath(user_config_path))
            except Exception:
                user_config_path = user_config_path.replace("file://", "")
    except Exception as exc:
        _log(f"Could not resolve UserConfig path: {exc}")
        user_config_path = ""

    user_config_file = os.path.join(user_config_path, "config.json") if user_config_path else ""
    # Look for config.default.json relative to this module (and one level up for OXT layout)
    package_candidates = [
        os.path.join(os.path.dirname(__file__), "config.default.json"),
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "config.default.json")
        ),
    ]

    package_config: dict = {}
    for p in package_candidates:
        if os.path.isfile(p):
            package_config = _read_config_file(p)
            if package_config:
                break

    user_config: dict = _read_config_file(user_config_file) if user_config_file else {}

    merged: dict = {}
    merged.update(package_config)
    merged.update(user_config)
    return merged


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

_PLUGIN_NAME = "MIrAI-LibreOffice"
_USER_AGENT = _PLUGIN_NAME


def _urlopen(request: urllib.request.Request, ssl_context: ssl.SSLContext, timeout: int = 60):
    """Thin wrapper around urllib.request.urlopen for easier mocking in tests."""
    return urllib.request.urlopen(request, context=ssl_context, timeout=timeout)


# ---------------------------------------------------------------------------
# LLM call — synchronous, stream=False
# ---------------------------------------------------------------------------

def _split_endpoint(endpoint: str) -> tuple[str, str]:
    """
    Split a base URL into (base, api_path).

    Rules mirror _split_endpoint_api_path in entrypoint.py:
    - If the URL already ends with /api or /v1, return (endpoint, "").
    - Otherwise append /api (OpenWebUI default).
    """
    ep = endpoint.rstrip("/")
    if ep.endswith("/api") or ep.endswith("/v1"):
        return ep, ""
    return ep, "/api"


def call_llm(
    message: str,
    system_prompt: str,
    model: str,
    max_tokens: int,
    config: dict,
    ssl_context: ssl.SSLContext,
) -> str:
    """
    Perform a synchronous (stream=False) chat/completions call to the LLM.

    Returns the generated text string, or an error message prefixed with
    "#PROMPT_ERROR:" so the Calc cell clearly shows what went wrong.
    """
    endpoint = str(config.get("llm_base_urls", "http://127.0.0.1:5000")).rstrip("/")
    api_key = str(config.get("llm_api_tokens", "") or "").strip()

    auth_header_name = str(config.get("authHeaderName", "Authorization") or "Authorization").strip() or "Authorization"
    auth_header_prefix = str(config.get("authHeaderPrefix", "Bearer ") or "Bearer ").strip()
    if auth_header_prefix and not auth_header_prefix.endswith(" "):
        auth_header_prefix += " "

    base, api_path = _split_endpoint(endpoint)
    url = base + api_path + "/chat/completions"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": message})

    body: dict = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 1,
        "top_p": 0.9,
        "stream": False,  # MUST be False — Calc needs a synchronous single response
    }
    if model:
        body["model"] = model

    headers = {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    if api_key:
        headers[auth_header_name] = f"{auth_header_prefix}{api_key}"

    json_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    _log(f"PROMPT call → {url}  model={model!r}  max_tokens={max_tokens}")

    req = urllib.request.Request(url, data=json_data, headers=headers, method="POST")

    try:
        timeout = int(config.get("llm_request_timeout_seconds", 60))
        if timeout < 5:
            timeout = 5
    except Exception:
        timeout = 60

    try:
        response = _urlopen(req, ssl_context, timeout=timeout)
        raw = response.read()
        if hasattr(response, "__exit__"):
            try:
                response.__exit__(None, None, None)
            except Exception:
                pass
        data = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        _log(f"HTTP error {exc.code}: body_len={len(body)}")
        return f"#PROMPT_ERROR: HTTP {exc.code} — {exc.reason} {body[:200]}".strip()
    except urllib.error.URLError as exc:
        _log(f"URL error: {exc.reason}")
        return f"#PROMPT_ERROR: network error — {exc.reason}"
    except json.JSONDecodeError as exc:
        _log(f"JSON decode error: {exc}")
        return f"#PROMPT_ERROR: invalid JSON response — {exc}"
    except Exception as exc:
        _log(f"Unexpected error: {exc}")
        return f"#PROMPT_ERROR: {exc}"

    # Extract content — support both chat and legacy completions formats
    try:
        choices = data.get("choices", [])
        if choices:
            choice = choices[0]
            # Chat completions: choices[0].message.content
            message_obj = choice.get("message")
            if isinstance(message_obj, dict):
                content = message_obj.get("content", "")
                if content is not None:
                    return str(content)
            # Legacy completions: choices[0].text
            text = choice.get("text")
            if text is not None:
                return str(text)
        _log(f"Unexpected response structure: keys={sorted(data)}")
        return f"#PROMPT_ERROR: unexpected response structure"
    except Exception as exc:
        _log(f"Response parse error: {exc}")
        return f"#PROMPT_ERROR: response parse error — {exc}"


# ---------------------------------------------------------------------------
# UNO CalcAddIn component
# ---------------------------------------------------------------------------

# Sentinel: loaded outside LibreOffice (e.g., in unit tests)?
_IN_LIBREOFFICE = True
try:
    import unohelper as _unohelper_check  # noqa: F401 — already imported above
    from com.sun.star.sheet import XAddIn  # type: ignore[import]
    from com.sun.star.lang import XServiceInfo  # type: ignore[import]
except (ImportError, ModuleNotFoundError):
    _IN_LIBREOFFICE = False


class PromptFunction(unohelper.Base):
    """
    LibreOffice Calc Add-In that exposes the =PROMPT() formula.

    The UNO service name matches the node declared in CalcAddIn.xcu.
    """

    IMPLEMENTATION_NAME = "fr.gouv.interieur.mirai.PromptFunction"
    SUPPORTED_SERVICES = ("com.sun.star.sheet.AddIn",)

    def __init__(self, ctx):
        self._ctx = ctx
        self._config: dict | None = None
        self._ssl_ctx: ssl.SSLContext | None = None

    # ------------------------------------------------------------------
    # Lazy initialisation helpers
    # ------------------------------------------------------------------

    def _get_config(self) -> dict:
        if self._config is None:
            try:
                self._config = load_config(self._ctx)
            except Exception as exc:
                _log(f"Config load failed: {exc}")
                self._config = {}
        return self._config

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_ctx is None:
            try:
                self._ssl_ctx = build_ssl_context(self._get_config())
            except Exception as exc:
                _log(f"SSL context build failed: {exc}")
                self._ssl_ctx = ssl.create_default_context()
        return self._ssl_ctx

    # ------------------------------------------------------------------
    # XAddIn interface (duck typing — IDL not compiled for this project)
    # ------------------------------------------------------------------

    def getProgrammaticFuntionName(self, display_name: str) -> str:  # noqa: N802 — UNO spelling
        if display_name.upper() == "PROMPT":
            return "prompt"
        return display_name.lower()

    def getDisplayFunctionName(self, programmatic_name: str) -> str:  # noqa: N802
        if programmatic_name == "prompt":
            return "PROMPT"
        return programmatic_name.upper()

    def getFunctionDescription(self, programmatic_name: str) -> str:  # noqa: N802
        if programmatic_name == "prompt":
            return "Send a prompt to the LLM and return the response."
        return ""

    def getDisplayArgumentName(self, programmatic_name: str, index: int) -> str:  # noqa: N802
        names = ["message", "system_prompt", "model", "max_tokens"]
        if 0 <= index < len(names):
            return names[index]
        return ""

    def getArgumentDescription(self, programmatic_name: str, index: int) -> str:  # noqa: N802
        descs = [
            "The prompt/question to send to the LLM.",
            "(Optional) System prompt / instructions for the LLM.",
            "(Optional) Model name to use (defaults to configured model).",
            "(Optional) Maximum number of tokens in the response (default: 2048).",
        ]
        if 0 <= index < len(descs):
            return descs[index]
        return ""

    def getProgrammaticCategoryName(self, programmatic_name: str) -> str:  # noqa: N802
        return "Text"

    def getDisplayCategoryName(self, programmatic_name: str) -> str:  # noqa: N802
        return "Text"

    # ------------------------------------------------------------------
    # XServiceInfo interface
    # ------------------------------------------------------------------

    def getImplementationName(self) -> str:  # noqa: N802
        return self.IMPLEMENTATION_NAME

    def supportsService(self, service_name: str) -> bool:  # noqa: N802
        return service_name in self.SUPPORTED_SERVICES

    def getSupportedServiceNames(self):  # noqa: N802
        return self.SUPPORTED_SERVICES

    # ------------------------------------------------------------------
    # The actual formula function
    # ------------------------------------------------------------------

    def prompt(
        self,
        message: str,
        system_prompt: str = "",
        model: str = "",
        max_tokens=2048,
    ) -> str:
        """
        =PROMPT(message; [system_prompt]; [model]; [max_tokens])

        Sends *message* to the configured LLM and returns the response text.
        All parameters except *message* are optional.

        Returns a string.  On error, returns a human-readable error description
        starting with "#PROMPT_ERROR:" so the cell shows useful diagnostic info.
        """
        try:
            max_tokens_int = int(max_tokens) if max_tokens else 2048
        except (TypeError, ValueError):
            max_tokens_int = 2048
        if max_tokens_int < 1:
            max_tokens_int = 2048

        config = self._get_config()

        # Use configured default model if the caller did not specify one
        effective_model = str(model or "").strip() or str(config.get("llm_default_models", "") or "").strip()

        ssl_ctx = self._get_ssl_context()

        return call_llm(
            message=str(message or ""),
            system_prompt=str(system_prompt or ""),
            model=effective_model,
            max_tokens=max_tokens_int,
            config=config,
            ssl_context=ssl_ctx,
        )


# ---------------------------------------------------------------------------
# UNO component registration boilerplate
# ---------------------------------------------------------------------------

def createInstance(ctx):  # noqa: N802 — UNO naming convention
    return PromptFunction(ctx)


def getServiceManager(ctx):  # noqa: N802
    return ctx.getServiceManager()


# UNO component registration table
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    PromptFunction,
    PromptFunction.IMPLEMENTATION_NAME,
    PromptFunction.SUPPORTED_SERVICES,
)
