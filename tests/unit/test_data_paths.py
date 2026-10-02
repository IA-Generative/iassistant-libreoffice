"""Tous les fichiers de l'extension sont rangés dans <UserConfig>/mirai/."""

import os

from tests.stubs.uno_stubs import install, make_job

install()


def test_paths_live_in_the_data_dir(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    data = os.path.join(str(tmp_path), "mirai")
    assert job._data_dir() == data
    assert job._pending_update_dir() == os.path.join(data, "pending_update")
    assert job._prompt_log_path() == os.path.join(data, "prompt.txt")
    assert job._prompts_calc_path() == os.path.join(data, "prompts_calc.txt")
    assert job._update_state_path() == os.path.join(data, "pending_update", "update_state.json")


def test_no_user_config_dir_means_no_paths(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job._local_cfg = None
    job._get_user_config_dir = lambda: ""
    assert job._data_dir() == ""
    assert job._pending_update_dir() == ""
    assert job._prompts_calc_path() == ""
