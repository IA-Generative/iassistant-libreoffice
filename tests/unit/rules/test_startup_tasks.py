"""Tâches de démarrage déclarées : celles que `MainJob.__init__` lance en fond
sont exactement `MainJob.STARTUP_TASKS`, dans cet ordre.

`make_job` désarme les tâches par cette liste : une tâche lancée par
`__init__` sans y figurer tournerait en fond pendant les tests.
"""

import ast
import functools
import os

_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "src", "mirai", "entrypoint.py"))


@functools.cache
def _tree():
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=_ENTRYPOINT)


def _mainjob():
    return next(node for node in _tree().body
                if isinstance(node, ast.ClassDef) and node.name == "MainJob")


def _creates_thread(function):
    return any(isinstance(node, ast.Call)
               and getattr(node.func, "attr", getattr(node.func, "id", None)) in ("Thread", "Timer")
               for node in ast.walk(function))


def _callees(function, methods, module_functions):
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "self" and func.attr in methods):
            yield methods[func.attr]
        elif isinstance(func, ast.Name) and func.id in module_functions:
            yield module_functions[func.id]


def _launched_by_init():
    """Méthodes appelées par `__init__` qui créent un thread ou un timer,
    elles-mêmes ou par un appel direct, dans l'ordre du source."""
    methods = {node.name: node for node in _mainjob().body
               if isinstance(node, ast.FunctionDef)}
    module_functions = {node.name: node for node in _tree().body
                        if isinstance(node, ast.FunctionDef)}
    calls = sorted((node for node in ast.walk(methods["__init__"])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
                    and node.func.attr in methods),
                   key=lambda node: (node.lineno, node.col_offset))
    launched = []
    for call in calls:
        method = methods[call.func.attr]
        if method.name in launched:
            continue
        if _creates_thread(method) or any(
                _creates_thread(callee)
                for callee in _callees(method, methods, module_functions)):
            launched.append(method.name)
    return launched


def _declared():
    for node in _mainjob().body:
        if (isinstance(node, ast.Assign)
                and any(getattr(target, "id", None) == "STARTUP_TASKS" for target in node.targets)):
            return list(ast.literal_eval(node.value))
    raise AssertionError("MainJob.STARTUP_TASKS absent")


def test_init_launches_exactly_the_declared_startup_tasks():
    assert _launched_by_init() == _declared()
