"""Profile identity, key ownership and migration checks using synthetic data only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_source import ModelSourceStore, ModelSourceUnavailable, _source_fingerprint


class ProfileIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "source.json"
        self.store = ModelSourceStore(self.path, root=self.root,
                                      protect=lambda value: value.encode(),
                                      unprotect=lambda value: value.decode())

    def profile(self, key="synthetic-a", model="m1"):
        return self.store.save_profile(None, "A", "responses", "https://example.test/v1",
                                       model, key, 8192)

    def test_same_endpoint_edit_uses_its_own_key_and_cleared_key_stays_empty(self):
        first, second = self.profile(), self.profile("synthetic-b")
        self.store.select_profile(first)
        self.assertEqual(self.store.resolve_key("responses", "https://example.test/v1", None,
                                              profile_id=second), "synthetic-b")
        self.store.clear_key(second)
        self.assertIsNone(self.store.resolve_key("responses", "https://example.test/v1", None,
                                               profile_id=second))
        self.assertIsNone(self.store.resolve_key("responses", "https://example.test/v1", None,
                                               profile_id=None))
        self.assertIsNone(self.store.resolve_key("responses", "https://other.test/v1", None,
                                               profile_id=first))

    def test_edit_keeps_management_id_but_switches_inference_identity_and_restores_cache(self):
        profile = self.profile()
        self.store.select_profile(profile)
        original = self.store.saved_selection()["sourceId"]
        self.store.save_profile(profile, "renamed", "responses", "https://example.test/v1",
                                "m1", None, 16384)
        self.assertEqual(self.store.saved_selection()["sourceId"], original)
        self.store.save_profile(profile, "renamed", "responses", "https://example.test/v1",
                                "m2", None, 16384)
        changed = self.store.saved_selection()
        self.assertEqual(changed["api"]["id"], profile)
        self.assertEqual(changed["profileId"], profile)
        self.assertNotEqual(changed["sourceId"], original)
        self.assertEqual(self.store.profile_source_id(profile), changed["sourceId"])
        self.store.save_profile(profile, "A", "responses", "https://example.test/v1",
                                "m1", None, 8192)
        self.assertEqual(self.store.saved_selection()["sourceId"], original)

    def test_migration_preserves_distinct_cache_ids_and_saves_encrypted_configuration_backup(self):
        current, prior = "a" * 32, "b" * 32
        document = {"version": 1, "sourceId": current, "selectedMode": "api",
                    "api": {"protocol": "responses", "baseUrl": "https://example.test/v1",
                            "model": "m1", "contextTokens": 8192, "encryptedKey": None},
                    "sourceIds": {_source_fingerprint("responses", "https://example.test/v1", "m1"): current,
                                  _source_fingerprint("responses", "https://example.test/v1", "m0"): prior}}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.store.save_profile(current, "A", "responses", "https://example.test/v1", "m0", None, 8192)
        self.assertEqual(self.store.saved_selection()["sourceId"], prior)
        backup = self.path.with_name(self.path.name + ".before-profiles-v2.bak")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), document)

    def test_write_failure_keeps_previous_profile_and_cleans_temporary(self):
        profile = self.profile()
        before = self.path.read_bytes()
        with patch("model_source.os.replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(ModelSourceUnavailable):
                self.store.save_profile(profile, "changed", "responses", "https://example.test/v1",
                                        "m2", None, 8192)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_interrupted_backup_does_not_block_a_later_migration(self):
        current = "c" * 32
        document = {"version": 1, "sourceId": current, "selectedMode": "api",
                    "api": {"protocol": "responses", "baseUrl": "https://example.test/v1",
                            "model": "m1", "contextTokens": 8192, "encryptedKey": None}}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        before = self.path.read_bytes()
        backup = self.path.with_name(self.path.name + ".before-profiles-v2.bak")
        with patch("model_source.os.fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(ModelSourceUnavailable):
                self.store.save_profile(current, "A", "responses", "https://example.test/v1",
                                        "m2", None, 8192)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(backup.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.store.save_profile(current, "A", "responses", "https://example.test/v1",
                                "m2", None, 8192)
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), document)

    def test_legacy_activation_keeps_the_source_id_already_activated_in_worker(self):
        source_id = "d" * 32
        self.assertEqual(self.store.save_api("responses", "https://example.test/v1",
                                            "m1", "synthetic", source_id=source_id), source_id)
        self.assertEqual(self.store.saved_selection()["sourceId"], source_id)


if __name__ == "__main__":
    unittest.main()
