"""
MockHttpRouter — intercepts _urlopen calls and routes them to registered handlers.

Usage:
    router = MockHttpRouter()
    router.add("GET", "/config/libreoffice/config.json", body={"keycloak_url": "..."})
    router.add("POST", "/token", body={"access_token": "...", "refresh_token": "..."})

    with patch.object(job, "_urlopen", side_effect=router):
        job._fetch_config()

    assert router.called("GET", "/config/libreoffice/config.json")
"""
import json
import urllib.error
import urllib.request


class _FakeResponse:
    """Mimics urllib response as a context manager."""

    def __init__(self, status, body_bytes, headers=None):
        self.status = status
        self._body = body_bytes
        self.headers = headers or {}

    def read(self):
        return self._body

    def readable(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class MockHttpRouter:
    """URL-routing mock for _urlopen calls."""

    def __init__(self):
        self.calls = []
        self._routes = []

    def add(self, method, url_contains, body=None, status=200, headers=None):
        """Register a handler.

        Args:
            method:       HTTP method ("GET", "POST", …)
            url_contains: substring that must appear in the URL
            body:         dict → JSON-encoded, bytes → raw, None → empty
            status:       HTTP status code
            headers:      dict of response headers
        """
        if isinstance(body, dict):
            raw = json.dumps(body).encode()
        elif isinstance(body, str):
            raw = body.encode()
        elif body is None:
            raw = b""
        else:
            raw = body
        self._routes.append({
            "method": method.upper(),
            "url_contains": url_contains,
            "status": status,
            "raw": raw,
            "headers": headers or {},
        })

    # Same signature as MainJob._urlopen (whose use_proxy defaults to True).

    def __call__(self, request, context=None, timeout=None, use_proxy=None):
        if isinstance(request, urllib.request.Request):
            method = request.get_method()
            url = request.full_url
            req_headers = dict(request.headers)
        else:
            method = "GET"
            url = str(request)
            req_headers = {}

        self.calls.append({"method": method, "url": url, "headers": req_headers})

        for route in self._routes:
            if route["method"] != method.upper():
                continue
            if route["url_contains"] not in url:
                continue
            if route["status"] >= 400:
                raise urllib.error.HTTPError(url, route["status"], "mock error", {}, None)
            return _FakeResponse(route["status"], route["raw"], route["headers"])

        raise urllib.error.URLError(f"MockHttpRouter: no handler for {method} {url}")

    def called(self, method, url_contains):
        """Return True if a call matching method + URL substring was made."""
        return any(
            c["method"].upper() == method.upper() and url_contains in c["url"]
            for c in self.calls
        )

    def calls_for(self, method, url_contains):
        """Return all recorded calls matching method + URL substring."""
        return [
            c for c in self.calls
            if c["method"].upper() == method.upper() and url_contains in c["url"]
        ]

    def call_count(self, method, url_contains):
        return len(self.calls_for(method, url_contains))
