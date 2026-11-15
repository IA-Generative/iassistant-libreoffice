"""Le dialogue « À propos » dit quand la version installée est une pré-version."""

import pytest

from src.mirai import i18n
from tests.stubs.uno_stubs import install

install()

from src.mirai.shell.version_label import version_label


@pytest.fixture(autouse=True)
def _french():
    previous = i18n.get_locale()
    i18n.set_locale("fr")
    yield
    i18n.set_locale(previous)


def test_a_general_version_is_shown_alone():
    assert version_label("0.4.0") == "Version 0.4.0"
    assert version_label("0.0.1.0.32") == "Version 0.0.1.0.32"


def test_a_release_candidate_is_named():
    assert version_label("0.4.0-rc.12") == "Version 0.4.0-rc.12 — Version candidate"


def test_a_trial_version_is_named():
    assert version_label("0.4.0-dev.7") == "Version 0.4.0-dev.7 — Version d'essai"


def test_the_label_follows_the_interface_language():
    i18n.set_locale("en")
    assert version_label("0.4.0-rc.12") == "Version 0.4.0-rc.12 — Release candidate"
    assert version_label("0.4.0-dev.7") == "Version 0.4.0-dev.7 — Trial version"
