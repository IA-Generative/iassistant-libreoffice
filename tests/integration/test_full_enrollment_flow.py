"""
Integration test: full new-user enrollment flow (fresh install, no local state).

Sequence validated (per device-management README — Secure Relay Flow):

  1.  Plugin → DM:  GET /config/libreoffice/config.json   (no relay headers)
  2.  DM → Plugin:  public config (keycloak endpoints, secrets scrubbed)
  3.  Plugin → KC:  PKCE authorization code flow (browser redirect + callback)
  4.  KC → Plugin:  access_token + refresh_token
  5.  Plugin → DM:  POST /enroll                          (Authorization: Bearer access_token)
  6.  DM → Plugin:  relayClientId + relayClientKey + expiry
  7.  Plugin → DM:  GET /config/libreoffice/config.json   (X-Relay-Client + X-Relay-Key)
  8.  DM → Plugin:  config with secret values (llm_api_tokens, llm_base_urls)
  9.  Plugin → LLM: POST /v1/chat/completions             (Authorization: Bearer llm-secret-token)
  10. LLM → Plugin: streaming completion response

All external HTTP calls are intercepted via MockHttpRouter.
The PKCE callback server IS real (localhost socket started by _wait_for_auth_code).
webbrowser.open is patched to auto-send the auth code to that server.
"""
import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

from tests.integration.mock_http import MockHttpRouter
from tests.stubs.uno_stubs import install, make_job, read_user_config, seed_user_config

install()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jwt(payload: dict) -> str:
    """Build a minimal unsigned JWT for testing."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.fakesig"


def _pkce_auto_callback(auth_url: str, delay: float = 0.2):
    """
    Extract redirect_uri + state from auth_url, then send the auth callback
    to the plugin's local HTTP server in a background thread.
    """
    parsed = urllib.parse.urlparse(auth_url)
    params = urllib.parse.parse_qs(parsed.query)
    redirect_uri = params.get("redirect_uri", ["http://localhost:19876/callback"])[0]
    state = params.get("state", ["test-state"])[0]
    callback_url = f"{redirect_uri}?code=FAKE_AUTH_CODE&state={urllib.parse.quote(state)}"

    def _send():
        time.sleep(delay)
        try:
            urllib.request.urlopen(callback_url, timeout=5)
        except Exception:
            pass  # plugin returns 200 OK; urllib may still raise due to response body

    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOOTSTRAP_URL = "http://dm.test"
KC_BASE = "http://keycloak.test/realms/mirai"
LLM_BASE = "http://llm.test"

FAKE_ACCESS_TOKEN = _jwt({"sub": "user-1", "email": "user@test.local", "exp": 9_999_999_999})
FAKE_REFRESH_TOKEN = "refresh-token-abc"
RELAY_CLIENT_ID = "relay-client-001"
RELAY_CLIENT_KEY = "relay-key-secret"
LLM_SECRET_TOKEN = "llm-secret-token"

# DM public config (step 1): keycloak endpoints + no LLM secrets.
# Keys must match what _flat_keycloak() recognises (see entrypoint._keycloak_config).
DM_PUBLIC_CONFIG = {
    "configVersion": 1,
    "settings": {
        # Keycloak — recognised by _flat_keycloak via authorization_endpoint / token_endpoint
        "authorization_endpoint": f"{KC_BASE}/protocol/openid-connect/auth",
        "token_endpoint": f"{KC_BASE}/protocol/openid-connect/token",
        "userinfo_endpoint": f"{KC_BASE}/protocol/openid-connect/userinfo",
        "clientId": "libreoffice-plugin",
        "telemetryEnabled": False,
        # No LLM secrets on first fetch
        "llm_base_urls": "",
        "llm_api_tokens": "",
        "endpoints": {
            "enroll": f"{BOOTSTRAP_URL}/enroll",
        },
    },
}

# DM config returned after enrollment (relay headers present → secrets unlocked).
DM_SECRET_CONFIG = {
    "configVersion": 1,
    "settings": {
        **DM_PUBLIC_CONFIG["settings"],
        "llm_base_urls": LLM_BASE,
        "llm_api_tokens": LLM_SECRET_TOKEN,
        "llm_default_models": "mistral:7b",
        "api_type": "chat",
    },
}

# SSE streaming response for LLM (step 9).
LLM_STREAMING_RESPONSE = (
    b'data: {"choices":[{"delta":{"content":"Bonjour"},"finish_reason":null}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" monde"},"finish_reason":null}]}\n\n'
    b'data: [DONE]\n\n'
)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestFullEnrollmentFlow(unittest.TestCase):
    """
    End-to-end enrollment flow without real network or LibreOffice.

    The PKCE callback socket server is real (bound to localhost).
    All other HTTP is routed through MockHttpRouter.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._write_local_config()
        self.job = make_job(config_dir=self.tmpdir)

        # Wait for __init__ background threads (schedule_config_refresh) to settle,
        # then clear the recursion guard so our tests can call _fetch_config freely.
        time.sleep(0.1)
        self.job._fetching_config = False

        self.router = MockHttpRouter()
        self._register_routes()

    def _write_local_config(self):
        """
        Write the user settings with bootstrap URL and Keycloak client settings.

        _authorization_code_flow reads keycloakClientId, keycloak_redirect_uri,
        and keycloak_allowed_redirect_uri directly from the local config file
        (via _get_config_from_file), so they must live here — not in DM config.
        """
        config = {
            "configVersion": 1,
            "enabled": True,
            "bootstrap_url": BOOTSTRAP_URL,
            "config_path": "/config/libreoffice/config.json",
            # PKCE client settings (read by _authorization_code_flow from local file)
            "keycloakClientId": "libreoffice-plugin",
            # L'URL d'AUTORISATION (celle ouverte dans le navigateur) est
            # toujours dérivée de l'émetteur + du realm lus ICI, jamais de
            # l'`authorization_endpoint` servi par le DM : le navigateur doit
            # atteindre le vrai SSO, jamais le relais. Le token_endpoint, lui,
            # reste surchargeable par le DM (appel programmatique, relayable).
            "keycloakIssuerUrl": "http://keycloak.test",
            "keycloakRealm": "mirai",
            "keycloak_redirect_uri": "http://localhost:19876/callback",
            "keycloak_allowed_redirect_uri": ["http://localhost:19876/callback"],
        }
        seed_user_config(self.tmpdir, config)

    def _register_routes(self):
        # Step 1: public config (first call, before enrollment)
        self.router.add("GET", "/config/libreoffice/config.json",
                        body=DM_PUBLIC_CONFIG)
        # Step 4: Keycloak token exchange
        self.router.add("POST", "/protocol/openid-connect/token", body={
            "access_token": FAKE_ACCESS_TOKEN,
            "refresh_token": FAKE_REFRESH_TOKEN,
            "expires_in": 3600,
            "token_type": "Bearer",
        })
        # Step 5: enrollment
        self.router.add("POST", "/enroll", body={
            "relayClientId": RELAY_CLIENT_ID,
            "relayClientKey": RELAY_CLIENT_KEY,
            "relayKeyExpiresAt": 9_999_999_999,
        })
        # Step 7: config with secrets (after enrollment, relay headers present)
        # Same URL suffix — router matches first registered route; we rotate in tests
        # that need the secret config by re-registering or using a separate router.
        self.router.add("GET", "/config/libreoffice/config.json",
                        body=DM_SECRET_CONFIG)
        # Step 9: LLM streaming response
        self.router.add("POST", "/chat/completions",
                        body=LLM_STREAMING_RESPONSE, status=200)
        self.router._routes[-1]["raw"] = LLM_STREAMING_RESPONSE  # ensure raw bytes

    def _patch_urlopen(self):
        return patch.object(self.job, "_urlopen", side_effect=self.router)

    def _patch_confirm(self, result=True):
        return patch.object(self.job, "_confirm_message", return_value=result)

    def _patch_enrollment_wizard(self, proceed=True):
        """Neutralise l'assistant d'enrôlement graphique.

        Au PREMIER enrôlement, `_authorization_code_flow` passe par
        `_show_enrollment_wizard` au lieu d'un simple `_confirm_message`. Hors
        LibreOffice, le wizard échoue à construire son dialogue et retombe sur
        `(False, None, None, None, None)` — le flux sortait donc avant même
        d'ouvrir le navigateur, et ces tests étaient rouges depuis l'ajout du
        wizard sans que personne ne le voie. On rend ici la décision de
        l'utilisateur, sans la couche graphique.
        """
        return patch.object(
            self.job, "_show_enrollment_wizard",
            return_value=(proceed, None, None, None, None))

    def _patch_show_message(self):
        return patch.object(self.job, "_show_message", return_value=None)

    # ------------------------------------------------------------------
    # Step 1 + 2: Bootstrap config fetch — no relay headers
    # ------------------------------------------------------------------

    def test_01_bootstrap_config_fetch_no_relay_headers(self):
        """
        Steps 1-2: Plugin fetches public config from DM.
        First call must NOT carry relay headers.
        """
        with self._patch_urlopen():
            config = self.job._fetch_config(force=True)

        self.assertIsNotNone(config, "Bootstrap config should be returned")

        calls = self.router.calls_for("GET", "/config/libreoffice/config.json")
        self.assertGreaterEqual(len(calls), 1, "At least one GET /config call expected")

        first_headers = {k.lower(): v for k, v in calls[0]["headers"].items()}
        self.assertNotIn("x-relay-client", first_headers,
                         "No relay headers on first bootstrap fetch")
        self.assertNotIn("x-relay-key", first_headers,
                         "No relay key header on first bootstrap fetch")

    # ------------------------------------------------------------------
    # Steps 3 + 4: PKCE — browser opens, code received, tokens stored
    # ------------------------------------------------------------------

    def test_02_pkce_flow_stores_tokens(self):
        """
        Steps 3-4: PKCE flow completes → access_token + refresh_token saved to config.json.
        webbrowser.open is intercepted; the auth code is sent automatically to the
        local callback server started by _wait_for_auth_code.
        """
        captured = {}

        def fake_browser_open(url):
            captured["auth_url"] = url
            _pkce_auto_callback(url, delay=0.2)

        # Pre-load DM config so keycloak endpoints are available
        self.job.config_cache = DM_PUBLIC_CONFIG
        self.job.config_loaded_at = time.time()

        with self._patch_urlopen(), \
             self._patch_confirm(True), \
             self._patch_enrollment_wizard(True), \
             self._patch_show_message(), \
             patch("webbrowser.open", side_effect=fake_browser_open):
            self.job._authorization_code_flow(DM_PUBLIC_CONFIG)

        self.assertIn("auth_url", captured, "webbrowser.open must have been called")

        # Verify PKCE parameters in auth URL
        params = urllib.parse.parse_qs(urllib.parse.urlparse(captured["auth_url"]).query)
        self.assertEqual(params.get("response_type"), ["code"], "response_type must be 'code'")
        self.assertEqual(params.get("code_challenge_method"), ["S256"], "S256 challenge required")
        self.assertIn("code_challenge", params, "code_challenge must be present")
        self.assertIn("state", params, "state must be present")

        # Keycloak /token must have been called
        self.assertTrue(
            self.router.called("POST", "/protocol/openid-connect/token"),
            "Keycloak /token endpoint must be called for code exchange"
        )

        # Tokens must be persisted to config.json
        saved = read_user_config(self.tmpdir)
        self.assertIn("access_token", saved, "access_token must be saved to config.json")
        self.assertIn("refresh_token", saved, "refresh_token must be saved to config.json")

    # ------------------------------------------------------------------
    # Steps 5 + 6: Device Management enrollment
    # ------------------------------------------------------------------

    def test_03_enroll_stores_relay_credentials(self):
        """
        Steps 5-6: POST /enroll with Bearer token → relay credentials stored.
        """
        # Pre-load tokens so _ensure_access_token skips interactive PKCE
        self.job.set_config("access_token", FAKE_ACCESS_TOKEN)
        self.job.set_config("access_token_expires_at", 9_999_999_999)
        # Pre-load DM config so enroll endpoint is found
        self.job.config_cache = DM_PUBLIC_CONFIG
        self.job.config_loaded_at = time.time()

        with self._patch_urlopen(), self._patch_show_message():
            self.job._ensure_device_management_state()

        # Enrollment POST must have occurred
        self.assertTrue(
            self.router.called("POST", "/enroll"),
            "POST /enroll must be called"
        )

        # Bearer token must be in enrollment request
        enroll_calls = self.router.calls_for("POST", "/enroll")
        enroll_headers = {k.lower(): v for k, v in enroll_calls[0]["headers"].items()}
        auth = enroll_headers.get("authorization", "")
        self.assertIn("Bearer", auth, "Enrollment must use Authorization: Bearer <token>")

        # Relay credentials persisted
        saved = read_user_config(self.tmpdir)
        self.assertEqual(saved.get("relay_client_id"), RELAY_CLIENT_ID,
                         "relayClientId must be persisted")
        self.assertEqual(saved.get("relay_client_key"), RELAY_CLIENT_KEY,
                         "relayClientKey must be persisted")
        self.assertTrue(saved.get("enrolled"), "enrolled flag must be True")

    # ------------------------------------------------------------------
    # Steps 7 + 8: Re-fetch config with relay headers → secrets returned
    # ------------------------------------------------------------------

    def test_04_second_config_fetch_uses_relay_headers(self):
        """
        Steps 7-8: After enrollment, re-fetch includes X-Relay-Client + X-Relay-Key.
        """
        # Inject relay creds as if enrollment just completed
        self.job.set_config("relay_client_id", RELAY_CLIENT_ID)
        self.job.set_config("relay_client_key", RELAY_CLIENT_KEY)
        # Invalidate cache to force a fresh fetch
        self.job.config_cache = None
        self.job.config_loaded_at = 0

        with self._patch_urlopen():
            config = self.job._fetch_config(force=True)

        self.assertIsNotNone(config, "Config should be returned after enrollment")

        calls = self.router.calls_for("GET", "/config/libreoffice/config.json")
        self.assertGreaterEqual(len(calls), 1)

        relay_call = next(
            (c for c in calls
             if any(k.lower() in ("x-relay-client", "x-relay-key")
                    for k in c["headers"])),
            None,
        )
        self.assertIsNotNone(relay_call,
                             "At least one config fetch must include relay headers after enrollment")

    # ------------------------------------------------------------------
    # Steps 9 + 10: LLM call with correct credentials
    # ------------------------------------------------------------------

    def test_05_llm_call_uses_secret_token(self):
        """
        Steps 9-10: make_api_request targets /chat/completions with the LLM secret token.
        """
        # Simulate post-enrollment state: config cache holds LLM secrets
        self.job.config_cache = DM_SECRET_CONFIG
        self.job.config_loaded_at = time.time()
        self.job.set_config("llm_base_urls", LLM_BASE)
        self.job.set_config("llm_api_tokens", LLM_SECRET_TOKEN)
        self.job.set_config("llm_default_models", "mistral:7b")
        self.job.set_config("api_type", "chat")

        with self._patch_urlopen():
            req = self.job.make_api_request(
                prompt="Dis bonjour.",
                system_prompt="Tu es un assistant.",
                max_tokens=50,
                api_type="chat",
            )

        self.assertIsNotNone(req, "make_api_request must return a Request object")
        req_headers = {k.lower(): v for k, v in dict(req.headers).items()}
        auth = req_headers.get("authorization", "")
        self.assertIn(LLM_SECRET_TOKEN, auth,
                      "LLM request must carry the secret token")
        self.assertIn("chat/completions", req.full_url,
                      "Chat API must target /chat/completions")

    # ------------------------------------------------------------------
    # Golden path: all steps in sequence
    # ------------------------------------------------------------------

    def test_06_full_sequence_in_order(self):
        """
        Runs all 10 steps in sequence, verifying each stage of the
        device-management README Secure Relay Flow diagram.
        """
        captured_browser = {}

        def fake_browser(url):
            captured_browser["url"] = url
            _pkce_auto_callback(url, delay=0.2)

        # ── Step 1-2: Bootstrap config (no relay headers) ──────────────
        with self._patch_urlopen():
            config = self.job._fetch_config(force=True)
        self.assertIsNotNone(config, "Step 1: bootstrap config must be returned")
        step1 = self.router.calls_for("GET", "/config/libreoffice/config.json")
        self.assertFalse(
            any("x-relay-client" in {k.lower(): v for k, v in c["headers"].items()}
                for c in step1),
            "Step 1: no relay headers on first fetch"
        )
        self.router.calls.clear()

        # ── Steps 3-4: PKCE ────────────────────────────────────────────
        self.job.config_cache = DM_PUBLIC_CONFIG
        self.job.config_loaded_at = time.time()

        with self._patch_urlopen(), \
             self._patch_confirm(True), \
             self._patch_enrollment_wizard(True), \
             self._patch_show_message(), \
             patch("webbrowser.open", side_effect=fake_browser):
            self.job._authorization_code_flow(DM_PUBLIC_CONFIG)

        self.assertIn("url", captured_browser, "Steps 3-4: browser must open for PKCE")
        self.assertTrue(self.router.called("POST", "/protocol/openid-connect/token"),
                        "Steps 3-4: Keycloak /token must be called")
        saved = read_user_config(self.tmpdir)
        self.assertIn("access_token", saved, "Step 4: access_token must be stored")
        self.router.calls.clear()

        # ── Steps 5-6: Enrollment ───────────────────────────────────────
        self.job.config_cache = DM_PUBLIC_CONFIG
        self.job.config_loaded_at = time.time()

        with self._patch_urlopen(), self._patch_show_message():
            self.job._ensure_device_management_state()

        self.assertTrue(self.router.called("POST", "/enroll"),
                        "Step 5: POST /enroll must be called")
        saved = read_user_config(self.tmpdir)
        self.assertEqual(saved.get("relay_client_id"), RELAY_CLIENT_ID,
                         "Step 6: relay_client_id must be stored")
        self.assertTrue(saved.get("enrolled"), "Step 6: enrolled flag must be set")
        self.router.calls.clear()

        # ── Steps 7-8: Re-fetch with relay headers ──────────────────────
        self.job.config_cache = None
        self.job.config_loaded_at = 0
        # Garde de récursion : _ensure_device_management_state a pu la laisser
        # armée. Sans cette remise à zéro, _fetch_config sort immédiatement et
        # l'assertion suivante porte sur une liste d'appels vide — donc verte
        # pour de mauvaises raisons, ou rouge sans rapport avec les en-têtes.
        self.job._fetching_config = False

        with self._patch_urlopen():
            self.job._fetch_config(force=True)

        dm_calls = self.router.calls_for("GET", "/config/libreoffice/config.json")
        self.assertTrue(dm_calls, "Steps 7-8: le re-fetch doit réellement partir")
        has_relay = any(
            "x-relay-client" in {k.lower(): v for k, v in c["headers"].items()}
            for c in dm_calls
        )
        self.assertTrue(has_relay, "Steps 7-8: config re-fetch must include relay headers")
        self.router.calls.clear()

        # ── Steps 9-10: LLM call ────────────────────────────────────────
        self.job.config_cache = DM_SECRET_CONFIG
        self.job.config_loaded_at = time.time()
        self.job.set_config("llm_base_urls", LLM_BASE)
        self.job.set_config("llm_api_tokens", LLM_SECRET_TOKEN)
        self.job.set_config("api_type", "chat")

        with self._patch_urlopen():
            req = self.job.make_api_request("Dis bonjour.", max_tokens=20, api_type="chat")

        req_headers = {k.lower(): v for k, v in dict(req.headers).items()}
        self.assertIn(LLM_SECRET_TOKEN, req_headers.get("authorization", ""),
                      "Steps 9-10: LLM request must carry the secret credential")
        self.assertIn("chat/completions", req.full_url,
                      "Steps 9-10: LLM call must target /chat/completions")


if __name__ == "__main__":
    unittest.main()
