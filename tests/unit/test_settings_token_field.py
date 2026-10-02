"""Le champ « Token OWUI » n'expose plus le llmToken minté par le DM."""

from unittest.mock import MagicMock

from tests.stubs.uno_stubs import install, make_job

install()


def _job(tmp_path, dm_enabled, token):
    job = make_job(config_dir=str(tmp_path))
    job._device_management_enabled = lambda: dm_enabled
    job.get_config = MagicMock(
        side_effect=lambda key, default=None: token if key == "llm_api_tokens" else default)
    return job


def test_dm_mode_keeps_field_empty_but_requests_use_token(tmp_path):
    job = _job(tmp_path, True, "dm-token-123456")
    assert job._settings_token_values() == ("", "dm-token-123456")


def test_offline_mode_shows_the_user_key(tmp_path):
    job = _job(tmp_path, False, "user-key-123456")
    assert job._settings_token_values() == ("user-key-123456", "user-key-123456")
