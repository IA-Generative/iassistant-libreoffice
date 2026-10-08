"""Chaîne d'authentification du proxy LLM du DM (/llm/v1).

Couvre la panne observée en production : un poste marqué `enrolled=True` mais
DÉPOURVU de credentials relay. Le DM ne mintait alors aucun llmToken, le plugin
n'envoyait aucun credential exploitable, et 100 % des appels /llm/v1 tombaient
en 401 sans aucun chemin de sortie automatique.

Aucun LibreOffice requis — les modules UNO sont bouchonnés.
"""
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from src.mirai import credentials
from tests.stubs.uno_stubs import install, make_job, make_jwt, read_user_config, seed_user_config

install()


BOOTSTRAP = "https://dm.example.test"
PROXY_URL = BOOTSTRAP + "/llm/v1"


def _dm_response(**inner_overrides):
    """Réponse /config du DM en mode proxy LLM.

    `llmToken` est présent (même vide) dès que FORCE_LLM_ENDPOINT_OVERRIDE est
    actif : c'est le marqueur du mode proxy côté plugin.
    """
    inner = {
        "enabled": True,
        "llm_base_urls": PROXY_URL,
        "llmEndpoint": PROXY_URL,
        "llm_api_tokens": "",
        "llmToken": "",
    }
    inner.update(inner_overrides)
    return {"meta": {"schema_version": 2}, "config": inner}


class _JobCase(unittest.TestCase):
    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.config_dir, ignore_errors=True)
        self.job = make_job(config_dir=self.config_dir)
        self.job._device_management_enabled = MagicMock(return_value=True)
        self.job._active_bootstrap_url = MagicMock(return_value=BOOTSTRAP)


class TestRelayCredentialsValid(_JobCase):
    """_relay_credentials_valid : la vraie source de vérité de l'enrôlement."""

    def test_missing_creds(self):
        seed_user_config(self.config_dir, {"enrolled": True})
        self.assertFalse(self.job._relay_credentials_valid())

    def test_present_creds_without_expiry(self):
        seed_user_config(self.config_dir, {
            "relay_client_id": "id", "relay_client_key": "key"})
        self.assertTrue(self.job._relay_credentials_valid())

    def test_expired_creds(self):
        seed_user_config(self.config_dir, {
            "relay_client_id": "id", "relay_client_key": "key",
            "relay_key_expires_at": int(time.time()) - 10})
        self.assertFalse(self.job._relay_credentials_valid())

    def test_future_expiry_is_valid(self):
        seed_user_config(self.config_dir, {
            "relay_client_id": "id", "relay_client_key": "key",
            "relay_key_expires_at": int(time.time()) + 7200})
        self.assertTrue(self.job._relay_credentials_valid())


class TestEnsureDeviceManagementStateGuard(_JobCase):
    """D1/D2 : le court-circuit porte sur les creds relay, pas sur `enrolled`."""

    def _enroll_calls(self, config_file, force_enroll=False):
        """Nombre de requêtes émises vers l'endpoint /enroll."""
        seed_user_config(self.config_dir, config_file)
        self.job._fetch_config = MagicMock(return_value=_dm_response())
        self.job._ensure_access_token = MagicMock(return_value="tok")
        self.job._token_email = MagicMock(return_value="a@b.c")
        self.job._keycloak_config = MagicMock(return_value={})
        self.job._keycloak_endpoint = MagicMock(return_value="")
        self.job._ensure_extension_uuid = MagicMock(return_value="uuid")
        with patch("urllib.request.Request") as request_cls:
            self.job._urlopen = MagicMock()
            self.job._urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(read=MagicMock(return_value=b"{}")))
            self.job._urlopen.return_value.__exit__ = MagicMock(return_value=False)
            self.job._ensure_device_management_state(force_enroll=force_enroll)
            return sum(
                1 for call in request_cls.call_args_list
                if call.args and str(call.args[0]).endswith("/enroll")
            )

    def test_enrolled_without_relay_creds_still_enrolls(self):
        """`enrolled=True` sans cred relay ne court-circuite pas l'enrôlement.

        Sans paire relais, aucun en-tête X-Relay-* n'est envoyé et le DM ne mint
        aucun llmToken.
        """
        self.assertEqual(self._enroll_calls({"enrolled": True}), 1)

    def test_valid_relay_creds_short_circuit(self):
        """Creds relay valides → aucun POST /enroll inutile."""
        self.assertEqual(self._enroll_calls({
            "enrolled": True, "relay_client_id": "id",
            "relay_client_key": "key"}), 0)

    def test_force_enroll_overrides_short_circuit(self):
        """La récupération d'auth doit pouvoir ré-enrôler malgré des creds
        présents (cas des creds révoqués côté serveur)."""
        self.assertEqual(self._enroll_calls({
            "enrolled": True, "relay_client_id": "id",
            "relay_client_key": "key"}, force_enroll=True), 1)

    def test_expired_relay_creds_re_enroll(self):
        """Creds relay périmés → ré-enrôlement (D8 : relay_key_expires_at relu)."""
        self.assertEqual(self._enroll_calls({
            "enrolled": True, "relay_client_id": "id", "relay_client_key": "key",
            "relay_key_expires_at": int(time.time()) - 10}), 1)


class TestAbsorbingStateHasAnExit(_JobCase):
    """L'état absorbant doit TOUJOURS avoir une porte de sortie.

    Rappel du piège : `enrolled=True` n'atteste que d'un HTTP 201 passé, pas
    d'une paire relais active. Un poste dans cet état reçoit 401 « Missing
    credentials » sur 100 % de ses appels — et si le drapeau court-circuite le
    ré-enrôlement, plus rien ne peut l'en sortir.

    Deux issues existent, selon qu'une session Keycloak est encore valable :
    ré-enrôlement silencieux en tâche de fond, ou assistant d'enrôlement.
    Ces tests vérifient qu'au moins l'une des deux s'ouvre toujours.
    """

    def _needs_wizard(self, config):
        seed_user_config(self.config_dir, config)
        return self.job._needs_first_enrollment()

    def test_absorbing_state_without_session_opens_the_wizard(self):
        """Ni paire relais ni session : seul l'assistant peut sauver le poste."""
        self.assertTrue(self._needs_wizard({"enrolled": True}))

    def test_absorbing_state_with_expired_session_opens_the_wizard(self):
        """Un jeton périmé ne permet pas le ré-enrôlement silencieux."""
        expired = make_jwt({"email": "a@b.c", "exp": int(time.time()) - 3600})
        self.assertTrue(self._needs_wizard({
            "enrolled": True, "access_token": expired}))

    def test_absorbing_state_with_live_session_recovers_silently(self):
        """Session valide : le ré-enrôlement de fond suffit, pas d'assistant.

        Il dérive l'e-mail du jeton — inutile d'importuner l'utilisateur.
        """
        fresh = make_jwt({"email": "a@b.c", "exp": int(time.time()) + 3600})
        self.assertFalse(self._needs_wizard({
            "enrolled": True, "access_token": fresh}))

    def test_healthy_device_is_left_alone(self):
        self.assertFalse(self._needs_wizard({
            "enrolled": True, "relay_client_id": "id",
            "relay_client_key": "key"}))

    def test_fresh_device_needs_enrollment(self):
        self.assertTrue(self._needs_wizard({}))

class TestLlmTokenExpiry(_JobCase):
    """D5 : le llmToken est court (TTL DM 3600 s) — son expiration fait foi."""

    def test_expired_persisted_token_is_ignored(self):
        seed_user_config(self.config_dir, {"llm_base_urls": PROXY_URL})
        credentials.remember(
            credentials.DM_LLM_TOKEN, "payload.signature", int(time.time()) - 10)
        self.job._schedule_config_refresh = MagicMock()
        self.assertEqual(self.job.get_config("llm_api_tokens", ""), "")
        reasons = [c.kwargs.get("reason")
                   for c in self.job._schedule_config_refresh.call_args_list]
        self.assertIn("llm_token_expired", reasons)

    def test_valid_persisted_token_is_served(self):
        seed_user_config(self.config_dir, {"llm_base_urls": PROXY_URL})
        credentials.remember(
            credentials.DM_LLM_TOKEN, "payload.signature", int(time.time()) + 3600)
        self.assertEqual(
            self.job.get_config("llm_api_tokens", ""), "payload.signature")

    def test_token_without_expiry_is_served(self):
        """Expiration inconnue → on ne périme rien : le serveur reste l'autorité."""
        seed_user_config(self.config_dir, {
            "llm_base_urls": PROXY_URL, "llm_api_tokens": "payload.signature"})
        self.assertEqual(
            self.job.get_config("llm_api_tokens", ""), "payload.signature")


class TestPersistClearsRevokedToken(_JobCase):
    """D6 : un `llm_api_tokens:""` renvoyé par le DM doit EFFACER le token local."""

    def test_empty_token_from_dm_clears_the_remembered_token(self):
        credentials.remember(credentials.DM_LLM_TOKEN, "stale.token", int(time.time()) + 3600)
        self.job._persist_bootstrap_config(_dm_response())
        self.assertEqual(credentials.recall(credentials.DM_LLM_TOKEN), "")

    def test_minted_token_is_remembered_in_memory_only(self):
        expires_at = int(time.time()) + 3600
        self.job._persist_bootstrap_config(_dm_response(
            llm_api_tokens="fresh.token", llmToken="fresh.token",
            llmTokenExpiresAt=expires_at))
        self.assertEqual(credentials.recall(credentials.DM_LLM_TOKEN), "fresh.token")
        self.assertEqual(credentials.expires_at(credentials.DM_LLM_TOKEN), expires_at)
        self.assertNotIn("llm_api_tokens", read_user_config(self.config_dir))
        self.assertIsNone(credentials.store().get("llm_api_tokens"))


class TestNoKeycloakFallbackInProxyMode(_JobCase):
    """D4 : le proxy DM n'accepte QUE son llmToken HMAC.

    Un access_token Keycloak est un JWT à 3 segments : `verify_llm_token` le
    rejette. Ce repli ne peut structurellement pas marcher et masquait la vraie
    cause (enrôlement incomplet) derrière un 401 « jeton invalide ».
    """

    def test_proxy_mode_refuses_keycloak_fallback(self):
        seed_user_config(self.config_dir, {"llm_base_urls": PROXY_URL})
        self.job._get_openwebui_access_token = MagicMock(return_value="jwt.head.sig")
        self.assertEqual(self.job._effective_api_token(""), "")
        self.job._get_openwebui_access_token.assert_not_called()

    def test_direct_mode_keeps_fallback(self):
        """Hors proxy DM (LLM direct/local), le repli reste valable."""
        seed_user_config(self.config_dir, {"llm_base_urls": "http://localhost:11434/v1"})
        self.job._get_openwebui_access_token = MagicMock(return_value="api-key")
        self.assertEqual(self.job._effective_api_token(""), "api-key")

    def test_explicit_token_always_wins(self):
        seed_user_config(self.config_dir, {"llm_base_urls": PROXY_URL})
        self.assertEqual(self.job._effective_api_token("given"), "given")


class TestAuthNoticeTriggersRecovery(_JobCase):
    """D3 : le DM annonce lui-même l'auth manquante — il faut y réagir."""

    def test_auth_notice_schedules_relay_recovery(self):
        seed_user_config(self.config_dir, {"enrolled": True})
        self.job._schedule_relay_recovery = MagicMock(return_value=True)
        self.job._check_relay_auth_notice(_dm_response(
            _auth_notice="Authentification requise. Effectuez un enrollment."))
        self.job._schedule_relay_recovery.assert_called_once()

    def test_empty_llm_token_alone_schedules_recovery(self):
        """Même sans `_auth_notice` : mode proxy + llmToken vide = impasse."""
        seed_user_config(self.config_dir, {"enrolled": True})
        self.job._schedule_relay_recovery = MagicMock(return_value=True)
        self.job._check_relay_auth_notice(_dm_response())
        self.job._schedule_relay_recovery.assert_called_once()

    def test_minted_token_does_not_schedule_recovery(self):
        seed_user_config(self.config_dir, {"enrolled": True})
        self.job._schedule_relay_recovery = MagicMock(return_value=True)
        self.job._check_relay_auth_notice(_dm_response(
            llmToken="fresh.token", llm_api_tokens="fresh.token"))
        self.job._schedule_relay_recovery.assert_not_called()


class TestRecoverLlmAuth(_JobCase):
    """D7 : reprise après 401 — refresh /config, puis ré-enrôlement si besoin."""

    def test_config_refresh_alone_is_enough(self):
        seed_user_config(self.config_dir, {})
        self.job._fetch_config = MagicMock()
        self.job.get_config = MagicMock(return_value="fresh.token")
        self.job._ensure_device_management_state = MagicMock()
        self.assertTrue(self.job._recover_llm_auth())
        self.job._fetch_config.assert_called_once_with(force=True)
        self.job._ensure_device_management_state.assert_not_called()

    def test_falls_back_to_re_enrollment(self):
        seed_user_config(self.config_dir, {"enrolled": True})
        self.job._fetch_config = MagicMock()
        self.job.get_config = MagicMock(return_value="")
        self.job._ensure_device_management_state = MagicMock()
        self.assertFalse(self.job._recover_llm_auth())
        self.job._ensure_device_management_state.assert_called_once_with(
            force_enroll=True)

    def test_backoff_prevents_hammering(self):
        seed_user_config(self.config_dir, {})
        self.job._fetch_config = MagicMock()
        self.job.get_config = MagicMock(return_value="fresh.token")
        self.job._recover_llm_auth()
        self.job._recover_llm_auth()
        self.job._fetch_config.assert_called_once()


if __name__ == "__main__":
    unittest.main()
