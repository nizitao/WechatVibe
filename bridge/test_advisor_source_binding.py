"""Source lifecycle regressions with temporary stores and synthetic inputs only."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from advisor_service import AdvisorService
from backend_contracts import AccountChangedError
from test_advisor_boundaries import ACCOUNT, USER, AGENT, DelayedRuntime, RetainedModelStore
from test_advisor_support import FakeSource, FakeRuntime, make_message, wait_until


class AdvisorSourceBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = FakeSource()
        self.source.seed_pages(USER, 3)
        self.runtime = FakeRuntime()
        self.models = RetainedModelStore()
        self.service = AdvisorService(self.source, self.models, Path(temporary.name),
                                      runtime_factory=lambda: self.runtime)
        self.addCleanup(self.service.shutdown)

    def start(self, request="binding", thread=None):
        result = self.service.start(ACCOUNT, USER, AGENT, thread, "synthetic question", request)
        return self.service._runs[result["run"]["id"]]

    def joined(self, run):
        run.worker.join(3)
        self.assertFalse(run.worker.is_alive(), "synthetic run did not settle")
        return run

    def test_late_decoded_window_cannot_be_relabelled_with_new_root(self):
        binding = self.source.capture_binding()
        rows = [make_message(1, text="old source private fixture")]
        self.source.switch_root("C:/synthetic-root-b")
        self.assertFalse(self.service.observe_message_window(
            ACCOUNT, self.source.workdir, USER, rows, False, binding=binding))
        self.assertEqual(self.service.context.messages(ACCOUNT, USER), [])
        self.assertFalse(self.service.observe_message_window(
            ACCOUNT, self.source.workdir, USER, rows, False))

    def test_same_account_and_workdir_new_root_rebuilds_context_and_native_session(self):
        first = self.joined(self.start("first"))
        self.assertEqual(first.state, "done")
        old_epoch = first.context_epoch
        self.source.switch_root("C:/synthetic-root-b")
        self.source.users[USER] = [make_message(1, text="new root only")]
        second = self.joined(self.start("second", first.thread_id))
        self.assertEqual(second.state, "done")
        self.assertNotEqual(second.context_epoch, old_epoch)
        request = self.runtime.respond_calls[-1][1]
        self.assertIn("new root only", request["contextFileText"])
        self.assertNotIn("消息内容", request["contextFileText"])
        self.assertIsNone(request["runtimeSessionId"])

    def test_queued_run_keeps_prequeue_binding_through_a_b_a_switch(self):
        entered, release = threading.Event(), threading.Event()
        original = self.service._run_worker

        def queued(run, agent):
            entered.set()
            release.wait(3)
            original(run, agent)

        with patch.object(self.service, "_run_worker", side_effect=queued):
            run = self.start()
            try:
                self.assertTrue(entered.wait(2))
                before = run.database.thread_messages(run.thread_id)
                original_root = self.source.source_root
                self.source.switch_root("C:/synthetic-root-b")
                self.source.switch_root(original_root)
            finally:
                release.set()
            self.joined(run)
        self.assertEqual(run.state, "stopped")
        self.assertEqual(run.database.thread_messages(run.thread_id), before)
        self.assertEqual(self.runtime.respond_calls, [])
        self.assertEqual(self.runtime.compact_calls, [])

    def test_root_switch_during_reply_cancels_without_late_or_partial_commit(self):
        self.runtime = DelayedRuntime()
        run = self.start()
        try:
            self.assertTrue(self.runtime.entered.wait(2))
            before = run.database.thread_messages(run.thread_id)
            self.service._on_event(run, {"type": "text", "text": "partial old source"})
            self.source.switch_root("C:/synthetic-root-b")
            self.service.cancel_stale_runs()
            self.assertEqual(run.state, "stopped")
        finally:
            self.runtime.release.set()
        self.joined(run)
        self.assertEqual(run.database.thread_messages(run.thread_id), before)

    def test_no_callback_reply_still_checks_source_at_final_commit(self):
        def respond(*_args):
            self.source.switch_root("C:/synthetic-root-b")
            return {"text": "late synthetic reply", "runtimeSessionId": "late"}

        self.runtime.respond = respond
        run = self.joined(self.start())
        self.assertEqual(run.state, "stopped")
        self.assertEqual([row["role"] for row in run.database.thread_messages(run.thread_id)], ["user"])

    def test_stop_after_source_switch_does_not_save_buffered_old_answer(self):
        self.runtime = DelayedRuntime()
        run = self.start()
        try:
            self.assertTrue(self.runtime.entered.wait(2))
            before = run.database.thread_messages(run.thread_id)
            self.service._on_event(run, {"type": "text", "text": "partial old source"})
            self.source.switch_root("C:/synthetic-root-b")
            self.service.stop(ACCOUNT, USER, run.id)
        finally:
            self.runtime.release.set()
        self.joined(run)
        self.assertEqual(run.state, "stopped")
        self.assertEqual(run.database.thread_messages(run.thread_id), before)

    def test_changed_root_in_highwater_read_never_starts_import(self):
        original = self.source.history_highwater

        def changed(user):
            highwater = original(user)
            self.source.switch_root("C:/synthetic-root-b")
            return highwater

        self.source.history_highwater = changed
        with self.assertRaises(AccountChangedError):
            self.service.context.prepare(ACCOUNT, USER)
        self.assertEqual(self.source.page_calls, [])
        self.assertEqual(self.service.context.messages(ACCOUNT, USER), [])

    def test_second_import_page_from_another_root_is_not_saved(self):
        self.source.users[USER] = []
        self.source.seed_pages(USER, 600)

        def switch(source):
            if len(source.page_calls) == 2:
                source.switch_root("C:/synthetic-root-b")

        self.source.page_hook = switch
        self.service.context.prepare(ACCOUNT, USER)
        self.assertTrue(wait_until(lambda: self.service.context.snapshot(ACCOUNT, USER)["state"] == "error"))
        self.assertEqual(len(self.service.context.messages(ACCOUNT, USER)), 256)

    def test_root_switch_during_compaction_saves_neither_summary_nor_reply(self):
        self.source.users[USER] = []
        self.source.seed_pages(USER, 100, text_size=100)
        self.models.context_tokens = 8192
        entered, release = threading.Event(), threading.Event()

        def compact(*_args):
            entered.set()
            release.wait(3)
            return {"text": "late synthetic summary"}

        self.runtime.compact = compact
        run = self.start()
        try:
            self.assertTrue(entered.wait(2))
            self.source.switch_root("C:/synthetic-root-b")
        finally:
            release.set()
        self.joined(run)
        self.assertEqual(run.state, "stopped")
        self.assertEqual(run.database._rows("SELECT * FROM summaries"), [])
        self.assertEqual(self.runtime.respond_calls, [])

    def test_same_source_new_messages_do_not_invalidate_inflight_reply(self):
        self.runtime = DelayedRuntime()
        run = self.start()
        try:
            self.assertTrue(self.runtime.entered.wait(2))
            self.source.add_messages(USER, [make_message(4, text="new same-source message")])
            self.service.cancel_stale_runs()
            self.assertEqual(run.state, "running")
        finally:
            self.runtime.release.set()
        self.joined(run)
        self.assertEqual(run.state, "done")

    def test_final_source_validation_and_save_exclude_internal_source_switch(self):
        self.runtime = DelayedRuntime()
        run = self.start()
        acquired = []
        try:
            self.assertTrue(self.runtime.entered.wait(2))
            original = run.database.finish_run

            def save(*args, **kwargs):
                def compete():
                    held = self.source.lock.acquire(timeout=0.05)
                    acquired.append(held)
                    if held:
                        self.source.lock.release()
                other = threading.Thread(target=compete)
                other.start()
                other.join(1)
                self.assertFalse(other.is_alive())
                return original(*args, **kwargs)

            with patch.object(run.database, "finish_run", side_effect=save):
                self.runtime.release.set()
                self.joined(run)
        finally:
            self.runtime.release.set()
        self.assertEqual(run.state, "done")
        self.assertEqual(acquired, [False])


if __name__ == "__main__":
    unittest.main()
