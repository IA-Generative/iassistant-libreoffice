"""Façade coquille → moteur.

Seul module autorisé à connaître l'objet MainJob (par duck-typing, sans
import). Le moteur (registry, orchestrateur, LLM, UI) ne voit que
ShellServices : transport HTTP authentifié, config, télémétrie, journal.
Invariant : aucune logique métier ici — uniquement de l'adaptation.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from .links import find_link

# Clamps max_tokens par modèle. make_chat_request ne les applique pas ; la
# façade comble ce trou.
MODEL_TOKEN_LIMITS = {
    "deepseek-r1-distill-llama-70b": 8196,
    "llama-3.3-70b-instruct": 4096,
}


def clamp_max_tokens(model, max_tokens):
    """Retourne max_tokens plafonné selon le modèle (0 = pas de plafond trouvé)."""
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        return max_tokens
    model_lower = str(model or "").lower()
    for key, limit in MODEL_TOKEN_LIMITS.items():
        if key in model_lower:
            return min(max_tokens, limit)
    return max_tokens


def feature_from(features, name, config_value):
    """`features` du DM d'abord (strictement True), sinon la clé de
    configuration `<name>_enabled`. None : le DM n'a encore rien dit."""
    if isinstance(features, dict) and name in features:
        return features[name] is True
    if config_value is None or config_value == "":
        return False if features else None
    return config_value is True or str(config_value).strip().lower() in ("1", "true", "yes", "on")


def build_tools_request(endpoint, method, path, body, headers, relay_headers, token):
    """Requête vers le relais d'outils du DM (https, sauf poste local)."""
    endpoint = str(endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise RuntimeError("relais d'outils non configuré par le DM (toolsEndpoint)")
    parsed = urllib.parse.urlparse(endpoint)
    local = parsed.hostname in ("localhost", "127.0.0.1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise RuntimeError("relais d'outils refusé : https exigé")
    token = str(token or "").strip()
    if not token:
        raise PermissionError("non connecté : la session MirAI est nécessaire")
    all_headers = dict(headers or {})
    all_headers.update(relay_headers or {})
    all_headers["X-User-Authorization"] = f"Bearer {token}"
    url = endpoint + ("/" + path.lstrip("/") if path else "")
    return urllib.request.Request(url, data=body, headers=all_headers, method=method.upper())


class MainJobShell:
    """Adaptateur concret au-dessus d'une instance MainJob (duck-typée)."""

    def __init__(self, job):
        self._job = job

    def get_config(self, key, default=None):
        return self._job.get_config(key, default)

    def set_config(self, key, value):
        return self._job.set_config(key, value)

    def data_dir(self):
        return self._job._data_dir()

    def get_link(self, link_id):
        """Lien distribué par le DM pour ce rôle ({label, url, hint?, hintUrl?}) ou None."""
        return find_link(self.get_config("links", []), link_id)

    def open_link(self, url):
        """Ouvre l'URL dans le navigateur de l'utilisateur (jamais dans le journal)."""
        # Aucune clé de config à lire : l'URL explicite court-circuite la recherche.
        self._job._open_url_config("", url=url)

    def build_chat_request(self, messages, max_tokens=2000, extra_body=None):
        """Request chat/completions authentifiée, clamps réappliqués, corps enrichi.

        Endpoint, auth et modèle restent résolus par la coquille
        (make_chat_request) ; on ne fait que réécrire le corps JSON.
        """
        request = self._job.make_chat_request(messages, max_tokens)
        try:
            body = json.loads(request.data.decode("utf-8"))
        except Exception:
            return request

        model = str(body.get("model", ""))
        clamped = clamp_max_tokens(model, body.get("max_tokens", max_tokens))
        if clamped != body.get("max_tokens"):
            body["max_tokens"] = clamped
            body["max_completion_tokens"] = clamped
        if extra_body:
            body.update(extra_body)

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = dict(request.header_items())
        rebuilt = urllib.request.Request(request.full_url, data=data, headers=headers)
        return rebuilt

    def recover_llm_auth(self):
        """Tente de restaurer un credential LLM valide après un 401.

        À n'appeler que depuis le thread réseau : la coquille y fait un refresh
        /config et, si besoin, un ré-enrôlement — deux appels bloquants.
        """
        try:
            return bool(self._job._recover_llm_auth())
        except Exception as exc:
            self.log(f"[llm-auth] recovery indisponible: {exc}")
            return False

    def urlopen(self, request, timeout=None):
        """Ouvre la requête via le transport coquille (proxy + SSL + relais)."""
        context = self._job.get_ssl_context(getattr(request, "full_url", None))
        return self._job._urlopen(request, context=context, timeout=timeout)

    def feature_state(self, name):
        """Interrupteur de fonctionnalité : True, False, ou None tant que le DM
        n'a pas encore répondu (voir `feature_from`)."""
        return feature_from(getattr(self._job, "_features_cache", None),
                            name, self.get_config(f"{name}_enabled", None))

    def feature_enabled(self, name):
        """Éteint par défaut, y compris tant que le DM n'a pas répondu."""
        return self.feature_state(name) is True

    def tools_request(self, method, path, body=None, headers=None, timeout=20):
        """Appel au relais d'outils du DM : `{toolsEndpoint}/<path>`.

        Paire relais + jeton de l'agent (`X-User-Authorization`) ; le DM pose
        le jeton de service et l'identité de l'agent. Rend (statut, en-têtes,
        octets), y compris pour une réponse en erreur HTTP. Réseau bloquant :
        thread de travail uniquement.
        """
        request = build_tools_request(
            self.get_config("toolsEndpoint", ""), method, path, body, headers,
            self._job._relay_headers(), self._job._get_openwebui_access_token())
        try:
            with self.urlopen(request, timeout=timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            try:
                data = exc.read()
            except Exception:
                data = b""
            return exc.code, exc.headers, data

    def request_timeout(self):
        try:
            timeout = int(self.get_config("llm_request_timeout_seconds", 45))
        except Exception:
            timeout = 45
        return max(timeout, 5)

    def report_llm_error(self, status_code, body, headers=None):
        """Émet LlmRelayError (parse + dédup côté coquille). Jamais de contenu."""
        try:
            error_code, retry_after = self._job._parse_llm_error(status_code, body, headers)
            request_id = ""
            try:
                if headers:
                    request_id = str(headers.get("X-Request-Id", "") or "")
            except Exception:
                pass
            self._job._send_llm_relay_error(
                status_code, error_code, retry_after=retry_after, request_id=request_id
            )
            return error_code, retry_after
        except Exception:
            return "", None

    def report_llm_network_error(self, reason):
        try:
            code = "timeout" if "timed out" in str(reason).lower() else "network_error"
            self._job._send_llm_relay_error(0, code)
        except Exception:
            pass

    def telemetry(self, span_name, attributes=None):
        try:
            self._job._send_telemetry(span_name, attributes)
        except Exception:
            pass

    def log(self, message):
        try:
            self._job._log(message)
        except Exception:
            pass

    @property
    def uno_ctx(self):
        return self._job.ctx

    def toolkit(self):
        ctx = self._job.ctx
        return ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.awt.Toolkit", ctx
        )
