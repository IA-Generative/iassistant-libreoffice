"""
Unit tests for src/mirai/calc_prompt_function.py
"""
import io
import json
import ssl
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

# ── Install UNO stubs FIRST so the module can be imported outside LibreOffice ──
from tests.stubs.helpers import http_response
from tests.stubs.uno_stubs import install

install()

# ── Now safe to import the module under test ──────────────────────────────────
from src.mirai.calc_prompt_function import (
    PromptFunction,
    build_ssl_context,
    call_llm,
    load_config,
)

# Helpers

def _make_ctx(config_dir: str = "/tmp/test_calc_prompt"):
    """Build a minimal UNO context mock that points PathSettings at config_dir."""
    path_settings = MagicMock()
    path_settings.UserConfig = config_dir

    service_manager = MagicMock()
    service_manager.createInstanceWithContext.return_value = path_settings

    ctx = MagicMock()
    ctx.getServiceManager.return_value = service_manager
    ctx.ServiceManager = service_manager
    return ctx


def _chat_response(content: str, status: int = 200) -> MagicMock:
    """Fake urllib response for a chat-completions JSON body."""
    payload = {
        "choices": [
            {"message": {"content": content}, "finish_reason": "stop"}
        ]
    }
    return http_response(payload, status)


def _completions_response(text: str) -> MagicMock:
    """Fake urllib response for a legacy completions JSON body."""
    payload = {
        "choices": [{"text": text, "finish_reason": "stop"}]
    }
    return http_response(payload)


def _base_config() -> dict:
    return {
        "llm_base_urls": "http://localhost:11434",
        "llm_api_tokens": "test-token",
        "authHeaderName": "Authorization",
        "authHeaderPrefix": "Bearer ",
        "llm_default_models": "llama3",
        "llm_request_timeout_seconds": 30,
        "proxy_allow_insecure_ssl": False,
    }


# Tests for call_llm()

class TestCallLlmSuccess(unittest.TestCase):
    """Happy-path: the LLM returns a chat-completions response."""

    def setUp(self):
        self.config = _base_config()
        self.ssl_ctx = ssl.create_default_context()

    def _patch_urlopen(self, response):
        return patch(
            "src.mirai.calc_prompt_function._urlopen",
            return_value=response,
        )

    def test_chat_format_returns_content(self):
        resp = _chat_response("Hello from the LLM")
        with self._patch_urlopen(resp):
            result = call_llm(
                message="Hi",
                system_prompt="",
                model="llama3",
                max_tokens=100,
                config=self.config,
                ssl_context=self.ssl_ctx,
            )
        self.assertEqual(result, "Hello from the LLM")

    def test_completions_format_returns_text(self):
        """Legacy completions: choices[0].text (no .message key)."""
        resp = _completions_response("Legacy completions answer")
        with self._patch_urlopen(resp):
            result = call_llm(
                message="What is 2+2?",
                system_prompt="",
                model="",
                max_tokens=50,
                config=self.config,
                ssl_context=self.ssl_ctx,
            )
        self.assertEqual(result, "Legacy completions answer")

    def test_stream_false_in_request_body(self):
        """Verify that stream=False is set in the serialised JSON body."""
        captured_body: list[bytes] = []

        def fake_urlopen(req, ssl_context, timeout):
            captured_body.append(req.data)
            return _chat_response("ok")

        with patch("src.mirai.calc_prompt_function._urlopen", side_effect=fake_urlopen):
            call_llm(
                message="test",
                system_prompt="",
                model="m",
                max_tokens=10,
                config=self.config,
                ssl_context=self.ssl_ctx,
            )

        self.assertTrue(captured_body, "No request body was captured")
        body = json.loads(captured_body[0].decode("utf-8"))
        self.assertIn("stream", body)
        self.assertFalse(body["stream"], "stream must be False for synchronous Calc use")

    def test_system_prompt_sent_as_system_message(self):
        """When system_prompt is provided it must appear as role=system in messages."""
        captured_body: list[bytes] = []

        def fake_urlopen(req, ssl_context, timeout):
            captured_body.append(req.data)
            return _chat_response("done")

        with patch("src.mirai.calc_prompt_function._urlopen", side_effect=fake_urlopen):
            call_llm(
                message="user msg",
                system_prompt="You are a helpful assistant.",
                model="m",
                max_tokens=10,
                config=self.config,
                ssl_context=self.ssl_ctx,
            )

        body = json.loads(captured_body[0].decode("utf-8"))
        messages = body.get("messages", [])
        roles = [m["role"] for m in messages]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        system_msg = next(m for m in messages if m["role"] == "system")
        self.assertIn("helpful assistant", system_msg["content"])

    def test_no_system_prompt_omits_system_message(self):
        captured_body: list[bytes] = []

        def fake_urlopen(req, ssl_context, timeout):
            captured_body.append(req.data)
            return _chat_response("done")

        with patch("src.mirai.calc_prompt_function._urlopen", side_effect=fake_urlopen):
            call_llm("msg", "", "m", 10, self.config, self.ssl_ctx)

        body = json.loads(captured_body[0].decode("utf-8"))
        roles = [m["role"] for m in body.get("messages", [])]
        self.assertNotIn("system", roles)

    def test_model_set_in_body(self):
        captured_body: list[bytes] = []

        def fake_urlopen(req, ssl_context, timeout):
            captured_body.append(req.data)
            return _chat_response("done")

        with patch("src.mirai.calc_prompt_function._urlopen", side_effect=fake_urlopen):
            call_llm("msg", "", "my-special-model", 10, self.config, self.ssl_ctx)

        body = json.loads(captured_body[0].decode("utf-8"))
        self.assertEqual(body.get("model"), "my-special-model")

    def test_auth_header_set_when_api_key_present(self):
        captured_headers: list[dict] = []

        def fake_urlopen(req, ssl_context, timeout):
            captured_headers.append(dict(req.headers))
            return _chat_response("done")

        with patch("src.mirai.calc_prompt_function._urlopen", side_effect=fake_urlopen):
            call_llm("msg", "", "", 10, self.config, self.ssl_ctx)

        hdrs = captured_headers[0]
        # urllib capitalises header names
        auth_value = hdrs.get("Authorization") or hdrs.get("authorization")
        self.assertIsNotNone(auth_value, "Authorization header missing")
        self.assertIn("test-token", auth_value)

    def test_no_auth_header_when_api_key_empty(self):
        config = dict(self.config)
        config["llm_api_tokens"] = ""
        captured_headers: list[dict] = []

        def fake_urlopen(req, ssl_context, timeout):
            captured_headers.append(dict(req.headers))
            return _chat_response("done")

        with patch("src.mirai.calc_prompt_function._urlopen", side_effect=fake_urlopen):
            call_llm("msg", "", "", 10, config, self.ssl_ctx)

        hdrs = captured_headers[0]
        self.assertNotIn("Authorization", hdrs)
        self.assertNotIn("authorization", hdrs)


# Tests for error handling in call_llm()

class TestCallLlmErrors(unittest.TestCase):

    def setUp(self):
        self.config = _base_config()
        self.ssl_ctx = ssl.create_default_context()

    def _patch_urlopen(self, side_effect):
        return patch(
            "src.mirai.calc_prompt_function._urlopen",
            side_effect=side_effect,
        )

    def test_http_error_returns_error_string(self):
        http_err = urllib.error.HTTPError(
            url="http://localhost/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b"invalid token"),
        )
        with self._patch_urlopen(http_err):
            result = call_llm("msg", "", "", 10, self.config, self.ssl_ctx)

        self.assertTrue(
            result.startswith("#PROMPT_ERROR:"),
            f"Expected error string, got: {result!r}",
        )
        self.assertIn("401", result)

    def test_network_error_returns_error_string(self):
        url_err = urllib.error.URLError(reason="Connection refused")
        with self._patch_urlopen(url_err):
            result = call_llm("msg", "", "", 10, self.config, self.ssl_ctx)

        self.assertTrue(result.startswith("#PROMPT_ERROR:"))
        self.assertIn("Connection refused", result)

    def test_invalid_json_returns_error_string(self):
        resp = MagicMock()
        resp.read.return_value = b"not json at all"
        resp.__exit__ = MagicMock(return_value=False)
        with patch("src.mirai.calc_prompt_function._urlopen", return_value=resp):
            result = call_llm("msg", "", "", 10, self.config, self.ssl_ctx)

        self.assertTrue(result.startswith("#PROMPT_ERROR:"))

    def test_unexpected_exception_returns_error_string(self):
        with self._patch_urlopen(RuntimeError("unexpected")):
            result = call_llm("msg", "", "", 10, self.config, self.ssl_ctx)

        self.assertTrue(result.startswith("#PROMPT_ERROR:"))
        self.assertIn("unexpected", result)

    def test_no_exception_raised_on_any_error(self):
        """call_llm must never raise — always return a string."""
        for exc in [
            urllib.error.HTTPError("u", 500, "ISE", {}, io.BytesIO(b"")),
            urllib.error.URLError("timeout"),
            RuntimeError("boom"),
            ValueError("bad"),
        ]:
            with patch("src.mirai.calc_prompt_function._urlopen", side_effect=exc):
                try:
                    result = call_llm("msg", "", "", 10, self.config, self.ssl_ctx)
                    self.assertIsInstance(result, str)
                except Exception as propagated:
                    self.fail(f"call_llm raised {propagated!r} instead of returning an error string")


# Tests for PromptFunction (UNO component)

class TestPromptFunction(unittest.TestCase):

    def _make_fn(self, config_overrides=None):
        ctx = _make_ctx()
        fn = PromptFunction(ctx)
        config = _base_config()
        if config_overrides:
            config.update(config_overrides)
        fn._config = config  # bypass file I/O
        fn._ssl_ctx = ssl.create_default_context()
        return fn

    def _patch_call_llm(self, return_value):
        return patch("src.mirai.calc_prompt_function.call_llm", return_value=return_value)

    def test_prompt_delegates_to_call_llm(self):
        fn = self._make_fn()
        with self._patch_call_llm("LLM answer"):
            result = fn.prompt("Hello")
        self.assertEqual(result, "LLM answer")

    def test_prompt_uses_config_default_model_when_none_given(self):
        fn = self._make_fn()
        captured: list[dict] = []

        def fake_call_llm(**kwargs):
            captured.append(kwargs)
            return "ok"

        with patch("src.mirai.calc_prompt_function.call_llm", side_effect=fake_call_llm):
            fn.prompt("Hello")

        self.assertEqual(captured[0]["model"], "llama3")  # from _base_config

    def test_prompt_uses_explicit_model_when_given(self):
        fn = self._make_fn()
        captured: list[dict] = []

        def fake_call_llm(**kwargs):
            captured.append(kwargs)
            return "ok"

        with patch("src.mirai.calc_prompt_function.call_llm", side_effect=fake_call_llm):
            fn.prompt("Hello", model="gpt-4o")

        self.assertEqual(captured[0]["model"], "gpt-4o")

    def test_prompt_max_tokens_defaults_to_2048(self):
        fn = self._make_fn()
        captured: list[dict] = []

        def fake_call_llm(**kwargs):
            captured.append(kwargs)
            return "ok"

        with patch("src.mirai.calc_prompt_function.call_llm", side_effect=fake_call_llm):
            fn.prompt("Hello")

        self.assertEqual(captured[0]["max_tokens"], 2048)

    def test_prompt_invalid_max_tokens_defaults_to_2048(self):
        fn = self._make_fn()
        captured: list[dict] = []

        def fake_call_llm(**kwargs):
            captured.append(kwargs)
            return "ok"

        with patch("src.mirai.calc_prompt_function.call_llm", side_effect=fake_call_llm):
            fn.prompt("Hello", max_tokens="notanumber")

        self.assertEqual(captured[0]["max_tokens"], 2048)

    def test_prompt_returns_error_string_not_exception(self):
        fn = self._make_fn()
        with self._patch_call_llm("#PROMPT_ERROR: HTTP 500"):
            result = fn.prompt("test")
        self.assertIsInstance(result, str)
        self.assertIn("#PROMPT_ERROR", result)

    def test_getImplementationName(self):
        fn = self._make_fn()
        self.assertEqual(
            fn.getImplementationName(),
            "fr.gouv.interieur.mirai.PromptFunction",
        )

    def test_supportsService(self):
        fn = self._make_fn()
        self.assertTrue(fn.supportsService("com.sun.star.sheet.AddIn"))
        self.assertFalse(fn.supportsService("com.sun.star.text.TextDocument"))


# Tests for build_ssl_context()

class TestBuildSslContext(unittest.TestCase):

    def test_returns_ssl_context(self):
        ctx = build_ssl_context({})
        self.assertIsInstance(ctx, ssl.SSLContext)

    def test_insecure_flag_sets_cert_none(self):
        ctx = build_ssl_context({"proxy_allow_insecure_ssl": True})
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)

    def test_default_is_not_cert_none(self):
        ctx = build_ssl_context({"proxy_allow_insecure_ssl": False})
        self.assertNotEqual(ctx.verify_mode, ssl.CERT_NONE)

    def test_loads_bundled_ca_if_present(self):
        """If the bundled PEM exists, it should be loaded without error."""
        import os

        from src.mirai.calc_prompt_function import _get_bundled_ca_path

        bundled = _get_bundled_ca_path()
        if not os.path.isfile(bundled):
            self.skipTest("Bundled CA chain not present in test environment")

        # Should not raise
        ctx = build_ssl_context({})
        self.assertIsInstance(ctx, ssl.SSLContext)


# Tests for load_config()

class TestLoadConfig(unittest.TestCase):

    def test_returns_dict(self):
        ctx = _make_ctx("/tmp")
        result = load_config(ctx)
        self.assertIsInstance(result, dict)

    def test_reads_user_config_json(self):
        import json
        import os
        import shutil
        import tempfile

        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as fh:
            json.dump({"llm_base_urls": "http://my-server:8080", "llm_api_tokens": "secret"}, fh)

        ctx = _make_ctx(tmpdir)
        result = load_config(ctx)
        self.assertEqual(result.get("llm_base_urls"), "http://my-server:8080")
        self.assertEqual(result.get("llm_api_tokens"), "secret")

    def test_returns_empty_dict_on_broken_context(self):
        ctx = MagicMock()
        ctx.getServiceManager.side_effect = RuntimeError("no service manager")
        result = load_config(ctx)
        self.assertIsInstance(result, dict)


# Sources de la configuration et du jeton (disposition mirai/)

def test_load_config_reads_settings_and_dm_snapshot(tmp_path):
    from src.mirai import local_config
    cfg = local_config.LocalConfig(str(tmp_path), [])
    cfg.set("llm_default_models", "modele-choisi")
    cfg.save_dm_snapshot({"config": {"llm_base_urls": "https://dm/llm/v1"}})
    config = load_config(_make_ctx(str(tmp_path)))
    assert config["llm_default_models"] == "modele-choisi"
    assert config["llm_base_urls"] == "https://dm/llm/v1"


def test_prompt_uses_the_remembered_dm_token(tmp_path):
    from src.mirai import credentials
    credentials.remember(credentials.DM_LLM_TOKEN, "dm-token-123")
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"llm_base_urls": "https://dm/llm/v1"}
    seen = {}

    def _fake_call(**kwargs):
        seen.update(kwargs["config"])
        return "ok"

    with patch("src.mirai.calc_prompt_function.call_llm", side_effect=_fake_call):
        assert fn.prompt("bonjour") == "ok"
    assert seen["llm_api_tokens"] == "dm-token-123"


def test_prompt_without_token_returns_a_readable_error(tmp_path):
    from src.mirai import credentials
    credentials.forget_all()
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"llm_base_urls": "https://dm/llm/v1"}
    result = fn.prompt("bonjour")
    assert result.startswith("#PROMPT_ERROR:")


def test_prompt_reloads_a_config_cached_without_endpoint(tmp_path):
    from src.mirai import credentials
    credentials.remember(credentials.DM_LLM_TOKEN, "dm-token-123")
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"enabled": True}
    seen = {}

    def _fake_call(**kwargs):
        seen.update(kwargs["config"])
        return "ok"

    with patch("src.mirai.calc_prompt_function.load_config",
               return_value={"enabled": True, "llm_base_urls": "https://dm/llm/v1"}), \
            patch("src.mirai.calc_prompt_function.call_llm", side_effect=_fake_call):
        assert fn.prompt("bonjour") == "ok"
    assert seen["llm_base_urls"] == "https://dm/llm/v1"
    assert seen["llm_api_tokens"] == "dm-token-123"


def test_prompt_without_endpoint_never_calls_the_llm(tmp_path):
    from src.mirai import credentials
    credentials.remember(credentials.DM_LLM_TOKEN, "dm-token-123")
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {}
    with patch("src.mirai.calc_prompt_function.load_config", return_value={}), \
            patch("src.mirai.calc_prompt_function.call_llm") as call:
        assert fn.prompt("bonjour").startswith("#PROMPT_ERROR:")
    call.assert_not_called()


def test_offline_tier_calls_the_llm_without_key(tmp_path):
    from src.mirai import credentials
    credentials.forget_all()
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"enabled": False, "llm_base_urls": "http://localhost:11434/v1"}
    seen = {}

    def _fake_call(**kwargs):
        seen.update(kwargs["config"])
        return "ok"

    with patch("src.mirai.calc_prompt_function.call_llm", side_effect=_fake_call):
        assert fn.prompt("bonjour") == "ok"
    assert seen["llm_api_tokens"] == ""


def test_dm_tier_without_token_still_returns_the_error(tmp_path):
    from src.mirai import credentials
    credentials.forget_all()
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"enabled": True, "llm_base_urls": "https://dm/llm/v1"}
    with patch("src.mirai.calc_prompt_function.call_llm") as call:
        assert fn.prompt("bonjour").startswith("#PROMPT_ERROR:")
    call.assert_not_called()


def test_prompt_uses_the_user_key_from_the_store(tmp_path):
    from src.mirai import credentials
    credentials.forget_all()
    credentials.set_secret("llm_api_tokens", "user-key", "")
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"enabled": False, "llm_base_urls": "http://localhost:11434/v1"}
    seen = {}

    def _fake_call(**kwargs):
        seen.update(kwargs["config"])
        return "ok"

    with patch("src.mirai.calc_prompt_function.call_llm", side_effect=_fake_call):
        assert fn.prompt("bonjour") == "ok"
    assert seen["llm_api_tokens"] == "user-key"


def test_prompt_prefers_the_session_copy_of_a_key_refused_by_the_store(tmp_path):
    from src.mirai import credentials
    credentials.forget_all()
    credentials.set_secret("llm_api_tokens", "ancienne", "")
    credentials.remember("llm_api_tokens", "nouvelle")
    fn = PromptFunction(_make_ctx(str(tmp_path)))
    fn._config = {"enabled": False, "llm_base_urls": "http://localhost:11434/v1"}
    seen = {}

    def _fake_call(**kwargs):
        seen.update(kwargs["config"])
        return "ok"

    with patch("src.mirai.calc_prompt_function.call_llm", side_effect=_fake_call):
        assert fn.prompt("bonjour") == "ok"
    assert seen["llm_api_tokens"] == "nouvelle"


if __name__ == "__main__":
    unittest.main()
