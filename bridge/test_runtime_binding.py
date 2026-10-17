"""Synthetic lifecycle regressions: no real WeChat, credentials or provider IO."""
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_api_insights as api_fixture
import test_real_backend as local_fixture
from backend_contracts import AccountChangedError
from wechat_source import WeChatSource


def install_binding(source, root):
    """Exercise the production binding API over two empty synthetic source roots."""
    roots = [Path(root) / name / source.account for name in ("origin-a", "origin-b")]
    for path in roots:
        (path / "db_storage").mkdir(parents=True)
    selected = [0]
    reader = WeChatSource(
        factory=lambda **kwargs: SimpleNamespace(account=source.account, workdir=source.workdir,
                                                  account_dir=roots[selected[0]]),
        active_account_locator=lambda: roots[selected[0]])
    source.lock = reader.lock
    for name in ("capture_binding", "assert_binding", "binding_scope", "binding_fingerprint",
                 "invalidate_binding"):
        setattr(source, name, getattr(reader, name))

    def switch(index, *, notify=True):
        with reader.lock:
            selected[0] = index
            if notify:
                reader.invalidate_binding()
    return reader, switch


class ApiBindingTests(unittest.TestCase):
    setUp = api_fixture.ApiInsightTests.setUp
    activate = api_fixture.ApiInsightTests.activate

    def bind(self):
        return install_binding(self.source, self.temp.name)

    def test_same_identity_root_change_rejects_late_result(self):
        self.activate()
        _reader, switch = self.bind()
        self.analyzer.entered, self.analyzer.release = threading.Event(), threading.Event()
        self.addCleanup(self.analyzer.release.set)
        self.backend.start_model_insights("account-a", "friend", 2)
        self.assertTrue(self.analyzer.entered.wait(2))
        switch(1, notify=False)  # External root change discovered by the fresh commit check.
        self.analyzer.release.set()
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        self.assertEqual(self.backend.model_insights("friend")["results"], {})

    def test_switch_away_and_back_still_cancels_old_job(self):
        self.activate()
        _reader, switch = self.bind()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.backend._run_model_insights
        def wait_before_worker(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        with patch.object(self.backend, "_run_model_insights", wait_before_worker):
            self.backend.start_model_insights("account-a", "friend", 2)
            self.assertTrue(entered.wait(2))
            switch(1)
            switch(0)
            release.set()
            self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        self.assertEqual(self.analyzer.calls, [])
        self.assertEqual(self.backend.model_insights("friend")["results"], {})

    def test_binding_is_captured_before_message_preload(self):
        self.activate()
        _reader, switch = self.bind()
        original = self.source.messages
        def changed_during_read(*args):
            rows = original(*args)
            switch(1)
            return rows
        with patch.object(self.source, "messages", changed_during_read):
            with self.assertRaises(AccountChangedError):
                self.backend.start_model_insights("account-a", "friend", 2)
        self.assertEqual(self.analyzer.calls, [])

    def test_reset_during_context_preload_never_calls_provider(self):
        selected = self.activate()
        self.bind()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        _account, _workdir, store = self.backend._scoped_identity()
        original = store.api_portrait_get
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        with patch.object(store, "api_portrait_get", delayed):
            self.backend.start_model_insights("account-a", "friend", 2)
            self.assertTrue(entered.wait(2))
            self.backend.analysis_scope_clear("account-a", "friend", selected["sourceId"], "conversation")
            release.set()
            self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        self.assertEqual(self.analyzer.calls, [])
        self.assertEqual(self.backend.model_insights("friend")["results"], {})

    def test_snapshot_reader_refresh_preserves_task_binding(self):
        self.activate()
        reader, _switch = self.bind()
        token = reader.capture_binding()
        fingerprint = reader.binding_fingerprint(token)
        with reader.lock:
            reader._release_db()
        self.assertEqual(reader.capture_binding(), token)
        self.backend.start_model_insights("account-a", "friend", 2)
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        self.assertEqual(len(self.backend.model_insights("friend")["results"]), 2)
        reader.invalidate_binding()
        replacement = reader.capture_binding()
        self.assertNotEqual(replacement, token)
        self.assertEqual(reader.binding_fingerprint(replacement), fingerprint)
        with self.assertRaises(AccountChangedError):
            with reader.binding_scope(token):
                self.fail("stale scope must fail at entry")

    def test_retry_wait_is_cancelled_on_source_switch(self):
        self.activate()
        _reader, switch = self.bind()
        with patch.object(self.analyzer, "model_insights", side_effect=RuntimeError("provider-busy")) as provider:
            with patch("backend_service.API_INSIGHT_RETRYABLE", {"provider-busy"}), \
                    patch("backend_service.API_INSIGHT_RETRY_SECONDS", 30):
                self.backend.start_model_insights("account-a", "friend", 2)
                with self.backend.api_condition:
                    self.assertTrue(self.backend.api_condition.wait_for(
                        lambda: any(job.get("retry") for job in self.backend.api_jobs.values()), timeout=3))
                switch(1)
                self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
            self.assertEqual(provider.call_count, 1)

    def test_portrait_available_preserves_saved_evidence_for_full_and_running_views(self):
        self.activate()
        self.backend.start_model_portrait("account-a", "friend")
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        account, _workdir, store = self.backend._scoped_identity()
        original = store.api_portrait_get
        def saved_with_evidence(*args, **kwargs):
            saved = original(*args, **kwargs)
            saved["available"]["mbtiEvidenceCount"] = 37
            return saved
        with patch.object(store, "api_portrait_get", saved_with_evidence):
            full = self.backend.model_portrait("friend")
            self.assertEqual(full["available"]["mbtiEvidenceCount"], 37)
            key = (account, "friend", self.backend.active_model_source_id, "friend")
            inventory = {"textCount": 3, "targetTextCount": 2}
            self.backend.api_portrait_jobs[key] = {"id": "synthetic", "status": "running",
                                                  "processed": 0, "total": 3, "_available": inventory}
            running = self.backend.model_portrait("friend")
            self.assertEqual(running["available"]["mbtiEvidenceCount"], 37)
            self.assertNotIn("mbtiEvidenceCount", inventory)

    def test_reset_while_waiting_for_pool_slot_never_calls_provider(self):
        selected = self.activate()
        self.bind()
        with self.backend.api_pool.lease():
            self.backend.start_model_insights("account-a", "friend", 2)
            with self.backend.api_pool.condition:
                self.assertTrue(self.backend.api_pool.condition.wait_for(
                    lambda: bool(self.backend.api_pool.waiters["shared"]), timeout=3))
            self.backend.analysis_scope_clear("account-a", "friend", selected["sourceId"], "conversation")
            self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        self.assertEqual(self.analyzer.calls, [])

    def test_api_force_has_own_completion_and_preserves_portrait_statistics(self):
        self.activate()
        self.backend.start_model_insights("account-a", "friend", 2)
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        ordinary = self.backend.model_insights("friend")["job"]["id"]
        self.backend.start_model_portrait("account-a", "friend")
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        _account, _workdir, store = self.backend._scoped_identity()
        from backend_contracts import api_portrait_scope
        source_id = self.backend.active_model_source_id
        before = store.api_portrait_get("account-a", "friend", api_portrait_scope(source_id), "friend")
        job = self.backend.start("friend", "message", 80, expected_account="account-a", message_id="o2")
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
        status = self.backend.analysis_job(job["id"])
        self.assertEqual(status["job"]["status"], "done")
        self.assertEqual(self.backend.model_insights("friend")["job"]["id"], ordinary)
        self.assertEqual(len(self.analyzer.calls), 2)
        self.assertEqual(self.analyzer.calls[-1][-1], ("o2",))
        self.assertEqual(store.api_portrait_get("account-a", "friend", api_portrait_scope(source_id), "friend"), before)

    def test_later_force_wins_when_an_older_job_finishes_last(self):
        self.activate()
        self.backend.api_pool.factory = lambda: self.analyzer
        self.backend.api_pool.configure(2)
        for first_is_force in (False, True):
            with self.subTest(first_is_force=first_is_force):
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                calls = []
                def provider(*args):
                    calls.append(args[-1])
                    ordinal = len(calls)
                    if ordinal == 1:
                        entered.set()
                        self.assertTrue(release.wait(3))
                    return {"insights": [{"id": target, "status": "ok", "emotion": "期待",
                                           "intent": "新结果" if ordinal == 2 else "旧结果"}
                                          for target in args[-1]]}
                # Distinct sessions give both subcases a fresh ordinary cache.
                user = "force-friend" if first_is_force else "ordinary-friend"
                with patch.object(self.analyzer, "model_insights", provider):
                    first = self.backend.start_model_insights("account-a", user, 2,
                                                              force=first_is_force)["job"]
                    self.assertTrue(entered.wait(2))
                    second = self.backend.start(user, "message", 80, expected_account="account-a",
                                                message_id="o2")
                    from time import monotonic, sleep
                    deadline = monotonic() + 3
                    while self.backend.analysis_job(second["id"])["job"]["status"] != "done":
                        self.assertLess(monotonic(), deadline)
                        sleep(.01)
                    release.set()
                    self.assertTrue(self.backend.api_tasks.wait_for_idle(3))
                saved = self.backend.model_insights(user)["results"]
                self.assertEqual(saved["o2"]["intents"], ["新结果"])
                self.assertEqual(saved["o1"]["intents"], ["旧结果"])
                self.assertEqual(self.backend.api_insight_writers, {})

    def test_guidance_get_returns_raw_validated_payload_from_real_store(self):
        self.activate()
        from guidance_contracts import guidance_scope, normalize_guidance
        from test_api_guidance import guidance
        _account, _workdir, store = self.backend._scoped_identity()
        payload = guidance()
        store.api_guidance_save("account-a", "friend",
                                guidance_scope(self.backend.active_model_source_id), "friend", payload)
        reply = self.backend.guidance("friend")
        self.assertEqual(reply["guidance"], payload)
        self.assertEqual(normalize_guidance(reply["guidance"]), payload)


class BatchBindingTests(unittest.TestCase):
    Source = local_fixture.QuotedBackfillTests.Source
    Analyzer = local_fixture.QuotedBackfillTests.Analyzer
    setUp = local_fixture.QuotedBackfillTests.setUp

    def test_late_batch_result_never_updates_statistics_after_root_change(self):
        self.addCleanup(self.backend.shutdown)
        self.source.add("friend", 1)
        _reader, switch = install_binding(self.source, self.source.workdir)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.analyzer.analyze_batch
        def delayed(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        with patch.object(self.analyzer, "analyze_batch", delayed):
            self.backend.start("friend", "incremental", None)
            self.assertTrue(entered.wait(2))
            switch(1, notify=False)
            release.set()
            self.backend.tasks.join()
        store = self.store_factory("account-a", self.source.workdir)
        saved = self.backend.batch_engine.snapshot("account-a", "friend", self.analyzer.version, store)
        self.assertEqual(saved["state"]["count"], 0)


class LocalBindingTests(unittest.TestCase):
    setUp = local_fixture.BackendTests.setUp

    def test_late_local_result_does_not_survive_root_change(self):
        self.addCleanup(self.backend.shutdown)
        self.source.add("friend", 1)
        _reader, switch = install_binding(self.source, self.source.workdir)
        self.analyzer.entered, self.analyzer.release = threading.Event(), threading.Event()
        self.addCleanup(self.analyzer.release.set)
        self.backend.start("friend", "recent", 1)
        self.assertTrue(self.analyzer.entered.wait(2))
        switch(1, notify=False)
        self.analyzer.release.set()
        self.backend.tasks.join()
        store = self.store_factory("account-a", self.source.workdir)
        self.assertEqual(store.ids("account-a", "friend", self.analyzer.version), set())

    def test_queued_local_job_cannot_rebind_after_switch(self):
        self.addCleanup(self.backend.shutdown)
        self.source.add("friend", 1)
        _reader, switch = install_binding(self.source, self.source.workdir)
        queued = []
        with patch.object(self.backend, "_enqueue", queued.append):
            self.backend.start("friend", "history", 1)
        switch(1)
        switch(0)
        self.backend._enqueue(queued[0])
        self.backend.tasks.join()
        self.assertEqual(self.analyzer.calls, [])


class SourceLeaseTests(unittest.TestCase):
    def test_automatic_and_pinned_readers_share_account_lease_and_close_releases_it(self):
        from account_leases import AccountLeaseRegistry
        from account_store import AccountConflict
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, _ = install_binding(api_fixture.Source(root), root / "first")
            second, _ = install_binding(api_fixture.Source(root), root / "second")
            first.account_leases = AccountLeaseRegistry(lease_root=root / "leases")
            second.account_leases = AccountLeaseRegistry(lease_root=root / "leases")
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            first.capture_binding()
            with self.assertRaises(AccountConflict):
                second.capture_binding()
            self.assertIsNone(second.db, "conflicting reader must not reach its factory")
            first.close()
            self.assertEqual(second.capture_binding().account, "account-a")
            second.account_leases.close()
            with self.assertRaises(AccountConflict):
                second.capture_binding()


if __name__ == "__main__":
    unittest.main()
