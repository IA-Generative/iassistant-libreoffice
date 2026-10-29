"""Fixtures de tous les tests : dossier personnel isolé, palette sur un faux dialogue."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_legacy_home(monkeypatch, tmp_path_factory):
    # La migration supprime ~/log.txt s'il vient de l'extension : jamais le
    # vrai dossier personnel du développeur pendant les tests.
    from src.mirai import local_config
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(local_config, "legacy_home_dir", lambda: str(home))


@pytest.fixture
def palette_module(monkeypatch):
    from src.mirai.core.ui_thread import DirectDispatcher
    from src.mirai.ui import dsfr
    from src.mirai.ui import palette as palette_module
    from tests.stubs.fake_palette_dialog import FakeDialog

    dialog = FakeDialog()
    monkeypatch.setattr(dsfr, "make_dialog",
                        lambda *_a, **_k: (dialog, dialog.model))
    monkeypatch.setattr(dsfr, "probe_font", lambda _toolkit: "Arial")
    monkeypatch.setattr(palette_module, "_open_palette", [None])
    # Dispatcher SYNCHRONE : avec le vrai, le service AsyncCallback est un
    # MagicMock qui accepte les tâches sans jamais les exécuter — les
    # assertions porteraient alors sur un affichage jamais mis à jour.
    monkeypatch.setattr(palette_module, "MainThreadDispatcher",
                        lambda _ctx, log=None: DirectDispatcher())
    monkeypatch.setattr(palette_module, "_fake_dialog", dialog, raising=False)
    return palette_module
