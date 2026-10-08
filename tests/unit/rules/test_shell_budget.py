"""Budget de la coquille : entrypoint.py ne grossit plus.

Chaque indicateur a une borne égale à sa valeur actuelle. Une PR qui en fait
monter un échoue : extraire ce qu'elle ajoute, ou relever la borne ici en le
justifiant dans la PR. Une PR qui en fait baisser un doit abaisser la borne,
pour que le cliquet ne fasse que descendre.
"""

import ast
import functools
import os

import pytest

_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "src", "mirai", "entrypoint.py"))

BUDGET = {
    "lines": 10425,
    "mainjob_methods": 166,
    "class_state_attributes": 18,
    "threads_and_timers": 22,
    "functions_over_100_lines": 23,
    "except_pass": 240,
    "uno_imports_in_functions": 26,
}


@functools.cache
def _measure():
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=_ENTRYPOINT)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def in_function(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return True
        return False

    def is_uno(name):
        return (name or "").split(".")[0] in ("uno", "unohelper", "com")

    mainjob = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "MainJob")
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return {
        "lines": source.count("\n"),
        "mainjob_methods": sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                               for node in mainjob.body),
        "class_state_attributes": sum(
            isinstance(target, ast.Name) and target.id.endswith("_cls")
            for node in mainjob.body if isinstance(node, ast.Assign)
            for target in node.targets),
        "threads_and_timers": sum(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("Thread", "Timer")
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "threading"
            for node in ast.walk(tree)),
        "functions_over_100_lines": sum(node.end_lineno - node.lineno + 1 > 100
                                        for node in functions),
        "except_pass": sum(isinstance(node, ast.ExceptHandler) and len(node.body) == 1
                           and isinstance(node.body[0], ast.Pass)
                           for node in ast.walk(tree)),
        "uno_imports_in_functions": sum(
            in_function(node) and (
                (isinstance(node, ast.ImportFrom) and is_uno(node.module))
                or (isinstance(node, ast.Import) and any(is_uno(a.name) for a in node.names)))
            for node in ast.walk(tree)),
    }


@pytest.mark.parametrize("indicator", sorted(BUDGET))
def test_the_shell_does_not_grow(indicator):
    value = _measure()[indicator]
    assert value <= BUDGET[indicator], (
        f"{indicator} : {value} > {BUDGET[indicator]}. La coquille grossit : "
        "extraire ce qui est ajouté, ou relever la borne en le justifiant.")


@pytest.mark.parametrize("indicator", sorted(BUDGET))
def test_the_budget_follows_every_improvement(indicator):
    value = _measure()[indicator]
    assert value >= BUDGET[indicator], (
        f"{indicator} est descendu à {value} : abaisser la borne de "
        f"{BUDGET[indicator]} à {value} dans ce fichier.")
