"""Faux objets pour les tests Éloignement : propriétés personnalisées d'un
document, document Writer minimal, relais HTTP scripté et façade."""

import json
import os
import tempfile


class FakeUserProperties:
    """`DocumentProperties.UserDefinedProperties` à état réel."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.attributes = {}

    def getPropertySetInfo(self):
        props = self

        class _Info:
            def hasPropertyByName(self, name):
                return name in props.values

        return _Info()

    def getPropertyValue(self, name):
        return self.values[name]

    def setPropertyValue(self, name, value):
        if name not in self.values:
            raise KeyError(name)
        self.values[name] = value

    def addProperty(self, name, attributes, value):
        if name in self.values:
            raise ValueError("PropertyExistException")
        self.values[name] = value
        self.attributes[name] = attributes

    def removeProperty(self, name):
        del self.values[name]


class FakeDocumentProperties:
    def __init__(self, user=None):
        self.user = user or FakeUserProperties()
        self.Title = ""

    def getUserDefinedProperties(self):
        return self.user


class FakeLinkedDoc:
    """Document Writer réduit à ce que la session Éloignement touche :
    propriétés, emplacement, état modifié, enregistrement, écouteurs."""

    def __init__(self, location="", text="", user_values=None):
        self.props = FakeDocumentProperties(FakeUserProperties(user_values))
        self.location = location
        self.text = text
        self.modified = False
        self.stored = 0
        self.listeners = []
        self.copies = []

    def getDocumentProperties(self):
        return self.props

    def getLocation(self):
        return self.location

    def isModified(self):
        return self.modified

    def store(self):
        self.stored += 1
        self.modified = False
        if self.location.startswith("file://"):
            path = self.location[len("file://"):]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"text": self.text, "props": self.props.user.values}, fh)

    def storeToURL(self, url, _args):
        """Copie : le fichier emporte les propriétés, le document ne change pas."""
        self.copies.append((url, dict(self.props.user.values)))

    def addDocumentEventListener(self, listener):
        self.listeners.append(listener)

    def removeDocumentEventListener(self, listener):
        self.listeners.remove(listener)

    def getBookmarks(self):
        raise AttributeError("pas de signets dans ce faux document")


class ScriptedRelay:
    """`send(method, path, body, headers, timeout)` qui rejoue des réponses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, path, body, headers, timeout):
        self.calls.append({"method": method, "path": path, "body": body,
                           "headers": dict(headers or {}), "timeout": timeout})
        if not self.responses:
            raise AssertionError(f"appel inattendu : {method} {path}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, headers, body = response
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        return status, headers or {}, body or b""


class FakeEloignementShell:
    """Façade minimale pour la session : dossier de données, interrupteur,
    relais scripté, journal."""

    def __init__(self, relay=None, enabled=True, data_dir=None):
        self.relay = relay or ScriptedRelay()
        self.enabled = enabled
        self.directory = data_dir or tempfile.mkdtemp(prefix="mirai-elo-")
        self.uno_ctx = None
        self.logs = []
        self.config = {}

    def data_dir(self):
        return self.directory

    def feature_enabled(self, name):
        return self.enabled and name == "eloignement"

    def feature_state(self, name):
        return self.feature_enabled(name)

    def tools_request(self, method, path, body=None, headers=None, timeout=20):
        return self.relay(method, path, body, headers, timeout)

    def log(self, message):
        self.logs.append(message)

    def telemetry(self, *_args, **_kwargs):
        pass

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


def saved_file(directory, name="acte.odt", content=b"PK-odt"):
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(content)
    return path
