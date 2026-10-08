"""Surface de test : les tests ne s'attachent pas davantage aux membres privés
du MainJob.

Chaque test qui cite un privé de MainJob casse à son extraction. Le compte par
fichier ne doit plus monter ; une PR d'extraction réaiguille les tests de sa
zone vers le nouveau module et abaisse les bornes. Un fichier absent de la
liste a une borne de zéro.
"""

import ast
import functools
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

PRIVATE_MEMBERS_BUDGET = {
    "tests/integration/test_full_enrollment_flow.py": 9,
    "tests/stubs/uno_stubs.py": 9,
    "tests/unit/conftest.py": 9,
    "tests/unit/core/test_shell_facade.py": 2,
    # Le routage vit encore dans des privés de MainJob ; ce test suivra son
    # extraction dans un module de dispatch.
    "tests/unit/rules/test_dispatch_routes.py": 9,
    "tests/unit/rules/test_import_graph.py": 1,
    "tests/unit/test_action_dispatch.py": 11,
    "tests/unit/test_bootstrap_insecure_ssl.py": 3,
    "tests/unit/test_calc_menu_actions.py": 5,
    "tests/unit/test_config_cache_secrets.py": 3,
    "tests/unit/test_config_fetch_perf.py": 12,
    "tests/unit/test_credentials.py": 1,
    "tests/unit/test_data_paths.py": 8,
    "tests/unit/test_enrollment.py": 8,
    "tests/unit/test_feed_rewrite.py": 19,
    "tests/unit/test_i18n.py": 1,
    "tests/unit/test_install_changes.py": 4,
    "tests/unit/test_llm_relay_error.py": 10,
    "tests/unit/test_llm_token_auth.py": 19,
    "tests/unit/test_log_hygiene.py": 18,
    "tests/unit/test_models_fetch.py": 4,
    "tests/unit/test_native_route.py": 34,
    "tests/unit/test_native_update.py": 16,
    "tests/unit/test_prompts_calc_path.py": 4,
    "tests/unit/test_reload_config.py": 3,
    "tests/unit/test_secret_routing.py": 6,
    "tests/unit/test_settings_token_field.py": 2,
    "tests/unit/test_storage_wiring.py": 10,
    "tests/unit/test_telemetry_defaults.py": 2,
    "tests/unit/test_token_logic.py": 4,
    "tests/unit/test_uninstall_wipe.py": 16,
    "tests/unit/test_update_blocked.py": 16,
    "tests/unit/test_update_features.py": 16,
}

MAKE_JOB_FILES_BUDGET = 30
# make_job est interdit dans ces dossiers, sauf pour ces fichiers : la façade
# et le routage ne se testent qu'avec un MainJob.
MAKE_JOB_FORBIDDEN_IN = ("tests/unit/core/", "tests/unit/rules/")
MAKE_JOB_ALLOWED = {"tests/unit/core/test_shell_facade.py",
                    "tests/unit/rules/test_dispatch_routes.py"}


@functools.cache
def _private_members():
    path = os.path.join(_REPO_ROOT, "src", "mirai", "entrypoint.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    mainjob = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "MainJob")

    def private(name):
        return name.startswith("_") and not name.startswith("__")

    names = set()
    for node in mainjob.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and private(node.name):
            names.add(node.name)
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name) and private(t.id)}
    for node in ast.walk(mainjob):
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name) and node.value.id == "self"
                and private(node.attr)):
            names.add(node.attr)
    return names


def _test_files():
    tests_dir = os.path.join(_REPO_ROOT, "tests")
    for root, dirs, files in os.walk(tests_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                yield os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/"), path


def _parse(path):
    return ast.parse(open(path, encoding="utf-8").read(), filename=path)


def test_tests_do_not_cite_more_mainjob_privates():
    privates = _private_members()
    over = {}
    for rel, path in _test_files():
        tree = _parse(path)
        cited = {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and node.attr in privates}
        cited |= {node.value for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and node.value in privates}
        budget = PRIVATE_MEMBERS_BUDGET.get(rel, 0)
        if len(cited) > budget:
            over[rel] = f"{len(cited)} > {budget}"
    assert over == {}, f"tests attachés à des privés de MainJob : {over}"


@functools.cache
def _make_job_files():
    found = set()
    for rel, path in _test_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and getattr(
                    node.func, "id", getattr(node.func, "attr", None)) == "make_job":
                found.add(rel)
                break
    return found


def test_make_job_is_not_spreading():
    files = _make_job_files()
    assert len(files) <= MAKE_JOB_FILES_BUDGET, (
        f"{len(files)} fichiers instancient MainJob (budget {MAKE_JOB_FILES_BUDGET})")
    misplaced = sorted(rel for rel in files
                       if rel.startswith(MAKE_JOB_FORBIDDEN_IN) and rel not in MAKE_JOB_ALLOWED)
    assert misplaced == []
