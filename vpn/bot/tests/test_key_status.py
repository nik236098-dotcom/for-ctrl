import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ruvpn_bot.db import Storage
from ruvpn_bot.key_status import key_status


class KeyStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Storage(Path(self.tmp.name) / "test.sqlite")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)
        self.db.create_user(1, "test", 30)
        self.db.add_device("desktop", 1, "Desktop", "10.0.0.2")
        self.db.store_key("testcode", "desktop", "test config")
        self.deps = SimpleNamespace(db=self.db, servers={"ru": object(), "us": object()})

    def status(self, country="ru", code="testcode"):
        return key_status(self.deps, code, country)

    def test_active_key(self):
        self.assertEqual(self.status(), "active")

    def test_unknown_code(self):
        self.assertEqual(self.status(code="missing"), "revoked")

    def test_expired_user_even_with_cached_config(self):
        self.db.expire_now(1)
        self.assertIsNotNone(self.db.key_config("testcode"))
        self.assertEqual(self.status(), "revoked")

    def test_suspended_and_resumed_device(self):
        self.db.mark_device_suspended("desktop", True)
        self.assertEqual(self.status(), "revoked")
        self.db.mark_device_suspended("desktop", False)
        self.assertEqual(self.status(), "active")

    def test_revoked_device_even_with_cached_config(self):
        self.db.mark_device_revoked("desktop")
        self.assertEqual(self.status(), "revoked")

    def test_unknown_region_is_not_revocation(self):
        self.assertEqual(self.status("xx"), "unknown")

    def test_unprovisioned_region_is_read_only(self):
        before = self.db._db.total_changes
        self.assertEqual(self.status("us"), "unknown")
        self.assertEqual(self.db._db.total_changes, before)
        self.assertIsNone(self.db.device("desktop-us"))

    def test_region_device_access(self):
        self.db.add_device("desktop-us", 1, "Desktop US", "10.0.0.3", country="us")
        self.db.store_key_region("testcode", "us", "desktop-us", "test config us")
        self.assertEqual(self.status("us"), "active")
        self.db.mark_device_suspended("desktop-us", True)
        self.assertEqual(self.status("us"), "revoked")
        self.db.mark_device_suspended("desktop-us", False)
        self.db.mark_device_revoked("desktop-us")
        self.assertEqual(self.status("us"), "revoked")
        self.assertEqual(self.status("ru"), "active")


if __name__ == "__main__":
    unittest.main()
