"""
Tests for User-Agent construction and propagation.
"""
import unittest

from tests.stubs.uno_stubs import install

install()

from src.mirai.entrypoint import (
    PLUGIN_NAME,
    _with_user_agent,
    build_user_agent,
    get_user_agent,
    set_user_agent,
)


class TestBuildUserAgent(unittest.TestCase):
    """Test build_user_agent() formatting."""

    def test_no_versions(self):
        self.assertEqual(build_user_agent(), PLUGIN_NAME)

    def test_plugin_version_only(self):
        self.assertEqual(
            build_user_agent(plugin_version="1.2.3"),
            "MIrAI-LibreOffice/1.2.3",
        )

    def test_lo_version_only(self):
        self.assertEqual(
            build_user_agent(lo_version="24.8.0"),
            "MIrAI-LibreOffice LibreOffice/24.8.0",
        )

    def test_both_versions(self):
        self.assertEqual(
            build_user_agent(plugin_version="0.1.0", lo_version="24.8.0"),
            "MIrAI-LibreOffice/0.1.0 LibreOffice/24.8.0",
        )

    def test_empty_strings_treated_as_absent(self):
        self.assertEqual(build_user_agent(plugin_version="", lo_version=""), PLUGIN_NAME)


class TestSetGetUserAgent(unittest.TestCase):
    """Test set_user_agent / get_user_agent module-level state."""

    def tearDown(self):
        # Reset to default after each test
        set_user_agent()

    def test_default_user_agent(self):
        set_user_agent()
        self.assertEqual(get_user_agent(), PLUGIN_NAME)

    def test_set_updates_get(self):
        set_user_agent("2.0.0", "25.2.1")
        self.assertEqual(get_user_agent(), "MIrAI-LibreOffice/2.0.0 LibreOffice/25.2.1")


class TestWithUserAgent(unittest.TestCase):
    """Test _with_user_agent() header injection."""

    def tearDown(self):
        set_user_agent()

    def test_adds_user_agent_when_missing(self):
        set_user_agent("1.0.0", "24.8.0")
        headers = _with_user_agent({"Content-Type": "application/json"})
        self.assertEqual(headers["User-Agent"], "MIrAI-LibreOffice/1.0.0 LibreOffice/24.8.0")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_does_not_overwrite_existing(self):
        headers = _with_user_agent({"User-Agent": "custom/1.0"})
        self.assertEqual(headers["User-Agent"], "custom/1.0")

    def test_none_headers(self):
        set_user_agent("1.0.0")
        headers = _with_user_agent(None)
        self.assertEqual(headers["User-Agent"], "MIrAI-LibreOffice/1.0.0")

    def test_empty_headers(self):
        headers = _with_user_agent({})
        self.assertIn("User-Agent", headers)


if __name__ == "__main__":
    unittest.main()
