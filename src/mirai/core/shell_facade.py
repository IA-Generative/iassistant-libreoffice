"""Façade coquille → moteur.

Seul module autorisé à connaître l'objet MainJob (par duck-typing, sans
import). Le moteur (registry, orchestrateur, LLM, UI) ne voit que
ShellServices : transport HTTP authentifié, config, télémétrie, journal.
Invariant : aucune logique métier ici — uniquement de l'adaptation.
"""

import json
import urllib.request

# Clamps max_tokens par modèle — miroir de make_api_request (coquille).
# make_chat_request ne les applique pas ; la façade comble ce trou.
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


class MainJobShell:
    """Adaptateur concret au-dessus d'une instance MainJob (duck-typée)."""

    def __init__(self, job):
        self._job = job

    # ── Config ──────────────────────────────────────────────────────────
    def get_config(self, key, default=None):
        return self._job.get_config(key, default)

    def set_config(self, key, value):
        return self._job.set_config(key, value)

    def data_dir(self):
        return self._job._data_dir()

    # ── Transport LLM ───────────────────────────────────────────────────
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
        rebuilt.get_method = lambda: "POST"
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

    def request_timeout(self):
        try:
            timeout = int(self.get_config("llm_request_timeout_seconds", 45))
        except Exception:
            timeout = 45
        return max(timeout, 5)

    def extract_content(self, chunk):
        return self._job.extract_content_from_response(chunk, "chat")

    # ── Erreurs / télémétrie / journal ──────────────────────────────────
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

    # ── UNO ─────────────────────────────────────────────────────────────
    @property
    def uno_ctx(self):
        return self._job.ctx

    def toolkit(self):
        ctx = self._job.ctx
        return ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.awt.Toolkit", ctx
        )
