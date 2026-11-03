"""
Tests for _fetch_models / _build_auth_headers in MainJob.
HTTP calls are intercepted via unittest.mock.patch.
"""
import unittest
from unittest.mock import MagicMock, patch

from tests.stubs.helpers import http_response
from tests.stubs.uno_stubs import install, make_job

install()


class TestBuildAuthHeaders(unittest.TestCase):
    def setUp(self):
        self.job = make_job()
        # Stub get_config to return predictable auth header config
        self.job.get_config = lambda key, default=None: {
            "authHeaderName": "Authorization",
            "authHeaderPrefix": "Bearer ",
        }.get(key, default)

    def test_no_api_key_omits_auth(self):
        headers = self.job._build_auth_headers("")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_api_key_adds_bearer(self):
        headers = self.job._build_auth_headers("mytoken")
        self.assertEqual(headers["Authorization"], "Bearer mytoken")

    def test_custom_header_name(self):
        self.job.get_config = lambda key, default=None: {
            "authHeaderName": "X-Api-Key",
            "authHeaderPrefix": "",
        }.get(key, default)
        headers = self.job._build_auth_headers("secret")
        self.assertIn("X-Api-Key", headers)


class TestFetchModels(unittest.TestCase):
    def setUp(self):
        self.job = make_job()
        self.job.get_config = lambda key, default=None: {
            "authHeaderName": "Authorization",
            "authHeaderPrefix": "Bearer ",
        }.get(key, default)
        self.job._get_openwebui_access_token = lambda: ""
        self.job.get_ssl_context = lambda: None

    def _patch_urlopen(self, response):
        return patch.object(self.job, "_urlopen", return_value=response)

    def test_openai_data_format(self):
        payload = {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5-turbo"}]}
        with self._patch_urlopen(http_response(payload)):
            models = self.job._fetch_models("https://api.example.com", "key")
        self.assertEqual(models, ["gpt-4", "gpt-3.5-turbo"])

    def test_ollama_models_format(self):
        payload = {"models": [{"name": "llama3"}, {"name": "mistral"}]}
        with self._patch_urlopen(http_response(payload)):
            models = self.job._fetch_models("https://api.example.com", "key")
        self.assertEqual(models, ["llama3", "mistral"])

    def test_plain_list_format(self):
        payload = ["model-a", "model-b"]
        with self._patch_urlopen(http_response(payload)):
            models = self.job._fetch_models("https://api.example.com", "key")
        self.assertEqual(models, ["model-a", "model-b"])

    def test_http_error_returns_empty_list(self):
        import urllib.error
        self.job._urlopen = MagicMock(side_effect=urllib.error.URLError("timeout"))
        models = self.job._fetch_models("https://api.example.com", "key")
        self.assertEqual(models, [])

    def test_include_info_returns_descriptions(self):
        payload = {"data": [
            {"id": "gpt-4", "description": "Most capable model"},
            {"id": "gpt-3.5-turbo"},
        ]}
        with self._patch_urlopen(http_response(payload)):
            models, descriptions = self.job._fetch_models(
                "https://api.example.com", "key", include_info=True
            )
        self.assertEqual(models, ["gpt-4", "gpt-3.5-turbo"])
        self.assertEqual(descriptions["gpt-4"], "Most capable model")
        self.assertNotIn("gpt-3.5-turbo", descriptions)

    def test_include_info_openwebui_nested_description(self):
        payload = {"data": [
            {"id": "llama3", "info": {"meta": {"description": "Fast LLM"}}},
        ]}
        with self._patch_urlopen(http_response(payload)):
            models, descriptions = self.job._fetch_models(
                "https://api.example.com/api", "key", include_info=True
            )
        self.assertIn("llama3", models)
        self.assertEqual(descriptions["llama3"], "Fast LLM")

    def test_models_are_listed_under_api_unless_the_endpoint_names_its_path(self):
        urls = []

        def record(request, context=None, timeout=None):
            urls.append(request.full_url)
            return http_response({"data": []})

        self.job._urlopen = record
        for endpoint in ("https://h.example", "https://h.example/api", "https://h.example/v1/"):
            self.job._fetch_models(endpoint, "key")
        self.assertEqual(urls, ["https://h.example/api/models", "https://h.example/api/models",
                                "https://h.example/v1/models"])


if __name__ == "__main__":
    unittest.main()
