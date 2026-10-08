"""Chemin d'installation unique : la mise à jour passe par
ExtensionManager.addExtension, et plus jamais par le gestionnaire de paquets
bas niveau.

Le remove/add de ce dernier ajoute le paquet sans l'enregistrer : après
redémarrage, la nouvelle version est listée mais désactivée (incident du
2026-10-05).
"""

import ast
import functools
import os

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))


@functools.cache
def _calls_and_strings():
    calls, strings = [], []
    for root, dirs, files in os.walk(_SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            where = os.path.relpath(path, _SRC)
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    calls.append((node.func.attr, f"{where}:{node.lineno}"))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    strings.append((node.value, f"{where}:{node.lineno}"))
    return calls, strings


def test_the_low_level_package_manager_is_never_called():
    calls, strings = _calls_and_strings()
    offenders = [site for name, site in calls if name in ("removePackage", "addPackage")]
    offenders += [site for value, site in strings if "thePackageManagerFactory" in value]
    assert offenders == []


def test_add_extension_has_a_single_call_site():
    calls, _ = _calls_and_strings()
    sites = [site for name, site in calls if name == "addExtension"]
    assert len(sites) == 1, sites
