"""Credential replacement must retire old work without erasing same-model results.

All keys, messages, stores and provider calls below are synthetic.
"""
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import test_api_insights as fixture
from model_source import ModelSourceUnavailable
from test_api_guidance import guidance, others


class CredentialLifecycleTests(unittest.TestCase):
    setUp = fixture.ApiInsightTests.setUp
    activate = fixture.ApiInsightTests.activate

    def change(self, selected, key, *, legacy=False, name="Same model"):
        request = {"protocol": "responses", "baseUrl": "https://example.test/v1",
                   "model": "test-model", "contextTokens": 32768, "name": name}
        if key is not None:
            request["apiKey"] = key
        if legacy:
            return self.backend.model_source_activate({"mode": "api", **request})
        return self.backend.model_source_profile_save(
            {"profileId": selected["api"]["id"], **request})

    def assert_idle(self):
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))

    def replacement_case(self, *, legacy=False):
        selected = self.activate(key="synthetic-old-key")
        # Keep one existing result to distinguish cancellation from clearing history.
        self.backend.start_model_insights("account-a", "friend", 1, ["o1"])
        self.assert_idle()
        cached = self.backend.model_insights("friend")["results"]
        self.assertEqual(set(cached), {"o1"})
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls, cancellations = [], []
        original = self.analyzer.model_insights

        def provider(protocol, base_url, api_key, model, messages, target_ids):
            calls.append(api_key)
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("synthetic turn was not released")
                raise RuntimeError("timeout")
            return original(protocol, base_url, api_key, model, messages, target_ids)

        def cancel():
            cancellations.append(True)
            release.set()

        with patch.object(self.analyzer, "model_insights", provider), \
                patch.object(self.analyzer, "cancel", cancel, create=True), \
                patch("backend_service.API_INSIGHT_RETRY_SECONDS", 0):
            self.backend.start_model_insights("account-a", "friend", 1, ["o2"])
            self.assertTrue(entered.wait(2))
            changed = self.change(selected, "synthetic-new-key", legacy=legacy)
            release.set()
            self.assert_idle()
            self.assertEqual(calls, ["synthetic-old-key"],
                             "old turn must not retry with the replaced credential")
            self.assertEqual(cancellations, [True])
            self.assertEqual(changed["sourceId"], selected["sourceId"])
            self.assertEqual(self.backend.model_insights("friend")["results"], cached)
            self.backend.start_model_insights("account-a", "friend", 1, ["o2"])
            self.assert_idle()
            self.assertEqual(calls, ["synthetic-old-key", "synthetic-new-key"])
            self.assertEqual(set(self.backend.model_insights("friend")["results"]), {"o1", "o2"})
            self.assertEqual(self.backend.api_pool.status()["totalActive"], 0)

    def test_editing_active_key_stops_old_retry_and_preserves_cache(self):
        self.replacement_case()

    def test_legacy_activation_with_new_key_also_stops_old_retry(self):
        self.replacement_case(legacy=True)

    def test_guidance_replacement_stops_old_key_retry_and_new_turn_uses_new_key(self):
        selected = self.activate(key="synthetic-old-key")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def provider(*args):
            calls.append(args[2])
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("synthetic guidance was not released")
                raise RuntimeError("timeout")
            return {"guidance": guidance(
                scenario=args[6], analyzeSelf=args[7],
                subtexts=[{"id": identity, "status": "uncertain"} for identity in args[5]],
                advice={"forOthers": others(), "forSelf": None})}

        with patch.object(self.analyzer, "model_guidance", provider, create=True), \
                patch.object(self.analyzer, "cancel", release.set, create=True), \
                patch("backend_service.API_MODEL_RETRY_SECONDS", 0):
            self.backend.start_guidance("account-a", "friend", draft="合成要求")
            self.assertTrue(entered.wait(2))
            self.change(selected, "synthetic-new-key")
            release.set()
            self.assert_idle()
            self.assertEqual(calls, ["synthetic-old-key"])
            self.assertIsNone(self.backend.guidance("friend")["guidance"])
            self.backend.start_guidance("account-a", "friend", draft="合成要求")
            self.assert_idle()
            self.assertEqual(calls, ["synthetic-old-key", "synthetic-new-key"])
            self.assertEqual(self.backend.guidance("friend")["job"]["status"], "done")

    def test_changed_key_invalidates_all_registries_and_waiting_lanes(self):
        selected = self.activate(key="synthetic-old-key")
        pool = self.backend.api_pool
        source_id = selected["sourceId"]
        keys = [("account-a", "friend", source_id),
                ("account-a", "friend", source_id, "friend")]
        for registry, key in ((self.backend.api_jobs, keys[0]),
                              (self.backend.api_portrait_jobs, keys[1]),
                              (self.backend.api_guidance_jobs, keys[1])):
            registry[key] = {"id": "synthetic-job", "status": "running"}
        calls, errors, threads = [], [], []

        def wait(lane, checked):
            try:
                context = (pool.lease(validate=checked.set) if lane == "shared"
                           else pool.dedicated(lane, validate=checked.set))
                with context:
                    calls.append(lane)
            except RuntimeError as error:
                errors.append((lane, str(error)))

        with ExitStack() as held:
            held.enter_context(pool.lease())
            held.enter_context(pool.dedicated("portrait"))
            for lane in ("shared", "portrait"):
                checked = threading.Event()
                thread = threading.Thread(target=wait, args=(lane, checked), daemon=True)
                threads.append(thread)
                thread.start()
                self.assertTrue(checked.wait(2))
            self.change(selected, "synthetic-new-key")
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(calls, [])
        self.assertEqual(set(errors), {("shared", "model-source-changed"),
                                       ("portrait", "model-source-changed")})
        self.assertEqual(self.backend.api_jobs, {})
        self.assertEqual(self.backend.api_portrait_jobs, {})
        self.assertEqual(self.backend.api_guidance_jobs, {})
        with self.assertRaisesRegex(RuntimeError, "model-source-changed"):
            self.backend._assert_api_portrait_job(
                keys[1], {"id": "synthetic-job"}, None, self.backend.active_api_config)
        self.assertEqual((pool.status()["totalActive"], pool.status()["waiting"]), (0, 0))

    def test_same_key_and_name_only_edit_keep_running_work_and_ciphertext(self):
        selected = self.activate(key="synthetic-stable-key")
        # Real DPAPI is randomized; use a randomized fake wrapper to detect a
        # redundant re-encryption that would otherwise stop the Advisor too.
        serial = []
        def protect(key):
            serial.append(True)
            return (str(len(serial)) + ":" + key).encode()
        self.store.protect = protect
        self.store.unprotect = lambda blob: blob.decode().split(":", 1)[1]
        previous = self.store.profile(selected["api"]["id"])["encryptedKey"]
        key = ("account-a", "friend", selected["sourceId"])
        job = {"id": "keep", "status": "running"}
        self.backend.api_jobs[key] = job
        generation = self.backend.api_pool.generation
        for supplied in (None, "synthetic-stable-key"):
            for legacy in (False, True):
                with self.subTest(supplied=bool(supplied), legacy=legacy):
                    self.change(selected, supplied, legacy=legacy, name="Renamed only")
                    self.assertIs(self.backend.api_jobs.get(key), job)
                    self.assertEqual(self.backend.api_pool.generation, generation)
                    self.assertEqual(self.store.profile(selected["api"]["id"])["encryptedKey"], previous)

    def test_inactive_key_edit_does_not_cancel_active_work(self):
        first = self.activate(key="synthetic-first-key")
        second = self.activate(model="another-model", key="synthetic-other-key")
        key = ("account-a", "friend", second["sourceId"])
        job = {"id": "keep", "status": "running"}
        self.backend.api_jobs[key] = job
        generation = self.backend.api_pool.generation
        changed = self.change(first, "synthetic-replacement-key")
        self.assertEqual(changed["sourceId"], second["sourceId"])
        self.assertIs(self.backend.api_jobs.get(key), job)
        self.assertEqual(self.backend.api_pool.generation, generation)

    def test_failed_save_leaves_running_work_and_original_credential(self):
        selected = self.activate(key="synthetic-old-key")
        key = ("account-a", "friend", selected["sourceId"])
        job = {"id": "keep", "status": "running"}
        self.backend.api_jobs[key] = job
        generation = self.backend.api_pool.generation
        with patch.object(self.store, "save_profile", side_effect=ModelSourceUnavailable("synthetic failure")):
            with self.assertRaises(ModelSourceUnavailable):
                self.change(selected, "synthetic-new-key")
        self.assertIs(self.backend.api_jobs.get(key), job)
        self.assertEqual(self.backend.api_pool.generation, generation)
        self.assertEqual(self.backend._api_source_snapshot()[2], "synthetic-old-key")

    def test_validated_new_key_can_replace_an_unreadable_old_wrapper(self):
        selected = self.activate(key="synthetic-old-key")
        original = self.store.unprotect
        def unprotect(blob):
            if blob == b"wrapped:synthetic-old-key":
                raise ValueError("synthetic damaged wrapper")
            return original(blob)
        with patch.object(self.store, "unprotect", unprotect):
            changed = self.change(selected, "synthetic-new-key")
            self.assertEqual(changed["sourceId"], selected["sourceId"])
            self.assertEqual(self.backend._api_source_snapshot()[2], "synthetic-new-key")


if __name__ == "__main__":
    unittest.main()
