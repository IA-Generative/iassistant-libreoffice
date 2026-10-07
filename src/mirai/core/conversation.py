"""Persistance minimum viable du fil de conversation.

Stockage local uniquement (profil utilisateur LibreOffice), jamais télémétré,
effaçable en un clic. Seuls les tours user/réponse finale sont persistés —
jamais les tool calls (éphémères au sein d'un run). Tolérant à la corruption :
un JSON invalide repart vide, jamais de crash.
"""

import json
import os

from .. import local_config

FILENAME = "assistant_conversation.json"
MAX_EXCHANGES = 20            # paires user+assistant conservées
MAX_BYTES = 100_000
DEFAULT_CONTEXT_MAX_CHARS = 4000


class ConversationStore:
    def __init__(self, directory, max_exchanges=MAX_EXCHANGES, max_bytes=MAX_BYTES):
        self._path = os.path.join(directory, FILENAME)
        self._max_entries = max_exchanges * 2
        self._max_bytes = max_bytes

    def load(self):
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                return []
            return [e for e in entries
                    if isinstance(e, dict) and e.get("role") and "text" in e]
        except Exception:
            return []

    def append(self, role, text, app=""):
        entries = self.load()
        entries.append({"role": str(role), "text": str(text), "app": str(app)})
        entries = entries[-self._max_entries:]
        while len(json.dumps({"entries": entries}, ensure_ascii=False)) > self._max_bytes \
                and len(entries) > 2:
            entries = entries[2:]   # retire la paire la plus ancienne
        self._write(entries)

    def clear(self):
        self._write([])

    def context_messages(self, max_chars=DEFAULT_CONTEXT_MAX_CHARS):
        """Derniers échanges au format messages[], plafonnés en caractères."""
        entries = self.load()
        selected = []
        total = 0
        for entry in reversed(entries):
            text = entry["text"]
            total += len(text)
            if total > max_chars and selected:
                break
            selected.append({"role": entry["role"], "content": text})
            if total > max_chars:
                break
        return list(reversed(selected))

    def _write(self, entries):
        if local_config.is_frozen():
            return
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump({"entries": entries}, fh, ensure_ascii=False, indent=1)
            os.replace(tmp_path, self._path)
        except Exception:
            pass
