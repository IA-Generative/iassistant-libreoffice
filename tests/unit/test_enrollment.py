"""
Tests for first-time enrollment logic: _needs_first_enrollment,
_enrollment_dismissed flag, trigger() enrollment interception,
and _schedule_enrollment_check deferred auto-launch.

No LibreOffice required — UNO modules are stubbed.
"""
import base64
import json
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from tests.stubs.uno_stubs import install, make_job, seed_user_config

install()

from src.mirai.entrypoint import MainJob


def _make_jwt(payload: dict) -> str:
    """Craft a minimal JWT (unsigned) for testing."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def _write_config(config_dir, data):
    return seed_user_config(config_dir, data)


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


class TestNeedsFirstEnrollment(unittest.TestCase):
    """Test _needs_first_enrollment() logic."""

    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.job = make_job(config_dir=self.config_dir)
        # DM activé : sinon _needs_first_enrollment court-circuite à False
        # (pas d'enrôlement quand le device management est désactivé).
        self.job._device_management_enabled = MagicMock(return_value=True)

    def test_no_config_file_returns_true(self):
        """No config.json at all → needs enrollment."""
        # config_dir is empty, no config.json
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_false_no_token_returns_true(self):
        """enrolled=false, no access_token → needs enrollment."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": ""})
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_true_returns_false(self):
        """enrolled=true + creds relay → does NOT need enrollment."""
        _write_config(self.config_dir, _enrolled())
        self.assertFalse(self.job._needs_first_enrollment())

    def test_enrolled_string_true_returns_false(self):
        """enrolled='true' (string) + creds relay → does NOT need enrollment."""
        _write_config(self.config_dir, _enrolled(enrolled="true"))
        self.assertFalse(self.job._needs_first_enrollment())

    def test_enrolled_without_relay_creds_and_no_login_returns_true(self):
        """Régression : enrolled=true SANS cred relay ni session valide.

        C'est l'état absorbant observé en production — le poste ne peut plus ni
        obtenir de llmToken ni se ré-enrôler seul (le ré-enrôlement de fond a
        besoin d'une session pour dériver l'email). Le wizard doit reprendre la
        main au lieu de laisser passer.
        """
        _write_config(self.config_dir, {"enrolled": True, "access_token": ""})
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_without_relay_creds_but_valid_login_returns_false(self):
        """Même état, mais session valide → le ré-enrôlement de fond suffit,
        on n'impose pas le wizard à l'utilisateur."""
        token = _make_jwt({"exp": int(time.time()) + 3600})
        _write_config(self.config_dir, {"enrolled": True, "access_token": token})
        self.assertFalse(self.job._needs_first_enrollment())

    def test_enrolled_with_expired_relay_creds_and_no_login_returns_true(self):
        """Creds relay présents mais périmés → équivalent à pas de creds."""
        _write_config(self.config_dir, _enrolled(
            relay_key_expires_at=int(time.time()) - 10, access_token=""))
        self.assertTrue(self.job._needs_first_enrollment())

    def test_not_enrolled_but_valid_token_returns_false(self):
        """enrolled=false but valid access_token → does NOT need enrollment."""
        token = _make_jwt({"exp": int(time.time()) + 3600})
        _write_config(self.config_dir, {"enrolled": False, "access_token": token})
        self.assertFalse(self.job._needs_first_enrollment())

    def test_not_enrolled_expired_token_returns_true(self):
        """enrolled=false and expired access_token → needs enrollment."""
        token = _make_jwt({"exp": int(time.time()) - 100})
        _write_config(self.config_dir, {"enrolled": False, "access_token": token})
        self.assertTrue(self.job._needs_first_enrollment())

    def test_enrolled_false_empty_token_returns_true(self):
        """enrolled=false, access_token=' ' → needs enrollment."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": "   "})
        self.assertTrue(self.job._needs_first_enrollment())


class TestEnrollmentDismissedFlag(unittest.TestCase):
    """Test _enrollment_dismissed flag behaviour."""

    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.job = make_job(config_dir=self.config_dir)
        # DM activé : sinon _needs_first_enrollment court-circuite à False
        # (pas d'enrôlement quand le device management est désactivé).
        self.job._device_management_enabled = MagicMock(return_value=True)

    def test_initial_dismissed_is_false(self):
        """Flag should start as False."""
        self.assertFalse(MainJob._enrollment_dismissed_cls)

    def test_trigger_sets_dismissed_on_enrollment_cancel(self):
        """When enrollment is needed and _run_first_enrollment returns False,
        _enrollment_dismissed should become True."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": ""})
        self.assertTrue(self.job._needs_first_enrollment())

        # Mock _run_first_enrollment to simulate cancel
        with patch.object(self.job, '_run_first_enrollment', return_value=False):
            with patch.object(self.job, '_schedule_config_refresh'):
                self.job.trigger("test_action")

        self.assertTrue(MainJob._enrollment_dismissed_cls)

    def test_trigger_skips_enrollment_when_dismissed(self):
        """Once dismissed, trigger should NOT call _run_first_enrollment again."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": ""})
        MainJob._enrollment_dismissed_cls = True

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            with patch.object(self.job, '_schedule_config_refresh'):
                # trigger will try to get desktop component, mock that too
                self.job.trigger("test_action")

        mock_enroll.assert_not_called()

    def test_trigger_proceeds_when_enrolled(self):
        """When already enrolled, trigger should NOT call _run_first_enrollment."""
        _write_config(self.config_dir, _enrolled())

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            with patch.object(self.job, '_schedule_config_refresh'):
                self.job.trigger("test_action")

        mock_enroll.assert_not_called()


class TestScheduleEnrollmentCheck(unittest.TestCase):
    """Test the deferred auto-launch mechanism."""

    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.job = make_job(config_dir=self.config_dir)
        # DM activé : sinon _needs_first_enrollment court-circuite à False
        # (pas d'enrôlement quand le device management est désactivé).
        self.job._device_management_enabled = MagicMock(return_value=True)

    def test_timer_fires_and_calls_enrollment(self):
        """_schedule_enrollment_check should eventually call _run_first_enrollment
        when enrollment is needed."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": ""})

        with patch.object(self.job, '_run_first_enrollment', return_value=True) as mock_enroll:
            # Use a very short timer for testing
            import threading
            original_timer = threading.Timer

            captured_fn = {}

            def fast_timer(delay, fn):
                captured_fn['fn'] = fn
                t = original_timer(0.01, fn)  # Near-instant
                t.daemon = True
                return t

            with patch('threading.Timer', side_effect=fast_timer):
                self.job._schedule_enrollment_check()

            # Wait for the timer to fire
            time.sleep(0.1)
            mock_enroll.assert_called_once()

    def test_timer_skips_when_already_enrolled(self):
        """Timer should NOT call _run_first_enrollment when already enrolled."""
        _write_config(self.config_dir, _enrolled())

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            import threading
            original_timer = threading.Timer

            def fast_timer(delay, fn):
                t = original_timer(0.01, fn)
                t.daemon = True
                return t

            with patch('threading.Timer', side_effect=fast_timer):
                self.job._schedule_enrollment_check()

            time.sleep(0.1)
            mock_enroll.assert_not_called()

    def test_timer_skips_when_dismissed(self):
        """Timer should NOT call _run_first_enrollment when dismissed."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": ""})
        MainJob._enrollment_dismissed_cls = True

        with patch.object(self.job, '_run_first_enrollment') as mock_enroll:
            import threading
            original_timer = threading.Timer

            def fast_timer(delay, fn):
                t = original_timer(0.01, fn)
                t.daemon = True
                return t

            with patch('threading.Timer', side_effect=fast_timer):
                self.job._schedule_enrollment_check()

            time.sleep(0.1)
            mock_enroll.assert_not_called()

    def test_timer_sets_dismissed_on_cancel(self):
        """When user cancels in auto-launched wizard, dismissed flag should be set."""
        _write_config(self.config_dir, {"enrolled": False, "access_token": ""})

        with patch.object(self.job, '_run_first_enrollment', return_value=False):
            import threading
            original_timer = threading.Timer

            def fast_timer(delay, fn):
                t = original_timer(0.01, fn)
                t.daemon = True
                return t

            with patch('threading.Timer', side_effect=fast_timer):
                self.job._schedule_enrollment_check()

            time.sleep(0.1)

        self.assertTrue(MainJob._enrollment_dismissed_cls)


class TestRunFirstEnrollment(unittest.TestCase):
    """Test _run_first_enrollment orchestration."""

    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.job = make_job(config_dir=self.config_dir)
        # DM activé : sinon _needs_first_enrollment court-circuite à False
        # (pas d'enrôlement quand le device management est désactivé).
        self.job._device_management_enabled = MagicMock(return_value=True)

    def test_returns_false_when_config_fetch_fails(self):
        """If config fetch returns None, enrollment should fail."""
        with patch.object(self.job, '_schedule_config_refresh'):
            with patch.object(self.job, '_fetch_config', return_value=None):
                result = self.job._run_first_enrollment()
        self.assertFalse(result)

    def test_returns_true_when_auth_succeeds(self):
        """Full flow: config fetch → auth → success."""
        fake_config = {"config": {"keycloakIssuerUrl": "http://test"}}
        fake_token = _make_jwt({"exp": int(time.time()) + 3600})

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
