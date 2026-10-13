"""Tâches de démarrage déclarées : `MainJob.STARTUP_TASKS` nomme les méthodes
par lesquelles `MainJob.__init__` lance un thread ou un timer.

`make_job` désarme les tâches par cette liste : une tâche lancée par
`__init__` sans y figurer tournerait en fond pendant les tests. La lecture du
source ne suit que deux niveaux d'appel ; la construction par `make_job`,
Device Management actif, couvre les chemins plus profonds.
"""

import ast
import functools
import os
import threading

import pytest

from tests.stubs.uno_stubs import install, make_job, seed_user_config

install()

from src.mirai.entrypoint import MainJob

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


def _started_by_make_job(monkeypatch, tmp_path, disarmed):
    seed_user_config(str(tmp_path), {"enabled": True, "bootstrap_url": "https://dm.invalid"})
    monkeypatch.setattr(MainJob, "STARTUP_TASKS", disarmed)
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda thread: started.append(thread))
    make_job(config_dir=str(tmp_path))
    return started


def test_every_launcher_read_in_the_source_is_declared():
    assert set(_launched_by_init()) <= set(MainJob.STARTUP_TASKS)


def test_make_job_starts_nothing(monkeypatch, tmp_path):
    assert _started_by_make_job(monkeypatch, tmp_path, MainJob.STARTUP_TASKS) == []


@pytest.mark.parametrize("task", MainJob.STARTUP_TASKS)
def test_each_declared_task_starts_something(monkeypatch, tmp_path, task):
    disarmed = tuple(name for name in MainJob.STARTUP_TASKS if name != task)
    assert _started_by_make_job(monkeypatch, tmp_path, disarmed) != []
