"""Synthetic context tests: multi-shard import, append, cancellation, compression."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

from advisor_context import MAX_PENDING_WINDOWS, ContextProjection
from advisor_store import AdvisorStoreRoot
from test_advisor_support import FakeSource, wait_until


def _projection(root, source):
    stores = AdvisorStoreRoot(root)
    return stores, ContextProjection(stores, source)


def _wait_ready(projection, account, user):
    def ready():
        snapshot = projection.snapshot(account, user)
        return snapshot if snapshot["state"] == "ready" else None

    return wait_until(ready)


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = FakeSource()
        self.stores, self.projection = _projection(self.root, self.source)
        self.addCleanup(self.stores.close)
        self.addCleanup(self.projection.close)

    def test_full_import_covers_multi_shard_history(self):
        self.source.seed_pages("user-a", 600, shards=["message__message_1.db",
                                                     "message__message_2.db",
                                                     "message__message_3.db"])
        snapshot = self.projection.prepare("synthetic-account-1", "user-a")
        self.assertIn(snapshot["state"], ("reading", "ready"))
        ready = _wait_ready(self.projection, "synthetic-account-1", "user-a")
        self.assertIsNotNone(ready, "import did not become ready")
        self.assertEqual(ready["readCount"], 600)
        self.assertGreaterEqual(ready["revision"], 3)
        self.assertGreaterEqual(len(self.source.page_calls), 3)
        rows = self.projection.messages("synthetic-account-1", "user-a")
        self.assertEqual([row["seq"] for row in rows], list(range(1, 601)))
        self.assertEqual([row["msg_id"] for row in rows],
                         ["msg-message__message_%d.db-%d-%d" % (((seq - 1) % 3) + 1, seq, seq)
                          for seq in range(1, 601)])
        raw = json.loads(rows[0]["raw"])
        self.assertEqual(raw["_sort"], [1, "message__message_1.db", 1])
        self.assertEqual(rows[0]["text"], "消息内容 1 " + "x" * 16)

    def test_incremental_append_reads_only_new_messages(self):
        self.source.seed_pages("user-a", 300)
        self.projection.prepare("synthetic-account-1", "user-a")
        _wait_ready(self.projection, "synthetic-account-1", "user-a")
        first = self.projection.snapshot("synthetic-account-1", "user-a")
        before = [row["msg_id"] for row in self.projection.messages("synthetic-account-1", "user-a")]
        old_highwater = self.source.history_highwater("user-a")
        self.source.seed_pages("user-a", 5, start_seq=301, text_size=4)
        self.projection.prepare("synthetic-account-1", "user-a")
        ready = _wait_ready(self.projection, "synthetic-account-1", "user-a")
        self.assertEqual(ready["readCount"], 305)
        self.assertGreater(ready["revision"], first["revision"])
        after = [row["msg_id"] for row in self.projection.messages("synthetic-account-1", "user-a")]
        self.assertEqual(after[:300], before)
        self.assertEqual(len(after), 305)
        self.assertIn(old_highwater, [call["after"] for call in self.source.page_calls])

    def test_identity_switch_mid_import_saves_no_late_page(self):
        self.source.seed_pages("user-a", 600)
        other = "synthetic-account-2"

        def switch_on_second_page(source):
            if len(source.page_calls) == 2:
                source.account = other

        self.source.page_hook = switch_on_second_page
        self.projection.prepare("synthetic-account-1", "user-a")
        failed = wait_until(lambda: self.projection.snapshot("synthetic-account-1", "user-a")
                            if self.projection.snapshot("synthetic-account-1", "user-a")["state"]
                            == "error" else None)
        self.assertIsNotNone(failed, "identity switch was not detected")
        self.assertIn("账号", failed["error"])
        self.assertEqual(failed["readCount"], 256)
        rows = self.projection.messages("synthetic-account-1", "user-a")
        self.assertEqual(len(rows), 256)
        data_root = self.root / ".local" / "advisor-data"
        digests = sorted(path.name for path in data_root.iterdir())
        self.assertEqual(digests, [hashlib.sha256(b"synthetic-account-1").hexdigest()])
        self.assertFalse((data_root / hashlib.sha256(other.encode()).hexdigest()).exists())

    def test_cancel_before_commit_discards_the_read_page(self):
        self.source.seed_pages("user-a", 600)
        fired = threading.Event()

        def cancel_after_read(_source):
            if not fired.is_set():
                fired.set()
                entry = self.projection._imports[("synthetic-account-1", "user-a")]
                entry.cancel.set()

        self.source.page_hook = cancel_after_read
        self.projection.prepare("synthetic-account-1", "user-a")
        self.assertTrue(wait_until(fired.is_set))
        snapshot = self.projection.snapshot("synthetic-account-1", "user-a")
        self.assertEqual(snapshot["readCount"], 0)
        self.assertEqual(self.projection.messages("synthetic-account-1", "user-a"), [])

    def test_pending_windows_keep_recent_observations_and_rebuild_full_evicted_history(self):
        account = "synthetic-account-1"
        binding = self.source.capture_binding(messages=True)
        self.source.seed_pages("user-evicted", 20)
        self.source.seed_pages("user-recent", 4)

        def observe(user):
            window = sorted(self.source.users[user], key=lambda row: tuple(row["_sort"]))[-2:]
            self.assertTrue(self.projection.observe_window(account, binding.workdir, user, window, True, binding))

        observe("user-evicted")
        observe("user-recent")
        for index in range(MAX_PENDING_WINDOWS - 2):
            user = "user-window-%d" % index
            self.source.seed_pages(user, 1)
            observe(user)
        observe("user-recent")
        self.source.seed_pages("user-latest", 1)
        observe("user-latest")
        self.assertEqual(len(self.projection._pending_windows), MAX_PENDING_WINDOWS)
        self.assertNotIn((account, "user-evicted"), self.projection._pending_windows)
        self.assertIn((account, "user-recent"), self.projection._pending_windows)
        self.source.seed_pages("user-overflow", 1)
        observe("user-overflow")
        self.assertIn((account, "user-recent"), self.projection._pending_windows,
                      "refreshing an existing window must renew its retention priority")

        self.projection.prepare(account, "user-evicted")
        ready = _wait_ready(self.projection, account, "user-evicted")
        self.assertIsNotNone(ready)
        self.assertEqual(ready["readCount"], 20)
        rows = self.projection.messages(account, "user-evicted")
        self.assertEqual([row["text"] for row in rows], [row["text"] for row in self.source.users["user-evicted"]])
        context = self.projection.build_model_context(account, "user-evicted", None, 100000,
                                                      lambda *_args: self.fail("history should fit"), "synthetic-model")
        self.assertIn(rows[0]["text"], context)
        self.assertIn(rows[-1]["text"], context)

    def test_pending_eviction_keeps_a_running_import_until_its_complete_history_is_saved(self):
        account, user = "synthetic-account-1", "user-pinned"
        binding = self.source.capture_binding(messages=True)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.source.seed_pages(user, 30)

        def hold_import(_source):
            entered.set()
            if not release.wait(10):
                raise AssertionError("synthetic import gate timed out")

        self.source.page_hook = hold_import
        self.projection.prepare(account, user)
        self.assertTrue(entered.wait(3))
        self.projection.observe_window(account, binding.workdir, user, self.source.users[user][-2:], True, binding)
        for index in range(MAX_PENDING_WINDOWS + 8):
            other = "user-pending-%d" % index
            self.source.seed_pages(other, 1)
            self.projection.observe_window(account, binding.workdir, other, self.source.users[other], True, binding)
        self.assertIn((account, user), self.projection._pending_windows)
        self.assertLessEqual(len(self.projection._pending_windows), MAX_PENDING_WINDOWS)
        release.set()
        ready = _wait_ready(self.projection, account, user)
        self.assertIsNotNone(ready)
        self.assertEqual(ready["readCount"], 30)
        self.assertTrue(wait_until(lambda: (account, user) not in self.projection._imports))
        self.assertEqual(len(self.projection.messages(account, user)), 30)
        self.assertLessEqual(len(self.projection._pending_windows), MAX_PENDING_WINDOWS)


class CompressionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = FakeSource()
        self.stores, self.projection = _projection(self.root, self.source)
        self.addCleanup(self.stores.close)
        self.addCleanup(self.projection.close)
        self.source.seed_pages("user-a", 400, text_size=60)
        self.projection.prepare("synthetic-account-1", "user-a")
        wait_until(lambda: self.projection.snapshot("synthetic-account-1", "user-a")["state"]
                   == "ready")

    def test_over_budget_input_is_compacted_hierarchically_then_cached(self):
        calls = []

        def compactor(text, range_key, level):
            calls.append((range_key, level))
            return "概" * 700

        text = self.projection.build_model_context(
            "synthetic-account-1", "user-a", None, 4000, compactor, "fp-1")
        self.assertLessEqual(len(text), 4000)
        self.assertIn("[摘要]", text)
        levels = {level for _key, level in calls}
        self.assertIn(1, levels)
        self.assertTrue(any(level >= 2 for level in levels), "hierarchy must deepen when needed")
        self.assertGreater(len(calls), 1)
        self.assertEqual(len(calls), len({key for key, _level in calls}))
        before = len(calls)
        cached = self.projection.build_model_context(
            "synthetic-account-1", "user-a", None, 4000, compactor, "fp-1")
        self.assertEqual(len(calls), before, "cached summaries must be reused")
        self.assertEqual(cached, text)
        other_fp = self.projection.build_model_context(
            "synthetic-account-1", "user-a", None, 4000, compactor, "fp-2")
        self.assertGreater(len(calls), before, "a new model fingerprint re-summarizes")
        self.assertLessEqual(len(other_fp), 4000)

    def test_short_history_is_never_compacted(self):
        def compactor(text, range_key, level):  # pragma: no cover - must not run
            raise AssertionError("compaction must not run for a fitting history")

        text = self.projection.build_model_context(
            "synthetic-account-1", "user-a", 20, 100000, compactor, "fp-1")
        self.assertNotIn("[摘要]", text)
        self.assertIn("[最近聊天记录]", text)

    def test_concurrent_builders_share_single_flight_summaries(self):
        import time as _time
        calls = []
        lock = threading.Lock()

        def compactor(text, range_key, level):
            with lock:
                calls.append((range_key, level))
            _time.sleep(0.05)
            return "概" * 600

        outcomes = []

        def build():
            outcomes.append(self.projection.build_model_context(
                "synthetic-account-1", "user-a", None, 3000, compactor, "fp-9"))

        threads = [threading.Thread(target=build) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(calls), len({key for key, _level in calls}),
                         "a source range must be summarized once")
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0], outcomes[1])


if __name__ == "__main__":
    unittest.main()
