"""Règles d'architecture : qui a le droit d'importer qui.

Couches : les modules communs (à plat dans src/mirai, sans UNO), le moteur
(core), la palette (ui), la coquille (entrypoint) et l'add-in =PROMPT(). Le
legacy (menu_actions, formatting, security_flow) est hors matrice : seule la
coquille l'importe encore, en attendant sa suppression.

Les écarts tolérés sont nommés ici, avec la raison de leur fin prévue. La liste
ne peut que raccourcir : un écart disparu doit en être retiré.
"""

import ast
import functools
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SRC = os.path.join(_REPO_ROOT, "src", "mirai")

COMMON = {"local_config", "credentials", "log_setup", "feed_rewrite", "i18n"}
LEGACY = {"menu_actions", "formatting", "security_flow"}
SHELL = {"entrypoint", "shell"}
ADDIN = "calc_prompt_function"
# Ce que le moteur et la palette prennent dans les modules communs ; le reste
# (configuration, coffres, feed) passe par la façade.
COMMON_FOR_ENGINE = {"log_setup", "i18n"}

TOLERATED_EDGES = {
    ("core.conversation", "local_config"): "le gel de l'écriture passera par la façade",
}
# Un import UNO paresseux échoue hors du thread principal : seules ces fonctions,
# appelées sur le thread principal, y ont droit.
UNO_IMPORTS_IN_FUNCTIONS = {
    ("i18n", "_uno_ui_locale"): "locale bientôt injectée par la coquille",
    ("calc_prompt_function", "_user_config_path"): "appelée par LibreOffice sur le thread principal",
}
# Accès directs au MainJob depuis le moteur ou la palette, hors de la façade.
FACADE_BYPASS = {
    "core/entry.py": {"_show_message", "ctx"},
}


def _modules():
    for root, dirs, files in os.walk(_SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                rel = os.path.relpath(path, _SRC)[:-3].split(os.sep)
                if rel[-1] == "__init__":
                    rel = rel[:-1]
                if rel:
                    yield ".".join(rel), path


def _enclosing_function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


def _is_uno(name):
    return name.split(".")[0] in ("uno", "unohelper", "com")


def _import_targets(node):
    for alias in node.names:
        if _is_uno(alias.name):
            yield "<uno>"
        elif alias.name.startswith("src.mirai."):
            yield alias.name[len("src.mirai."):]


def _import_from_targets(node, package):
    name = node.module or ""
    if node.level:
        base = package[:len(package) - node.level + 1]
        if name:
            yield ".".join(base + [name])
        else:
            yield from (".".join(base + [alias.name]) for alias in node.names)
    elif _is_uno(name):
        yield "<uno>"
    elif name == "src.mirai":
        yield from (alias.name for alias in node.names)
    elif name.startswith("src.mirai."):
        yield name[len("src.mirai."):]


def _imports(module, path):
    """(cible, fonction englobante) : cible = module interne, ou "<uno>"."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    package = module.split(".")
    if not path.endswith("__init__.py"):
        package = package[:-1]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = _import_targets(node)
        elif isinstance(node, ast.ImportFrom):
            targets = _import_from_targets(node, package)
        else:
            continue
        function = _enclosing_function(node, parents)
        found.extend((target, function) for target in targets)
    return found


def _layer(module):
    top = module.split(".")[0]
    if top in ("core", "ui"):
        return top
    if top in COMMON:
        return "common"
    if top in SHELL:
        return "shell"
    if top == ADDIN:
        return "addin"
    if top in LEGACY:
        return "legacy"
    return None


@functools.cache
def _edges():
    return [(module, target, function)
            for module, path in _modules()
            for target, function in _imports(module, path)]


def test_every_module_belongs_to_a_layer():
    unknown = sorted(module for module, _ in _modules() if module and _layer(module) is None)
    assert unknown == [], f"modules hors de toute couche, à classer ici : {unknown}"


def test_common_modules_need_only_the_standard_library():
    offenders = []
    for module, target, function in _edges():
        if _layer(module) != "common":
            continue
        if target == "<uno>":
            if (module, function) not in UNO_IMPORTS_IN_FUNCTIONS:
                offenders.append(f"{module} importe UNO ({function or 'au niveau du module'})")
        elif _layer(target) != "common":
            offenders.append(f"{module} -> {target}")
    assert offenders == []


def test_engine_and_palette_stay_behind_the_facade():
    offenders = []
    for module, target, function in _edges():
        source = _layer(module)
        if source not in ("core", "ui") or target == "<uno>":
            continue
        layer = _layer(target)
        if layer in ("core", "ui") and (source == "ui" or layer == "core"):
            continue
        if source == "core" and layer == "ui" and module == "core.entry" and function:
            continue
        if layer == "common" and (target.split(".")[0] in COMMON_FOR_ENGINE
                                  or (module, target) in TOLERATED_EDGES):
            continue
        offenders.append(f"{module} -> {target}")
    assert offenders == []


def test_addin_uses_only_common_modules():
    offenders = []
    for module, target, function in _edges():
        if _layer(module) != "addin":
            continue
        if target == "<uno>":
            if function and (module, function) not in UNO_IMPORTS_IN_FUNCTIONS:
                offenders.append(f"{module} importe UNO dans {function}")
        elif _layer(target) != "common":
            offenders.append(f"{module} -> {target}")
    assert offenders == []


def test_legacy_shell_and_addin_are_never_imported_by_the_new_layers():
    offenders = [f"{module} -> {target}" for module, target, _ in _edges()
                 if target != "<uno>"
                 and _layer(target) in ("legacy", "shell", "addin")
                 and _layer(module) in ("common", "core", "ui", "addin")]
    assert offenders == []


def test_tolerated_exceptions_are_still_needed():
    edges = {(module, target) for module, target, _ in _edges()}
    uno_sites = {(module, function) for module, target, function in _edges() if target == "<uno>"}
    stale = [edge for edge in TOLERATED_EDGES if edge not in edges]
    stale += [site for site in UNO_IMPORTS_IN_FUNCTIONS if site not in uno_sites]
    assert stale == [], f"écarts disparus, à retirer de la liste : {stale}"


def test_common_modules_and_addin_never_pump_events():
    offenders = []
    for module, path in _modules():
        if _layer(module) not in ("common", "addin"):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        offenders += [f"{module}:{node.lineno}" for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute) and node.attr == "processEventsToIdle"]
    assert offenders == []


def test_engine_reaches_mainjob_only_through_the_facade():
    """Le moteur et la palette ne voient le MainJob qu'à travers la façade
    (core/shell_facade.py) ; l'objet y circule sous le nom `job`."""
    found = {}
    for module, path in _modules():
        rel = os.path.relpath(path, _SRC).replace(os.sep, "/")
        if _layer(module) not in ("core", "ui") or rel == "core/shell_facade.py":
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        attrs = {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)
                 and isinstance(node.value, ast.Name) and node.value.id == "job"}
        if attrs:
            found[rel] = attrs
    assert found == FACADE_BYPASS
