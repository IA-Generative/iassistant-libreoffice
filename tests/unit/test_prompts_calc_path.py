"""Tests de l'historique des prompts Calc (issue #31).

Le défaut d'origine : `_prompts_calc_path()` lisait `self._profile_config_path`,
un attribut jamais affecté. L'`AttributeError` était rattrapée par un `except`
qui repliait sur `~/prompts_calc.txt` — donc tout l'historique des demandes
Calc s'écrivait en clair dans le dossier personnel de l'utilisateur, sans que
rien ne le signale.

Ces tests verrouillent les deux propriétés qui comptent : l'historique va dans
le profil LibreOffice, et il ne va JAMAIS dans le HOME.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()


class TestPromptsCalcPath(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Filet de sécurité : si ces tests sont un jour rejoués contre un
        # checkout ANTÉRIEUR au correctif (bisect, archéologie), l'ancien code
        # repliait sur ~/prompts_calc.txt — et `_save_prompt_calc` fait un
        # read-modify-write plafonné à 100 lignes, donc écrire 120 entrées de
        # test chassait le vrai historique de l'utilisateur. C'est arrivé.
        # On isole donc le HOME pour que ce repli ne puisse toucher personne.
        self._fake_home = tempfile.mkdtemp()
        self._real_home = os.environ.get("HOME")
        os.environ["HOME"] = self._fake_home
        self.job = make_job(config_dir=self.tmpdir)

    def tearDown(self):
        if self._real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._real_home
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self._fake_home, ignore_errors=True)

    def _break_path_settings(self):
        """Simule un PathSettings indisponible (LO dégradé, contexte mocké)."""
        self.job.sm.createInstanceWithContext.return_value.UserConfig = MagicMock()
        self.job._local_cfg = None

    # ── Le chemin nominal ────────────────────────────────────────────────────

    def test_history_lives_next_to_config(self):
        self.assertEqual(
            self.job._prompts_calc_path(),
            os.path.join(self.tmpdir, "mirai", "prompts_calc.txt"),
        )

    def test_path_does_not_raise_on_missing_attribute(self):
        # Régression directe : l'attribut _profile_config_path n'existe pas.
        self.assertFalse(hasattr(self.job, "_profile_config_path"))
        self.job._prompts_calc_path()  # ne doit pas lever

    # ── Le repli interdit ────────────────────────────────────────────────────

    def test_never_falls_back_to_home(self):
        self._break_path_settings()
        path = self.job._prompts_calc_path()
        self.assertEqual(path, "")
        home = os.path.expanduser("~")
        self.assertNotEqual(path, os.path.join(home, "prompts_calc.txt"))

    def test_save_writes_nothing_when_config_dir_unknown(self):
        self._break_path_settings()
        home_file = os.path.join(os.path.expanduser("~"), "prompts_calc.txt")
        before = os.path.exists(home_file)
        mtime_before = os.path.getmtime(home_file) if before else None

        self.job._save_prompt_calc("somme des ventes par région")

        # Ni création, ni modification d'un éventuel fichier préexistant.
        self.assertEqual(os.path.exists(home_file), before)
        if before:
            self.assertEqual(os.path.getmtime(home_file), mtime_before)

    def test_load_returns_empty_when_config_dir_unknown(self):
        self._break_path_settings()
        self.assertEqual(self.job._load_prompts_calc(), [])

    # ── Persistance ──────────────────────────────────────────────────────────

    def test_save_then_load_roundtrip(self):
        self.job._save_prompt_calc("moyenne de la colonne B")
        self.assertEqual(self.job._load_prompts_calc(), ["moyenne de la colonne B"])

    def test_most_recent_first(self):
        self.job._save_prompt_calc("premier")
        self.job._save_prompt_calc("second")
        self.assertEqual(self.job._load_prompts_calc(), ["second", "premier"])

    def test_duplicates_are_deduplicated(self):
        self.job._save_prompt_calc("même demande")
        self.job._save_prompt_calc("autre")
        self.job._save_prompt_calc("même demande")
        self.assertEqual(self.job._load_prompts_calc(), ["même demande", "autre"])

    def test_history_is_capped_at_100(self):
        for i in range(120):
            self.job._save_prompt_calc(f"demande {i}")
        history = self.job._load_prompts_calc()
        self.assertEqual(len(history), 100)
        self.assertEqual(history[0], "demande 119")


if __name__ == "__main__":
    unittest.main()
