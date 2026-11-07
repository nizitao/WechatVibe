"""Assistant-bound reply choices over synthetic chat and a local Node IO double."""
import copy
import http.client
import json
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from guidance_contracts import REPLY_OPTIONS_REVISION
from backend_contracts import api_insight_scope
from model_source import LOCAL_SOURCE_ID, ModelSourceUnavailable
from node_analysis import NodeAnalysis
from test_api_guidance import reply_options
from test_advisor_support import FakeRuntime
import test_guidance_lifecycle as lifecycle_fixture


class ReplyAssistantTests(unittest.TestCase):
    activate = lifecycle_fixture.GuidanceLifecycleTests.activate
    idle = lifecycle_fixture.GuidanceLifecycleTests.idle

    def setUp(self):
        lifecycle_fixture.GuidanceLifecycleTests.setUp(self)
        lifecycle_fixture.GuidanceLifecycleTests.ready(self)
        self.requests = []
        self.snapshots = []
        self.provider_context = threading.local()
        self.request_condition = threading.Condition()
        self.gates = {}
        self.fail_once = set()
        self.backend.api_analyzer = self.make_node()
        self.backend.api_pool.factory = self.make_node
        self.backend.api_pool.settings_path = Path(self.temp.name) / "api-workers.json"
        self.backend.api_pool.configure(2)
        self.agent_a = self.make_agent("A", "Use short direct replies.", "A_SKILL_ONLY")
        self.agent_b = self.make_agent("B", "Use warm playful replies.", "B_SKILL_ONLY")
        self.addCleanup(self.finish_requests)

    def make_node(self):
        node = NodeAnalysis(settings_path=Path(self.temp.name) / "inference.json", api_only=True)
        original = node.model_guidance

        def generate(*args, **kwargs):
            self.provider_context.assistant = kwargs.get("assistant", args[14] if len(args) > 14 else None)
            return original(*args, **kwargs)

        guidance_guard = patch.object(node, "model_guidance", side_effect=generate)
        guidance_guard.start()
        self.addCleanup(guidance_guard.stop)
        guard = patch.object(node, "_request", side_effect=self.provider)
        guard.start()
        self.addCleanup(guard.stop)
        return node

    def make_agent(self, name, prompt, skill_content):
        config = self.advisor.stores.config
        skill = config.import_skill(name + " skill", "Synthetic", skill_content)
        return config.save_agent({"name": name, "description": "Synthetic", "prompt": prompt,
                                  "skillIds": [skill["id"]]})

    @staticmethod
    def args(agent):
        return {"reply_options": True, "agent_id": agent["id"],
                "agent_revision": agent["revision"]}

    @staticmethod
    def payload(agent):
        return {"account": "account-a", "user": "friend", "replyOptions": True,
                "agentId": agent["id"], "agentRevision": agent["revision"]}

    def start(self, agent):
        return self.backend.start_guidance("account-a", "friend", **self.args(agent))

    def saved(self, agent):
        return self.backend.guidance("friend", **self.args(agent))

    def release_all(self):
        for gate in self.gates.values():
            gate.set()

    def finish_requests(self):
        self.release_all()
        self.backend.shutdown()

    def block(self, agent):
        gate = self.gates[agent["id"]] = threading.Event()
        return gate

    def wait_requests(self, count):
        with self.request_condition:
            self.assertTrue(self.request_condition.wait_for(
                lambda: len(self.requests) >= count, timeout=3), "provider did not receive the request")

    def provider(self, payload, **_kwargs):
        snapshot = getattr(self.provider_context, "assistant", None)
        with self.request_condition:
            self.requests.append(copy.deepcopy(payload))
            self.snapshots.append(copy.deepcopy(snapshot))
            self.request_condition.notify_all()
        assistant = payload.get("assistant")
        if not isinstance(assistant, dict):
            raise AssertionError("reply generation omitted its selected assistant")
        if set(assistant) != {"prompt", "skills"}:
            raise AssertionError("local assistant cache identity must not be sent to the model")
        agent_id = snapshot["agentId"]
        gate = self.gates.get(agent_id)
        if gate is not None and not gate.wait(4):
            raise AssertionError("synthetic provider was not released")
        if agent_id in self.fail_once:
            self.fail_once.remove(agent_id)
            raise RuntimeError("network")
        text = "A" if agent_id == self.agent_a["id"] else "B"
        value = reply_options(replies=[{"text": text + " first"}, {"text": text + " second"},
                                       {"text": text + " third"}])
        return {"guidanceVersion": REPLY_OPTIONS_REVISION, **value}, None

    def mutate(self, agent, action):
        config = self.advisor.stores.config
        if action == "delete":
            config.delete_agent(agent["id"])
            return None
        if action == "disable":
            return config.set_enabled(agent["id"], False)
        return config.save_agent({"id": agent["id"], "name": agent["name"],
                                  "description": agent["description"],
                                  "prompt": agent["prompt"] + " Changed.",
                                  "skillIds": agent["skillIds"]})

    def test_each_assistant_reaches_node_with_its_own_prompt_skills_and_saved_choices(self):
        for agent in (self.agent_a, self.agent_b):
            started = self.start(agent)
            self.assertEqual(started["agentId"], agent["id"])
            self.assertEqual(started["agentRevision"], agent["revision"])
            self.idle()
        self.assertEqual(len(self.requests), 2)
        for request, snapshot, agent, marker, other_marker in zip(
                self.requests, self.snapshots, (self.agent_a, self.agent_b),
                ("A_SKILL_ONLY", "B_SKILL_ONLY"), ("B_SKILL_ONLY", "A_SKILL_ONLY")):
            profile = request["assistant"]
            self.assertEqual(snapshot["agentId"], agent["id"])
            self.assertEqual(snapshot["agentRevision"], agent["revision"])
            self.assertEqual(profile["prompt"], agent["prompt"])
            self.assertIn(marker, json.dumps(profile["skills"]))
            self.assertNotIn(other_marker, json.dumps(profile))
            self.assertEqual([row["id"] for row in request["messages"]], ["s1", "o1", "o2"])
        for agent, prefix in ((self.agent_a, "A"), (self.agent_b, "B")):
            result = self.saved(agent)
            self.assertEqual(result["agentId"], agent["id"])
            self.assertEqual(result["agentRevision"], agent["revision"])
            self.assertEqual(result["job"]["status"], "done")
            self.assertEqual([row["text"] for row in result["guidance"]["replies"]],
                             [prefix + " first", prefix + " second", prefix + " third"])
        _, _, store = self.backend._scoped_identity()
        with store.connect() as connection:
            rows = connection.execute("SELECT source_id FROM api_guidance_v1").fetchall()
        self.assertEqual(len(rows), 2, "assistants must retain separate persistent results")
        self.assertNotEqual(rows[0][0], rows[1][0])

    def save_independent_assistant_profile(self):
        # Same endpoint, different credential: endpoint-only key reuse would select the wrong key.
        saved = self.backend.model_source_profile_save({
            "name": "Synthetic assistant B", "protocol": "responses",
            "baseUrl": "https://example.test/v1", "model": "assistant-model-b",
            "apiKey": "synthetic-assistant-key-b", "contextTokens": 32768})
        return saved["profile"]

    def test_independent_assistant_model_does_not_replace_the_reply_provider(self):
        helper = self.save_independent_assistant_profile()
        before = (self.backend.active_model_profile_id, self.backend.active_model_source_id,
                  self.backend.model_source_revision)
        self.backend.model_source_select({"profileId": helper})
        assistant_config = self.advisor._resolve_config("account-a")
        self.assertEqual(assistant_config["model"], "assistant-model-b")
        self.assertEqual(assistant_config["apiKey"], "synthetic-assistant-key-b")
        self.start(self.agent_a)
        self.idle()
        request = self.requests[-1]
        self.assertEqual((request["protocol"], request["baseUrl"], request["model"], request["apiKey"]),
                         ("responses", "https://example.test/v1", "test-model", "test-key"))
        self.assertEqual(request["assistant"]["prompt"], self.agent_a["prompt"])
        self.assertIn("A_SKILL_ONLY", json.dumps(request["assistant"]["skills"]))
        self.assertNotIn("B_SKILL_ONLY", json.dumps(request["assistant"]["skills"]))
        self.assertEqual((self.backend.active_model_profile_id, self.backend.active_model_source_id,
                          self.backend.model_source_revision), before)
        self.assertEqual(self.saved(self.agent_a)["job"]["status"], "done")

    def test_local_analysis_keeps_independent_assistant_available_without_enabling_replies(self):
        helper = self.save_independent_assistant_profile()
        self.backend.model_source_select({"profileId": helper})
        self.backend.model_source_activate({"mode": "local"})
        runtime = FakeRuntime(text="Synthetic independent assistant answer.")
        self.advisor.runtime_factory = lambda: runtime
        started = self.advisor.start("account-a", "friend", self.agent_a["id"], None,
                                     "Synthetic advice request")
        run = self.advisor._runs[started["run"]["id"]]
        run.worker.join(3)
        self.assertFalse(run.worker.is_alive(), "synthetic assistant did not finish")
        self.assertEqual(run.state, "done", run.error)
        config, request = runtime.respond_calls[0]
        self.assertEqual((config["model"], config["apiKey"]),
                         ("assistant-model-b", "synthetic-assistant-key-b"))
        self.assertIn(self.agent_a["prompt"], request["system"])
        self.assertIn("A_SKILL_ONLY", json.dumps(request["skills"]))
        with self.assertRaises(ModelSourceUnavailable):
            self.start(self.agent_a)
        self.assertEqual(self.requests, [])
        self.assertEqual((self.backend.active_model_source_mode, self.backend.active_model_source_id),
                         ("local", LOCAL_SOURCE_ID))
        self.assertEqual(self.backend.model_insights("friend")["results"], {})

    def test_assistant_model_switch_does_not_cancel_replies_or_mix_label_caches(self):
        helper = self.save_independent_assistant_profile()
        helper_source = self.store.profile_source_id(helper)
        _, _, store = self.backend._scoped_identity()
        message = self.source.rows[1]
        original = {"id": message["id"], "status": "ok", "affect": {"feeling": "期待"},
                    "intents": ["邀约"]}
        other = {"id": message["id"], "status": "ok", "affect": {"feeling": "无奈"},
                 "intents": ["婉拒"]}
        store.save_api_insights("account-a", "friend", api_insight_scope(self.selection["sourceId"]),
                                [(message, original)])
        store.save_api_insights("account-a", "friend", api_insight_scope(helper_source),
                                [(message, other)])
        before = self.backend.model_insights("friend")["results"]
        self.assertEqual(before[message["id"]], original)
        gate = self.block(self.agent_a)
        started = self.start(self.agent_a)
        self.wait_requests(1)
        revision = self.backend.model_source_revision
        self.backend.model_source_select({"profileId": helper})
        self.assertEqual(self.backend.model_source_revision, revision)
        self.assertEqual(self.backend.model_insights("friend")["results"], before)
        gate.set()
        self.idle()
        saved = self.saved(self.agent_a)
        self.assertEqual(saved["job"]["id"], started["job"]["id"])
        self.assertEqual(saved["job"]["status"], "done")
        self.assertEqual((self.requests[-1]["model"], self.requests[-1]["apiKey"]),
                         ("test-model", "test-key"))
        self.assertEqual(self.backend.model_insights("friend")["results"], before)

    def test_two_assistants_can_generate_concurrently_and_same_assistant_deduplicates(self):
        gate_a, gate_b = self.block(self.agent_a), self.block(self.agent_b)
        first = self.start(self.agent_a)
        self.wait_requests(1)
        again = self.start(self.agent_a)
        self.assertEqual(first["job"]["id"], again["job"]["id"])
        second = self.start(self.agent_b)
        self.wait_requests(2)
        self.assertNotEqual(first["job"]["id"], second["job"]["id"])
        gate_b.set()
        gate_a.set()
        self.idle()
        self.assertEqual(self.saved(self.agent_a)["guidance"]["replies"][0]["text"], "A first")
        self.assertEqual(self.saved(self.agent_b)["guidance"]["replies"][0]["text"], "B first")

    def test_changed_disabled_and_deleted_templates_discard_inflight_output(self):
        for action in ("edit", "disable", "delete"):
            with self.subTest(action=action):
                agent = self.make_agent(action, "Synthetic " + action, "Skill " + action)
                gate = self.block(agent)
                before = len(self.requests)
                self.start(agent)
                self.wait_requests(before + 1)
                with self.request_condition:
                    self.assertTrue(self.request_condition.wait_for(lambda: any(
                        row["agentId"] == agent["id"] for row in self.snapshots), timeout=3))
                changed = self.mutate(agent, action)
                gate.set()
                self.idle()
                _, _, store = self.backend._scoped_identity()
                with store.connect() as connection:
                    count = connection.execute("SELECT COUNT(*) FROM api_guidance_v1").fetchone()[0]
                self.assertEqual(count, 0, "stale template result was persisted")
                if changed and action == "edit":
                    self.assertIsNone(self.saved(changed)["guidance"])

    def test_template_change_after_retryable_failure_prevents_another_provider_call(self):
        for action in ("edit", "disable", "delete"):
            with self.subTest(action=action):
                agent = self.make_agent("retry " + action, "Synthetic retry", "Retry skill " + action)
                gate = self.block(agent)
                self.fail_once.add(agent["id"])
                before = len(self.requests)
                self.start(agent)
                self.wait_requests(before + 1)
                self.mutate(agent, action)
                gate.set()
                with patch("backend_service.API_MODEL_RETRY_SECONDS", 0.01):
                    self.idle()
                self.assertEqual(len(self.requests), before + 1)
                _, _, store = self.backend._scoped_identity()
                with store.connect() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM api_guidance_v1").fetchone()[0], 0)

    def test_template_edit_while_waiting_for_worker_never_reaches_provider(self):
        self.backend.api_pool.configure(1)
        with self.backend.api_pool.lease():
            self.start(self.agent_a)
            with self.backend.api_pool.condition:
                self.assertTrue(self.backend.api_pool.condition.wait_for(
                    lambda: bool(self.backend.api_pool.waiters["shared"]), timeout=3))
            updated = self.mutate(self.agent_a, "edit")
            self.idle()
        self.assertEqual(self.requests, [])
        self.assertIsNone(self.saved(updated)["guidance"])

    def test_template_edit_during_retry_countdown_never_calls_provider_again(self):
        entered, proceed = threading.Event(), threading.Event()
        self.addCleanup(proceed.set)
        self.fail_once.add(self.agent_a["id"])
        original = self.backend._wait_api_model_retry

        def countdown(*args, **kwargs):
            entered.set()
            if not proceed.wait(3):
                raise AssertionError("retry countdown was not released")
            return original(*args, **kwargs)

        with patch.object(self.backend, "_wait_api_model_retry", countdown), \
                patch("backend_service.API_MODEL_RETRY_SECONDS", 0.01):
            self.start(self.agent_a)
            self.assertTrue(entered.wait(3), "the initial error never reached retry countdown")
            updated = self.mutate(self.agent_a, "edit")
            proceed.set()
            self.idle()
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.saved(updated)["guidance"])

    def test_candidate_commit_acquires_source_before_config_and_holds_both_during_save(self):
        events = []
        local = threading.local()
        _, _, store = self.backend._scoped_identity()
        config = self.advisor.stores.config
        original_io = self.backend.api_analyzer._request
        original_save = store.api_guidance_save
        case = self

        class ObservedLock:
            def __init__(self, lock, name):
                self.lock, self.name = lock, name

            def __enter__(self):
                if not self.lock.acquire(timeout=1):
                    raise AssertionError("bounded lock acquisition timed out: " + self.name)
                if getattr(local, "commit", False):
                    events.append(self.name)
                    counts = getattr(local, "held", {})
                    counts[self.name] = counts.get(self.name, 0) + 1
                    local.held = counts
                return self

            def __exit__(self, *_args):
                if getattr(local, "commit", False):
                    local.held[self.name] -= 1
                self.lock.release()

            def __getattr__(self, name):
                return getattr(self.lock, name)

        def generated(*args, **kwargs):
            result = original_io(*args, **kwargs)
            # Start observing after the provider result, isolating the final commit.
            local.commit = True
            local.held = {}
            return result

        def saved_with_locks(*args, **kwargs):
            case.assertGreater(local.held.get("source", 0), 0)
            case.assertGreater(local.held.get("config", 0), 0)
            return original_save(*args, **kwargs)

        with patch.object(self.source, "lock", ObservedLock(self.source.lock, "source")), \
                patch.object(config, "lock", ObservedLock(config.lock, "config")), \
                patch.object(self.backend.api_analyzer, "_request", side_effect=generated), \
                patch.object(store, "api_guidance_save", side_effect=saved_with_locks) as save:
            self.start(self.agent_a)
            self.idle()
            self.assertEqual(save.call_count, 1)
        self.assertEqual(events[:2], ["source", "config"],
                         "candidate commit inverted the source -> config order used by assistant start")
        self.assertEqual(self.saved(self.agent_a)["job"]["status"], "done")

    def test_edit_invalidates_same_id_cache_and_explicit_regeneration_is_fresh(self):
        self.start(self.agent_a)
        self.idle()
        previous_id = self.saved(self.agent_a)["job"]["id"]
        updated = self.mutate(self.agent_a, "edit")
        self.assertEqual(updated["id"], self.agent_a["id"])
        self.assertIsNone(self.saved(updated)["guidance"])
        current = self.start(updated)
        self.idle()
        self.assertNotEqual(previous_id, current["job"]["id"])
        self.assertEqual(self.requests[-1]["assistant"]["prompt"], updated["prompt"])
        self.assertEqual(len(self.saved(updated)["guidance"]["replies"]), 3)

    def test_old_unbound_v1_cache_cannot_be_loaded_as_current_assistant(self):
        _, _, store = self.backend._scoped_identity()
        old = reply_options(version="api-reply-options-v1")
        source = self.selection["sourceId"] + ":api-reply-options-v1"
        with store.connect() as connection:
            connection.execute("INSERT INTO api_guidance_v1 "
                "(account,session,source_id,subject,revision,scenario,analyze_self,payload_json,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)", ("account-a", "friend", source, "friend",
                "api-reply-options-v1", "auto", 0, store.cipher.dumps(old), 1))
        self.assertIsNone(self.saved(self.agent_a)["guidance"])
        self.assertIsNone(self.saved(self.agent_b)["guidance"])
        self.assertEqual(self.requests, [])

    def test_http_missing_disabled_and_stale_assistant_reject_before_reading_chat(self):
        from http.server import ThreadingHTTPServer
        from real_http import make_handler
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.backend))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        headers = {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}"}
        updated = self.mutate(self.agent_a, "edit")
        self.mutate(self.agent_b, "disable")
        cases = [{"account": "account-a", "user": "friend", "replyOptions": True},
                 self.payload(self.agent_a), self.payload(self.agent_b),
                 self.payload(updated) | {"agentRevision": True}]
        try:
            with patch.object(self.source, "messages", wraps=self.source.messages) as read:
                for payload in cases:
                    connection.request("POST", "/api/model-guidance", json.dumps(payload), headers)
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    self.assertIn(response.status, (400, 409), body)
                self.assertEqual(read.call_count, 0)
            self.assertEqual(self.requests, [])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_http_get_reads_only_requested_assistant_without_provider_call(self):
        from http.server import ThreadingHTTPServer
        from real_http import make_handler
        self.start(self.agent_a)
        self.idle()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.backend))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            for agent, has_result in ((self.agent_a, True), (self.agent_b, False)):
                query = urlencode({"user": "friend", "replyOptions": "true", "agentId": agent["id"],
                                   "agentRevision": agent["revision"]})
                connection.request("GET", "/api/model-guidance?" + query)
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 200, body)
                self.assertEqual(body["agentId"], agent["id"])
                self.assertEqual(body["guidance"] is not None, has_result)
            self.assertEqual(len(self.requests), 1)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
