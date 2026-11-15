"""Numéro d'une pré-version : la prochaine version de master, suivie de
-rc.N ou -dev.N (`scripts/prerelease-version.sh`). GitHub est remplacé par un
faux `gh` qui rejoue ses réponses."""

import os
import stat
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "prerelease-version.sh")
PR_BRANCH = "release-please--branches--master--components--mirai-libreoffice"

FAKE_GH = """#!/usr/bin/env bash
# pr list : la branche de la PR release-please ouverte, ou rien.
# api     : le manifeste de la branche demandée (-f ref=<branche>).
if [ "$1" = "pr" ]; then
  printf '%s\\n' "$FAKE_PR_BRANCH"
  exit 0
fi
for arg in "$@"; do
  case "$arg" in
    ref=master) printf '%s' "$FAKE_MASTER_MANIFEST"; exit 0 ;;
    ref=*)      printf '%s' "$FAKE_PR_MANIFEST"; exit 0 ;;
  esac
done
exit 1
"""


@pytest.fixture
def run(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    def _run(*args, **fake):
        env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
               "GITHUB_REPOSITORY": "org/depot", "FAKE_PR_BRANCH": "",
               "FAKE_MASTER_MANIFEST": '{".":"0.3.0"}', "FAKE_PR_MANIFEST": "", **fake}
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env)
    return _run


def test_without_a_release_pr_the_next_version_is_the_next_minor(run):
    res = run("rc", "12")
    assert (res.returncode, res.stdout.strip()) == (0, "0.4.0-rc.12")
    res = run("dev", "7", FAKE_MASTER_MANIFEST='{".":"1.9.4"}')
    assert (res.returncode, res.stdout.strip()) == (0, "1.10.0-dev.7")


def test_an_open_release_pr_names_the_next_version(run):
    res = run("rc", "13", FAKE_PR_BRANCH=PR_BRANCH, FAKE_PR_MANIFEST='{".":"0.3.1"}')
    assert (res.returncode, res.stdout.strip()) == (0, "0.3.1-rc.13")
    assert PR_BRANCH in res.stderr


@pytest.mark.parametrize("args", [("beta", "1"), ("rc", "x"), ("rc", ""), ("rc",)])
def test_an_unknown_kind_or_number_is_refused(run, args):
    res = run(*args)
    assert res.returncode != 0 and res.stdout == ""


def test_an_unreadable_version_is_refused(run):
    res = run("rc", "1", FAKE_MASTER_MANIFEST='{".":"0.3.0-beta"}')
    assert res.returncode != 0 and res.stdout == ""
    res = run("rc", "1", FAKE_PR_BRANCH=PR_BRANCH, FAKE_PR_MANIFEST='{".":"next"}')
    assert res.returncode != 0 and res.stdout == ""
