"""
Tests for first-time enrollment logic: _needs_first_enrollment,
_enrollment_dismissed flag, trigger() enrollment interception,
and _schedule_enrollment_check deferred auto-launch.

No LibreOffice required — UNO modules are stubbed.
"""
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from tests.stubs.uno_stubs import install, make_job, make_jwt, seed_user_config

install()

from src.mirai.entrypoint import MainJob


def _enrolled(**extra):
    """Config d'un poste RÉELLEMENT enrôlé : drapeau + credentials relay.

    `enrolled` seul ne suffit pas — c'est justement l'état « enrôlé à moitié »
    (drapeau posé, aucun cred relay) qui bloquait le poste : le DM ne mintait
    alors aucun llmToken et tous les appels /llm/v1 tombaient en 401.
    """
    data = {
        "enrolled": True,
        "relay_client_id": "relay-client-abc",
        "relay_client_key": "relay-key-xyz",
    }
    data.update(extra)
    return data


_REAL_TIMER = threading.Timer


def _fast_timer(delay, fn):
    t = _REAL_TIMER(0.01, fn)
    t.daemon = True
    return t


class _EnrollmentCase(unittest.TestCase):
    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.config_dir, ignore_errors=True)
        self.job = make_job(config_dir=self.config_dir)
        # DM activé : sinon _needs_first_enrollment court-circuite à False
        # (pas d'enrôlement quand le device management est désactivé).
        self.job._device_management_enabled = MagicMock(return_value=True)


class TestNeedsFirstEnrollment(_EnrollmentCase):
    """Test _needs_first_enrollment() logic."""

    def test_no_config_file_returns_true(self):
        """No config.json at all → needs enrollment."""
        # config_dir is empty, no config.json
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_false_no_token_returns_true(self):
        """enrolled=false, no access_token → needs enrollment."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": ""})
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_true_returns_false(self):
        """enrolled=true + creds relay → does NOT need enrollment."""
        seed_user_config(self.config_dir, _enrolled())
        self.assertFalse(self.job._needs_first_enrollment())

    def test_enrolled_string_true_returns_false(self):
        """enrolled='true' (string) + creds relay → does NOT need enrollment."""
        seed_user_config(self.config_dir, _enrolled(enrolled="true"))
        self.assertFalse(self.job._needs_first_enrollment())

    def test_enrolled_without_relay_creds_and_no_login_returns_true(self):
        """enrolled=true SANS cred relay ni session valide : le wizard reprend la main.

        Sans paire relais le poste n'obtient pas de llmToken, et sans session il ne
        peut pas se ré-enrôler seul (le ré-enrôlement de fond a besoin d'une
        session pour dériver l'email).
        """
        seed_user_config(self.config_dir, {"enrolled": True, "access_token": ""})
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_without_relay_creds_but_valid_login_returns_false(self):
        """Même état, mais session valide → le ré-enrôlement de fond suffit,
        on n'impose pas le wizard à l'utilisateur."""
        token = make_jwt({"exp": int(time.time()) + 3600})
        seed_user_config(self.config_dir, {"enrolled": True, "access_token": token})
        self.assertFalse(self.job._needs_first_enrollment())

    def test_enrolled_with_expired_relay_creds_and_no_login_returns_true(self):
        """Creds relay présents mais périmés → équivalent à pas de creds."""
        seed_user_config(self.config_dir, _enrolled(
            relay_key_expires_at=int(time.time()) - 10, access_token=""))
        self.assertTrue(self.job._needs_first_enrollment())

    def test_not_enrolled_but_valid_token_returns_false(self):
        """enrolled=false but valid access_token → does NOT need enrollment."""
        token = make_jwt({"exp": int(time.time()) + 3600})
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": token})
        self.assertFalse(self.job._needs_first_enrollment())

    def test_not_enrolled_expired_token_returns_true(self):
        """enrolled=false and expired access_token → needs enrollment."""
        token = make_jwt({"exp": int(time.time()) - 100})
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": token})
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_false_empty_token_returns_true(self):
        """enrolled=false, access_token=' ' → needs enrollment."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": "   "})
        self.assertTrue(self.job._needs_first_enrollment())


class TestEnrollmentDismissedFlag(_EnrollmentCase):
    """Test _enrollment_dismissed flag behaviour."""

    def test_initial_dismissed_is_false(self):
        """Flag should start as False."""
        self.assertFalse(MainJob._enrollment_dismissed_cls)

    def test_trigger_sets_dismissed_on_enrollment_cancel(self):
        """When enrollment is needed and _run_first_enrollment returns False,
        _enrollment_dismissed should become True."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": ""})
        self.assertTrue(self.job._needs_first_enrollment())

        # Mock _run_first_enrollment to simulate cancel
        with patch.object(self.job, '_run_first_enrollment', return_value=False):
            with patch.object(self.job, '_schedule_config_refresh'):
                self.job.trigger("test_action")

        self.assertTrue(MainJob._enrollment_dismissed_cls)

    def test_trigger_skips_enrollment_when_dismissed(self):
        """Once dismissed, trigger should NOT call _run_first_enrollment again."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": ""})
        MainJob._enrollment_dismissed_cls = True

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            with patch.object(self.job, '_schedule_config_refresh'):
                self.job.trigger("test_action")

        mock_enroll.assert_not_called()

    def test_trigger_proceeds_when_enrolled(self):
        """When already enrolled, trigger should NOT call _run_first_enrollment."""
        seed_user_config(self.config_dir, _enrolled())

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            with patch.object(self.job, '_schedule_config_refresh'):
                self.job.trigger("test_action")

        mock_enroll.assert_not_called()


class TestScheduleEnrollmentCheck(_EnrollmentCase):
    """Test the deferred auto-launch mechanism."""

    def test_timer_fires_and_calls_enrollment(self):
        """_schedule_enrollment_check should eventually call _run_first_enrollment
        when enrollment is needed."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": ""})

        with patch.object(self.job, '_run_first_enrollment', return_value=True) as mock_enroll:
            # Use a very short timer for testing
            with patch('threading.Timer', side_effect=_fast_timer):
                self.job._schedule_enrollment_check()

            # Wait for the timer to fire
            time.sleep(0.1)
            mock_enroll.assert_called_once()

    def test_timer_skips_when_already_enrolled(self):
        """Timer should NOT call _run_first_enrollment when already enrolled."""
        seed_user_config(self.config_dir, _enrolled())

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            with patch('threading.Timer', side_effect=_fast_timer):
                self.job._schedule_enrollment_check()

            time.sleep(0.1)
            mock_enroll.assert_not_called()

    def test_timer_skips_when_dismissed(self):
        """Timer should NOT call _run_first_enrollment when dismissed."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": ""})
        MainJob._enrollment_dismissed_cls = True

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            with patch('threading.Timer', side_effect=_fast_timer):
                self.job._schedule_enrollment_check()

            time.sleep(0.1)
            mock_enroll.assert_not_called()

    def test_timer_sets_dismissed_on_cancel(self):
        """When user cancels in auto-launched wizard, dismissed flag should be set."""
        seed_user_config(self.config_dir, {"enrolled": False, "access_token": ""})

        with patch.object(self.job, '_run_first_enrollment', return_value=False):
            with patch('threading.Timer', side_effect=_fast_timer):
                self.job._schedule_enrollment_check()

            time.sleep(0.1)

        self.assertTrue(MainJob._enrollment_dismissed_cls)


class TestRunFirstEnrollment(_EnrollmentCase):
    """Test _run_first_enrollment orchestration."""

    def test_returns_false_when_config_fetch_fails(self):
        """If config fetch returns None, enrollment should fail."""
        with patch.object(self.job, '_schedule_config_refresh'):
            with patch.object(self.job, '_fetch_config', return_value=None):
                result = self.job._run_first_enrollment()
        self.assertFalse(result)

    def test_returns_true_when_auth_succeeds(self):
        """Full flow: config fetch → auth → success."""
        fake_config = {"config": {"keycloakIssuerUrl": "http://test"}}
        fake_token = make_jwt({"exp": int(time.time()) + 3600})

        with patch.object(self.job, '_schedule_config_refresh'):
            with patch.object(self.job, '_fetch_config', return_value=fake_config):
                with patch.object(self.job, '_ensure_access_token', return_value=fake_token):
                    result = self.job._run_first_enrollment()
        self.assertTrue(result)

    def test_returns_false_when_auth_fails(self):
        """Config fetch OK but auth returns None → enrollment fails."""
        fake_config = {"config": {"keycloakIssuerUrl": "http://test"}}

        with patch.object(self.job, '_schedule_config_refresh'):
            with patch.object(self.job, '_fetch_config', return_value=fake_config):
                with patch.object(self.job, '_ensure_access_token', return_value=None):
                    result = self.job._run_first_enrollment()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
