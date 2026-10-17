"""Acceptance source isolation checks. Every writable fixture is inside this repo."""
from pathlib import Path
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bridge"))
sys.path.insert(0, str(REPO / "tests" / "support"))

from chat_server import classify
from synthetic_client import (ACCOUNTS, MARKER, SyntheticDatabase, create_backend,
                              fixture_discovery, seed_fixtures, validate_root)
from account_api import AccountAPI
from account_pin_source import AccountPinSource
from backend_contracts import AccountChangedError, AccountUnavailableError
from backend_service import Backend
from wechat_source import WeChatSource
from history_browser import browse, search
from real_http import ThreadingHTTPServer, make_handler


class IdleAnalyzer:
    """No model results are manufactured; source tests must never infer."""
    model = {"state": "ready"}
    def analysis_version(self):
        return "fixture-no-inference"
    def analyze(self, *_args, **_kwargs):
        raise AssertionError("source acceptance must not infer")


class SyntheticClientTests(unittest.TestCase):
    def setUp(self):
        parent = REPO / ".local" / "implementation-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-client-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.origin = Path(self.temp.name)
        self.root = self.origin / ".local" / "test-client" / "client"
        self.root.mkdir(parents=True)
        (self.root / MARKER).write_text(json.dumps({"schema": 1, "mode": "synthetic",
                                                   "sourceRoot": str(self.origin)}), encoding="utf-8")
        self.patches = [
            patch("backend_service.ROOT", self.root),
            patch("result_store.ROOT", self.root),
            patch("wechat_source.active_account_dir", side_effect=AssertionError("real discovery called")),
            patch("live_source.live_account_selections", side_effect=AssertionError("real discovery called")),
            patch("live_source.active_account_snapshot", side_effect=AssertionError("real discovery called")),
            patch("live_source.selection_for_account", side_effect=AssertionError("real discovery called")),
            patch("live_source.discovery.find_weixin_processes", side_effect=AssertionError("process scan called")),
            patch("live_source.discovery.discover_account_dirs", side_effect=AssertionError("account scan called")),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        boundary = fixture_discovery(self.root)
        boundary.__enter__()
        self.addCleanup(boundary.__exit__, None, None, None)
        self.backend = create_backend(self.root, classify, analyzer=IdleAnalyzer())
        self.addCleanup(self.backend.shutdown)

    def test_ready_without_real_wechat_and_blank_api_profiles(self):
        health = self.backend.health()
        self.assertIs(type(self.backend), Backend)
        self.assertIs(type(self.backend.source), WeChatSource)
        self.assertIs(type(self.backend.account_pin_store), AccountPinSource)
        self.assertEqual(health["data"]["state"], "ready")
        self.assertEqual(self.backend.model_source_store.saved_selection()["profiles"], [])
        sessions = self.backend.source.sessions()
        self.assertEqual(sessions["account"], ACCOUNTS[0])
        self.assertEqual(len(sessions["sessions"]), 5)
        self.assertEqual(sum(item["isGroup"] for item in sessions["sessions"]), 1)
        self.assertTrue(all(not item["avatar"] for item in sessions["sessions"]))

    def test_first_open_selects_all_but_requests_no_analysis(self):
        for account in ACCOUNTS:
            selection = self.backend.selection_store.get(account)
            self.assertEqual(len(selection["selectedSessions"]), 5)
            self.assertEqual(selection["requestedSessions"], [])
        self.assertEqual(self.backend.tasks.unfinished_tasks, 0)

    def test_restart_preserves_removed_conversation_and_fixture_ids(self):
        source = self.backend.source
        before = source.messages("fixture-lin", 80)
        self.backend.selection_store.set_selected(ACCOUNTS[0], "fixture-lin", False)
        seed_fixtures(self.root)
        other = create_backend(self.root, classify, analyzer=IdleAnalyzer())
        try:
            self.assertNotIn("fixture-lin", other.selection_store.get(ACCOUNTS[0])["selectedSessions"])
            self.backend.shutdown()
            self.assertEqual([row["id"] for row in before],
                             [row["id"] for row in other.source.messages("fixture-lin", 80)])
        finally:
            other.shutdown()

    def test_long_target_counts_group_senders_and_quotes(self):
        source = self.backend.source
        self.assertEqual(source.target_text_totals(["fixture-qiao"])["fixture-qiao"], 120)
        self.assertEqual(source.stats("fixture-qiao")[:2], (240, 240))
        group = source.messages("fixture-study@chatroom", 80)
        self.assertEqual(len({row["senderId"] for row in group}), 5)
        quotes = source.messages("fixture-yan", 80)
        self.assertEqual(quotes[-1]["text"], "我没有说不去，只是需要晚一点。")
        self.assertTrue(all(row["kind"] == "text" for row in quotes))

    def test_batch_windows_and_history_pages_use_production_reader(self):
        source = self.backend.source
        windows = source.message_windows(["fixture-lin", "fixture-qiao"], 80,
                                         expected_account=ACCOUNTS[0])
        self.assertEqual(len(windows["fixture-qiao"]), 80)
        self.assertEqual(windows.verified_scope[0], ACCOUNTS[0])
        pages = []
        highwater, after = source.history_highwater("fixture-qiao"), None
        while True:
            rows, after = source.history_page("fixture-qiao", highwater, after, page_size=37)
            if not rows:
                break
            pages.extend(rows)
        self.assertEqual(len(pages), 240)
        self.assertEqual(len({row["id"] for row in pages}), 240)
        self.assertEqual(browse(source, ACCOUNTS[0], "fixture-lin")["messages"][-1]["text"],
                         "这个主意不错，我很期待。")
        found = search(source, ACCOUNTS[0], "fixture-yan", query="不是")
        self.assertTrue(found["messages"])

    def test_account_switch_changes_scope_and_keeps_separate_results(self):
        source = self.backend.source
        binding = source.capture_binding()
        self.backend.configure_account_pin(str(self.root / ".local/test-fixtures" / ACCOUNTS[1]))
        self.assertEqual(source.identity()[0], ACCOUNTS[1])
        with self.assertRaises(AccountChangedError):
            source.assert_binding(binding)
        self.assertIn("账号 B", source.messages("fixture-lin", 80)[0]["text"])
        self.assertNotEqual(self.backend.store_factory(ACCOUNTS[0], None).path,
                            self.backend.store_factory(ACCOUNTS[1], None).path)
        self.assertEqual(self.backend.clear_account_pin()["state"], "unpinned-ambiguous")
        with self.assertRaises(AccountUnavailableError):
            source.identity()
        self.backend.configure_account_pin(str(self.root / ".local/test-fixtures" / ACCOUNTS[0]))
        self.assertEqual(source.identity()[0], ACCOUNTS[0])

    def test_arbitrary_roots_accounts_and_media_are_rejected(self):
        for action in (lambda: self.backend.configure_data_root(str(REPO)),
                       lambda: self.backend.configure_account_pin(str(REPO))):
            with self.assertRaises(ValueError):
                action()
        self.assertIsNone(self.backend.source.media("fixture-lin", "any-id"))
        self.assertTrue(self.backend.clear_data_root()["synthetic"])
        with self.assertRaises(ValueError):
            SyntheticDatabase(self.root, "real-account")

    def test_account_pin_uses_real_config_and_conflict_checks(self):
        pin = self.backend.account_pin_store
        self.assertEqual(pin.config.name, "wechat-account-pin.json")
        self.assertEqual(json.loads(pin.config.read_text(encoding="utf-8")),
                         {"schema": 1, "accountDir": str(self.root / ".local/test-fixtures" / ACCOUNTS[0])})
        with patch("backend_service.conflicting_instance", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "另一个实例"):
                self.backend.configure_account_pin(str(self.root / ".local/test-fixtures" / ACCOUNTS[1]))
        self.assertEqual(self.backend.source.identity()[0], ACCOUNTS[0])

    def test_read_only_sqlite_factory_and_allowlist(self):
        db = SyntheticDatabase(self.root, ACCOUNTS[0])
        with self.assertRaises(ValueError):
            db._open("../../outside.db")
        conn = db._open("contact/contact.db")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM contact")
        finally:
            conn.close()
        self.assertEqual(db._msg_conns("unknown"), [])

    def test_marker_removal_and_corrupt_fixture_fail_closed(self):
        marker = self.root / MARKER
        raw = marker.read_text(encoding="utf-8")
        marker.unlink()
        with self.assertRaises(RuntimeError):
            seed_fixtures(self.root)
        marker.write_text(raw, encoding="utf-8")
        target = self.root / ".local" / "test-snapshots" / ACCOUNTS[1] / "message__message_0.db"
        target.write_bytes(b"invalid synthetic database")
        with self.assertRaises(sqlite3.DatabaseError):
            seed_fixtures(self.root)

    def test_marker_wrong_location_fails_closed(self):
        marker = self.root / MARKER
        marker.write_text(json.dumps({"schema": 1, "mode": "synthetic", "sourceRoot": str(REPO)}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            validate_root(self.root)

    def test_http_health_sessions_accounts_and_pin_do_not_discover_real_data(self):
        accounts = AccountAPI(self.backend, self.root / ".local" / "real-client-data",
                              snapshot_root=self.root / ".local" / "test-snapshots",
                              stable_keys_dir=self.root / ".local" / "test-keys",
                              runtime_dir=self.root / ".local" / "real-client-runtime")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.backend, accounts))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for route in ("health", "sessions", "accounts", "account-pin", "data-root", "conversation-selection"):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    connection.request("GET", "/api/" + route)
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, 200, (route, payload))
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
