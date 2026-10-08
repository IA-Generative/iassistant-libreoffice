"""Registre des tools — miroir interne des concepts MCP (tools/list, tools/call).

Pas de serveur, pas de stdio, pas de JSON-RPC : un futur pont MCP réel n'est
qu'un sérialiseur au-dessus de ToolSpec/ToolResult. `call_tool` ne lève
jamais : toute exception du handler devient un ToolResult(ok=False) que le
LLM peut corriger.
"""

import time

from .tool_calls import ToolResult, validate_args

DEFAULT_RESULT_MAX_CHARS = 6000
UNDO_LABEL = "MIrAI — Assistant"


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, spec):
        self._tools[spec.name] = spec

    def list_tools(self, app):
        return [spec for spec in self._tools.values() if app in spec.apps]

    def openai_tools(self, app):
        """Format tools de l'API OpenAI (mode natif)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters or {"type": "object", "properties": {}},
                },
            }
            for spec in self.list_tools(app)
        ]

    def prompt_catalog(self, app):
        """Catalogue textuel des tools pour le prompt système (mode JSON)."""
        lines = []
        for spec in self.list_tools(app):
            params = (spec.parameters or {}).get("properties", {})
            required = set((spec.parameters or {}).get("required", []))
            args_desc = ", ".join(
                f"{name}: {schema.get('type', 'any')}"
                + ("" if name in required else " (optionnel)")
                for name, schema in params.items()
            )
            lines.append(f"- {spec.name}({args_desc}) : {spec.description}")
        return "\n".join(lines)

    def call_tool(self, name, arguments, ctx, call_id=""):
        started = time.monotonic()
        spec = self._tools.get(name)
        if spec is None or ctx.app not in spec.apps:
            result = ToolResult(
                call_id=call_id, ok=False, content="",
                error=f"Outil inconnu : '{name}'. Utilise uniquement les outils listés.",
            )
            self._finish(ctx, name, result, started, error_kind="unknown_tool")
            return result

        ok, error_message, coerced = validate_args(spec.parameters or {}, arguments or {})
        if not ok:
            result = ToolResult(call_id=call_id, ok=False, content="", error=error_message)
            self._finish(ctx, name, result, started, error_kind="invalid_args")
            return result

        # Combien d'arguments le validateur a-t-il réécrits ("3"→3, 50 000
        # ramené sous le plafond) ? Sans ce compte, les appels rattrapés en
        # silence sont indistinguables des appels propres.
        coerced_count = sum(
            1 for key, value in (arguments or {}).items()
            if key in coerced and coerced[key] != value)

        if spec.mutates:
            ctx.undo_begin(UNDO_LABEL)

        try:
            result = spec.handler(ctx, coerced)
            result.call_id = call_id
            error_kind = "" if result.ok else "tool_error"
        except Exception as exc:
            result = ToolResult(
                call_id=call_id, ok=False, content="",
                error=f"Échec de l'outil {name} : {exc}",
            )
            error_kind = "exception"

        try:
            max_chars = int(ctx.shell.get_config("tool_result_max_chars", DEFAULT_RESULT_MAX_CHARS))
        except Exception:
            max_chars = DEFAULT_RESULT_MAX_CHARS
        if result.content and len(result.content) > max_chars:
            result.content = result.content[:max_chars] + "\n[... tronqué ...]"

        self._finish(ctx, name, result, started, error_kind=error_kind,
                     coerced=coerced_count)
        return result

    def _finish(self, ctx, name, result, started, error_kind="", coerced=0):
        # Télémétrie fonctionnelle : nom du tool + statut + durée + motif
        # d'échec + nombre d'arguments réécrits. Jamais les arguments ni le
        # contenu (contrat DM — pas de données documentaires). Types réels
        # (bool/int), agrégeables côté observabilité.
        try:
            attrs = {
                "plugin.action": "assistant.tool",
                "tool.name": name,
                "tool.ok": bool(result.ok),
                "tool.duration_ms": int((time.monotonic() - started) * 1000),
            }
            if not result.ok and error_kind:
                attrs["tool.error_kind"] = error_kind
            if coerced:
                attrs["tool.args_coerced"] = int(coerced)
            ctx.shell.telemetry("AssistantToolCall", attrs)
        except Exception:
            pass
