"""Règle d'architecture exécutable : core/ et ui/ n'importent JAMAIS entrypoint.

La façade duck-type l'objet MainJob sans import — c'est ce qui garantit que le
moteur reste testable sans UNO et que la coquille reste intouchée.
"""

import ast
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_PACKAGES = [
    os.path.join(_REPO_ROOT, "src", "mirai", "core"),
    os.path.join(_REPO_ROOT, "src", "mirai", "ui"),
]


def _python_files():
    for package in _PACKAGES:
        if not os.path.isdir(package):
            continue
        for root, _dirs, files in os.walk(package):
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def test_core_and_ui_never_mention_entrypoint():
    offenders = []
    for path in _python_files():
        with open(path, encoding="utf-8") as fh:
            if "entrypoint" in fh.read():
                offenders.append(os.path.relpath(path, _REPO_ROOT))
    assert offenders == [], (
        "Ces fichiers mentionnent 'entrypoint' — la règle d'architecture "
        f"interdit tout couplage direct : {offenders}")


def test_tools_never_import_ui():
    tools_dir = os.path.join(_REPO_ROOT, "src", "mirai", "core", "tools")
    offenders = []
    for root, _dirs, files in os.walk(tools_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            if "from ..ui" in content or "mirai.ui" in content:
                offenders.append(os.path.relpath(path, _REPO_ROOT))
    assert offenders == []


def test_core_and_ui_never_pump_events():
    """Aucun processEventsToIdle dans le nouveau cœur.

    Le run vit dans un thread worker et repasse par MainThreadDispatcher ; le
    thread principal n'est jamais immobilisé dans une boucle de drain. Pomper
    les événements depuis un dispatch imbriqué gèle LibreOffice et peut
    l'aborter (std::terminate dans DispatchUserEvents) — la contrainte
    historique disparaît ici par construction, pas par vigilance.
    """
    # On inspecte l'AST plutôt que le texte : une docstring qui explique la
    # règle doit rester permise, seul un accès réel à l'attribut est fautif.
    offenders = []
    for path in _python_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "processEventsToIdle":
                offenders.append(f"{os.path.relpath(path, _REPO_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "processEventsToIdle est interdit dans core/ et ui/ : tout ce qui "
        f"touche l'UI passe par MainThreadDispatcher. Occurrences : {offenders}")
