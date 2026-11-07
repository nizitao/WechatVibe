"""Synthetic business checks for assistant-only model selection and real progress stages."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from advisor_contracts import AdvisorError
from advisor_service import AdvisorService
from model_source import ModelSourceStore
from test_advisor_support import FakeRuntime, FakeSource, wait_until


ACCOUNT, USER, AGENT = "synthetic-account-1", "synthetic-chat", "builtin:advisor"


class AssistantProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = FakeSource(account=ACCOUNT)
        self.source.seed_pages(USER, 5)
        self.source.users[USER][0]["text"] = "synthetic-private-conversation-marker"
        self.runtime = FakeRuntime()
        self.models = ModelSourceStore(self.root / "models.json", root=self.root,
                                       protect=lambda value: value.encode(), unprotect=lambda value: value.decode())
        self.profile = self.models.save_profile(None, "Synthetic assistant", "chat_completions",
                                               "http://127.0.0.1:9/v1", "synthetic-assistant-model",
                                               "synthetic-only-api-key", 32768)
        self.service = AdvisorService(self.source, self.models, self.root, runtime_factory=lambda: self.runtime)
        self.addCleanup(self.service.shutdown)

    def finish(self, started):
        result = wait_until(lambda: (value if value["run"]["state"] in {"done", "error", "stopped"} else None)
                            if (value := self.service.events(ACCOUNT, USER, started["run"]["id"])) else None)
        self.assertIsNotNone(result)
        return result

    def test_dedicated_profile_enables_send_without_activating_api_analysis(self):
        before = self.models.saved_selection()
        self.assertEqual(before["selectedMode"], "local")
        self.assertEqual(self.service.catalog()["engine"]["state"], "missing")
        self.models.select_assistant_profile(self.profile)
        self.assertEqual(self.service.catalog()["engine"]["state"], "available")
        started = self.service.start(ACCOUNT, USER, AGENT, None, "synthetic question", "req-dedicated")
        result = self.finish(started)
        self.assertEqual(result["run"]["state"], "done", result)
        config, request = self.runtime.respond_calls[0]
        self.assertEqual(config["model"], "synthetic-assistant-model")
        self.assertEqual(config["apiKey"], "synthetic-only-api-key")
        self.assertIn("synthetic-private-conversation-marker", request["contextFileText"])
        after = self.models.saved_selection()
        self.assertEqual(after["selectedMode"], "local")
        self.assertEqual(after["sourceId"], before["sourceId"])
        self.assertEqual(after["api"], before["api"])
        self.assertNotIn("synthetic-only-api-key", json.dumps(self.service.catalog()))

    def test_progress_is_ordered_and_never_contains_chat_prompt_or_credentials(self):
        self.models.select_assistant_profile(self.profile)
        started = self.service.start(ACCOUNT, USER, AGENT, None, "synthetic-private-user-question", "req-stages")
        result = self.finish(started)
        self.assertEqual(result["run"]["state"], "done")
        stages = [event for event in result["events"] if event["type"] == "status"]
        self.assertEqual([event["state"] for event in stages], ["reading", "preparing", "thinking"])
        self.assertEqual([event["text"] for event in stages],
                         ["正在读取会话资料", "已读取 5 条消息，正在整理上下文", "正在调用模型"])
        first_text = next(event["seq"] for event in result["events"] if event["type"] == "text")
        self.assertLess(stages[-1]["seq"], first_text)
        encoded = json.dumps(stages, ensure_ascii=False)
        for secret in ("synthetic-private-conversation-marker", "synthetic-private-user-question",
                       "synthetic-only-api-key", self.service.stores.config.agent(AGENT)["prompt"]):
            self.assertNotIn(secret, encoded)
        self.assertEqual([row["role"] for row in result["thread"]["messages"]], ["user", "assistant"])

    def test_keyless_loopback_chat_models_send_with_a_string_key_and_keep_local_analysis(self):
        for index, address in enumerate(("http://127.0.0.1:9/v1", "http://127.6.8.9:9/v1",
                                         "https://localhost/v1", "http://[::1]:9/v1")):
            with self.subTest(address=address):
                profile = self.models.save_profile(None, "Synthetic keyless", "chat_completions", address,
                                                   "synthetic-keyless-model", None, 32768)
                self.models.select_assistant_profile(profile)
                self.assertEqual(self.service.catalog()["engine"]["state"], "available")
                started = self.service.start(ACCOUNT, USER, AGENT, None, "keyless local question", "req-keyless-%d" % index)
                self.assertEqual(self.finish(started)["run"]["state"], "done")
                config = self.runtime.respond_calls[-1][0]
                self.assertEqual(config["apiKey"], "")
                self.assertEqual(config["baseUrl"], address)
                self.assertEqual(self.models.saved_selection()["selectedMode"], "local")

    def test_remote_or_lookalike_localhost_without_a_key_remains_unavailable(self):
        for index, address in enumerate(("https://models.example.invalid/v1", "https://localhost.example.invalid/v1",
                                         "https://127.0.0.1.example.invalid/v1", "https://192.168.1.3/v1")):
            with self.subTest(address=address):
                profile = self.models.save_profile(None, "Synthetic remote", "chat_completions", address,
                                                   "synthetic-no-key-model", None, 32768)
                self.models.select_assistant_profile(profile)
                self.assertEqual(self.service.catalog()["engine"]["state"], "missing")
                with self.assertRaises(AdvisorError):
                    self.service._resolve_config(ACCOUNT)
                started = self.service.start(ACCOUNT, USER, AGENT, None, "remote question", "req-remote-%d" % index)
                self.assertEqual(self.finish(started)["run"]["state"], "error")
        self.assertFalse(self.runtime.respond_calls)

    def test_keyless_ollama_retains_its_existing_support_and_never_serializes_a_null_key(self):
        profile = self.models.save_profile(None, "Synthetic Ollama", "ollama", "http://127.0.0.1:9",
                                           "synthetic-ollama-model", None, 32768)
        self.models.select_assistant_profile(profile)
        self.assertEqual(self.service.catalog()["engine"]["state"], "available")
        started = self.service.start(ACCOUNT, USER, AGENT, None, "ollama question", "req-keyless-ollama")
        self.assertEqual(self.finish(started)["run"]["state"], "done")
        self.assertEqual(self.runtime.respond_calls[0][0]["apiKey"], "")

    def test_stop_during_history_read_does_not_report_later_stages_or_call_model(self):
        self.models.select_assistant_profile(self.profile)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hold(_source):
            entered.set()
            if not release.wait(10):
                raise AssertionError("synthetic import gate timed out")

        self.source.page_hook = hold
        started = self.service.start(ACCOUNT, USER, AGENT, None, "synthetic question", "req-stop-reading")
        self.assertTrue(entered.wait(3))
        reading = self.service.events(ACCOUNT, USER, started["run"]["id"])
        self.assertEqual(reading["run"]["phase"], "reading")
        self.assertEqual([row["state"] for row in reading["events"] if row["type"] == "status"], ["reading"])
        self.service.stop(ACCOUNT, USER, started["run"]["id"])
        release.set()
        stopped = self.finish(started)
        self.assertEqual(stopped["run"]["state"], "stopped")
        self.assertFalse(self.runtime.respond_calls)
        self.assertFalse(self.runtime.compact_calls)
        self.assertNotIn("thinking", [row.get("state") for row in stopped["events"]])


if __name__ == "__main__":
    unittest.main()
