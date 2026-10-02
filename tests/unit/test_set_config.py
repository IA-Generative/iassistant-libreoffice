"""
Tests for set_config in MainJob.
Verifies atomic writes and concurrent-write safety.
"""
import json
import os
import tempfile
import threading
import unittest

from tests.stubs.uno_stubs import install, make_job

install()


class TestSetConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.job = make_job(config_dir=self.tmpdir)
        self.config_path = os.path.join(self.tmpdir, "mirai", "settings.json")

    def test_writes_key_to_file(self):
        self.job.set_config("my_key", "my_value")
        with open(self.config_path) as f:
            data = json.load(f)
        self.assertEqual(data["my_key"], "my_value")

    def test_preserves_existing_keys(self):
        self.job.set_config("key_a", "value_a")
        self.job.set_config("key_b", "value_b")
        with open(self.config_path) as f:
            data = json.load(f)
        self.assertEqual(data["key_a"], "value_a")
        self.assertEqual(data["key_b"], "value_b")

    def test_overwrites_existing_key(self):
        self.job.set_config("key", "old")
        self.job.set_config("key", "new")
        with open(self.config_path) as f:
            data = json.load(f)
        self.assertEqual(data["key"], "new")

    def test_concurrent_writes_do_not_corrupt(self):
        """20 threads each write a distinct key — all keys must be present."""
        n = 20
        errors = []

        def _write(i):
            try:
                self.job.set_config(f"key_{i}", f"val_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Exceptions during concurrent writes: {errors}")

        with open(self.config_path) as f:
            data = json.load(f)

        for i in range(n):
            self.assertIn(f"key_{i}", data, f"key_{i} missing after concurrent writes")
            self.assertEqual(data[f"key_{i}"], f"val_{i}")

    def test_writes_non_string_values(self):
        self.job.set_config("int_key", 42)
        self.job.set_config("bool_key", True)
        self.job.set_config("list_key", [1, 2, 3])
        with open(self.config_path) as f:
            data = json.load(f)
        self.assertEqual(data["int_key"], 42)
        self.assertEqual(data["bool_key"], True)
        self.assertEqual(data["list_key"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
