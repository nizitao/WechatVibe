"""Guidance integration over synthetic chat only; no model or account IO."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from advisor_service import AdvisorService
from backend_contracts import AccountChangedError
from test_api_guidance import guidance, others, reply_options
import test_api_insights as api_fixture
from test_runtime_binding import install_binding
from real_http import make_handler


class GuidanceLifecycleTests(unittest.TestCase):
    activate = api_fixture.ApiInsightTests.activate

    def setUp(self):
        folder = Path(__file__).resolve().parents[1] / ".local" / "reply-assistant-tests"
        folder.mkdir(parents=True, exist_ok=True)
        temporary_directory = tempfile.TemporaryDirectory
        with patch.object(api_fixture.tempfile, "TemporaryDirectory",
                          side_effect=lambda: temporary_directory(dir=folder)):
            api_fixture.ApiInsightTests.setUp(self)

    def reply_args(self):
        return {"reply_options": True, "agent_id": self.reply_agent["id"],
                "agent_revision": self.reply_agent["revision"]}

    def reply_payload(self):
        return {"replyOptions": True, "agentId": self.reply_agent["id"],
                "agentRevision": self.reply_agent["revision"]}

    def reply_query(self):
        from urllib.parse import urlencode
        return urlencode({"user": "friend", "replyOptions": "true",
                          "agentId": self.reply_agent["id"],
                          "agentRevision": self.reply_agent["revision"]})

    def ready(self):
        self.selection = self.activate()
        self.reader, self.switch = install_binding(self.source, self.temp.name)
        self.advisor = AdvisorService(self.source, self.store, self.temp.name)
        self.backend._advisor = self.advisor
        self.reply_agent = self.advisor.stores.config.agent("builtin:advisor")
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.addCleanup(self.release.set)
        self.reply_transform = lambda value: value

        def provider(*args):
            self.calls.append(args)
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("synthetic provider blocked")
            if len(args) > 13 and args[13]:
                return {"guidance": self.reply_transform(reply_options())}
            value = guidance(scenario=args[6], analyzeSelf=args[7],
                             subtexts=[{"id": identity, "status": "uncertain"} for identity in args[5]],
                             advice={"forOthers": others(), "forSelf": (
                                 {"summary": "表达清楚", "strengths": ["明确"], "improvements": ["留出回应空间"]}
                                 if args[7] else None)})
            return {"guidance": self.reply_transform(value)}
        guard = patch.object(self.analyzer, "model_guidance", provider, create=True)
        guard.start()
        self.addCleanup(guard.stop)

    def idle(self):
        self.assertTrue(self.backend.api_tasks.wait_for_idle(3))

    def test_reply_options_generate_replace_and_keep_legacy_guidance_separate(self):
        self.ready()
        self.backend.start_guidance("account-a", "friend")
        self.idle()
        self.assertIsNone(self.backend.guidance("friend", **self.reply_args())["guidance"])
        with patch.object(self.backend, "_api_subject_summary",
                          side_effect=AssertionError("reply options do not read portraits")):
            first = self.backend.start_guidance("account-a", "friend", **self.reply_args())
            self.idle()
        result = self.backend.guidance("friend", **self.reply_args())
        self.assertEqual(result["guidance"], reply_options())
        sent = self.calls[-1]
        self.assertTrue(sent[13])
        self.assertIsNone(sent[9])
        self.assertIsNone(sent[10])
        self.assertIsNone(sent[11])
        self.assertIsNone(sent[12])
        self.assertEqual([item["id"] for item in sent[4]], ["s1", "o1", "o2"])
        self.assertEqual(self.backend.guidance("friend")["guidance"]["version"], "api-guidance-v5")
        self.assertEqual(self.backend.guidance("friend")["job"]["status"], "done")
        second = self.backend.start_guidance("account-a", "friend", **self.reply_args())
        self.idle()
        self.assertNotEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(len(self.calls), 3)

    def test_reply_options_clear_discards_inflight_result_and_rejects_manual_draft(self):
        self.ready()
        with self.assertRaises(ValueError):
            self.backend.start_guidance("account-a", "friend", draft="写回复", **self.reply_args())
        self.release.clear()
        self.backend.start_guidance("account-a", "friend", **self.reply_args())
        self.assertTrue(self.entered.wait(2))
        self.backend.analysis_scope_clear("account-a", "friend", self.selection["sourceId"], "conversation")
        self.release.set()
        self.idle()
        self.assertIsNone(self.backend.guidance("friend", **self.reply_args())["guidance"])

    def test_reply_options_malformed_provider_does_not_persist(self):
        self.ready()
        with patch("backend_service.API_MODEL_RETRY_MAX", 0):
            for transform in [lambda _: guidance(),
                              lambda value: value | {"replies": [{"text": "好"}] * 3},
                              lambda value: value | {"reading": "额外场景解读"}]:
                self.reply_transform = transform
                self.backend.start_guidance("account-a", "friend", **self.reply_args())
                self.idle()
                result = self.backend.guidance("friend", **self.reply_args())
                self.assertEqual(result["job"]["status"], "error")
                self.assertEqual(result["job"]["error"], "invalid-guidance")
                self.assertIsNone(result["guidance"])

    def test_reply_options_report_truncation_without_repeating_the_same_limited_request(self):
        self.ready()
        with patch.object(self.analyzer, "model_guidance", side_effect=RuntimeError("output-truncated")) as provider:
            self.backend.start_guidance("account-a", "friend", **self.reply_args())
            self.idle()
        self.assertEqual(provider.call_count, 1)
        result = self.backend.guidance("friend", **self.reply_args())
        self.assertEqual(result["job"]["status"], "error")
        self.assertEqual(result["job"]["error"], "output-truncated")
        self.assertIsNone(result["guidance"])

    def test_http_reply_options_mode_reads_only_its_own_result_and_rejects_manual_input(self):
        self.ready()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.backend))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        headers = {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}"}
        try:
            payload = {"account": "account-a", "user": "friend", **self.reply_payload()}
            connection.request("POST", "/api/model-guidance", json.dumps(payload), headers)
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 202, body)
            self.idle()
            connection.request("GET", "/api/model-guidance?" + self.reply_query())
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 200, body)
            self.assertEqual(body["guidance"], reply_options())
            connection.request("GET", "/api/model-guidance?user=friend")
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 200, body)
            self.assertIsNone(body["guidance"])
            for invalid in [payload | {"draft": "输入要求"}, payload | {"replyOptions": "true"}]:
                connection.request("POST", "/api/model-guidance", json.dumps(invalid), headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 400)
        finally:
            connection.close()
            self.release.set()
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_delayed_http_reply_request_checks_the_expected_source_before_context_or_provider(self):
        self.ready()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.backend))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        headers = {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}"}
        original = self.backend.start_guidance
        try:
            # The optional legacy form snapshots whatever source is active at receipt.
            # An explicit UI source must reject that same delay across an API/local switch.
            for include_source, destination in ((False, "api"), (True, "api"), (True, "local")):
                selected = self.activate()
                self.calls.clear()
                arrived, continue_request = threading.Event(), threading.Event()
                responses, errors = [], []
                payload = {"account": "account-a", "user": "friend", **self.reply_payload()}
                if include_source:
                    payload["sourceId"] = selected["sourceId"]

                def delayed(*args, **kwargs):
                    arrived.set()
                    if not continue_request.wait(3):
                        raise RuntimeError("synthetic delayed request timed out")
                    return original(*args, **kwargs)

                def request():
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
                    try:
                        connection.request("POST", "/api/model-guidance", json.dumps(payload), headers)
                        response = connection.getresponse()
                        responses.append((response.status, json.loads(response.read())))
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        connection.close()

                with patch.object(self.backend, "start_guidance", delayed), \
                        patch.object(self.source, "messages", wraps=self.source.messages) as read:
                    requester = threading.Thread(target=request, daemon=True)
                    requester.start()
                    try:
                        self.assertTrue(arrived.wait(2))
                        if destination == "api":
                            changed = self.activate(model="second-synthetic-model")
                        else:
                            changed = self.backend.model_source_activate({"mode": "local"})
                        self.assertNotEqual(changed["sourceId"], selected["sourceId"])
                    finally:
                        continue_request.set()
                        requester.join(4)
                    self.assertFalse(requester.is_alive())
                    self.assertEqual(errors, [])
                    self.idle()
                    status, body = responses[0]
                    if include_source:
                        self.assertEqual(status, 409, body)
                        self.assertEqual(body["error"], "model-source-changed")
                        self.assertEqual(read.call_count, 0)
                        self.assertEqual(self.calls, [], "stale UI source cannot reach the new provider")
                    else:
                        self.assertEqual(status, 202, body)
                        self.assertEqual(body["sourceId"], changed["sourceId"])
                        self.assertEqual(len(self.calls), 1)
                        self.assertEqual(self.calls[0][3], "second-synthetic-model")

            selected = self.activate()
            self.calls.clear()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                payload = {"account": "account-a", "user": "friend", **self.reply_payload(),
                           "sourceId": selected["sourceId"]}
                connection.request("POST", "/api/model-guidance", json.dumps(payload), headers)
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 202, body)
                self.assertEqual(body["sourceId"], selected["sourceId"])
                self.idle()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0][3], "test-model")
            finally:
                connection.close()
        finally:
            self.release.set()
            server.shutdown()
            server.server_close()
            server_thread.join(3)

    def test_get_is_readonly_and_duplicate_send_is_idempotent(self):
        self.ready()
        for _ in range(3):
            self.assertIsNone(self.backend.guidance("friend")["guidance"])
        self.assertEqual(self.calls, [])
        self.release.clear()
        first = self.backend.start_guidance("account-a", "friend")
        self.assertTrue(self.entered.wait(2))
        again = self.backend.start_guidance("account-a", "friend")
        self.assertEqual(first["job"]["id"], again["job"]["id"])
        self.release.set()
        self.idle()
        for _ in range(3):
            public = self.backend.guidance("friend")
            self.assertEqual(public["job"]["status"], "done")
            self.assertEqual([item["id"] for item in public["guidance"]["subtexts"]], ["o1", "o2"])
            self.assertEqual(public["guidance"]["advice"]["forOthers"], others())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][6], "auto", "no user-selected scene is required")
        self.assertEqual(public["guidance"]["scenario"], "auto")

    def test_context_is_chronological_and_self_never_borrows_other_portrait(self):
        self.ready()
        with patch.object(self.backend, "_api_subject_summary", return_value="对方画像专属标记"):
            self.backend.start_guidance("account-a", "friend", analyze_self=True)
            self.idle()
        sent = self.calls[0]
        self.assertEqual([item["id"] for item in sent[4]], ["s1", "o1", "o2"])
        self.assertEqual(list(sent[5]), ["o1", "o2"])
        self.assertEqual(sent[9], "对方画像专属标记")
        self.assertIsNone(sent[10], "a conversation's OTHER portrait cannot become my portrait")
        self.assertEqual(sent[4][0]["sender"], "SELF")

    def test_reply_voice_keeps_self_text_without_enabling_optional_self_review(self):
        self.ready()
        original = self.source.messages
        with patch.object(self.source, "messages", wraps=original) as read:
            self.backend.start_guidance("account-a", "friend", analyze_self=False)
            self.idle()
        self.assertEqual(read.call_count, 1, "do not re-read another history for reply style")
        self.assertEqual(read.call_args.args[0], "friend")
        self.assertEqual(len(self.calls), 1)
        sent = self.calls[0]
        self.assertEqual([item["text"] for item in sent[4] if item["sender"] == "SELF"],
                         ["周六看展吗？"])
        self.assertFalse(sent[7])
        self.assertIsNone(sent[10])
        self.assertEqual(self.source.history_pages, 0)

    def test_first_draft_is_forwarded_without_old_advice_or_a_separate_style_request(self):
        self.ready()
        draft = "想婉拒这次邀请。\n\t保留我的日常语气，不生硬。"
        self.assertIsNone(self.backend.guidance("friend")["guidance"])
        self.backend.start_guidance("account-a", "friend", draft=draft)
        self.idle()
        self.assertEqual(len(self.calls), 1)
        sent = self.calls[0]
        self.assertEqual(sent[6], "auto")
        self.assertFalse(sent[7])
        self.assertIsNone(sent[11], "the old feedback field is not borrowed as a draft")
        self.assertEqual(sent[12], draft)
        self.assertEqual([item["text"] for item in sent[4] if item["sender"] == "SELF"],
                         ["周六看展吗？"])
        self.assertEqual(self.backend.guidance("friend")["job"]["status"], "done")
        # An explicit new generation uses only its own draft, not the stored suggestion.
        second = "愿意参加，但请帮我问一下几点集合。"
        self.backend.start_guidance("account-a", "friend", draft=second)
        self.idle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][12], second)

    def test_inflight_deduplication_never_misrepresents_a_changed_draft_as_submitted(self):
        self.ready()
        self.release.clear()
        first = self.backend.start_guidance("account-a", "friend", draft="婉拒邀请")
        self.assertTrue(self.entered.wait(2))
        again = self.backend.start_guidance("account-a", "friend", draft="婉拒邀请")
        self.assertEqual(first["job"]["id"], again["job"]["id"])
        self.assertNotIn("_inputDigest", again["job"])
        self.assertNotIn("_inputDigest", self.backend.guidance("friend")["job"])
        variants = [
            {"draft": "接受邀请"},
            {"draft": "婉拒邀请", "feedback": "换一个方向"},
            {"draft": "婉拒邀请", "scenario": "leader"},
            {"draft": "婉拒邀请", "analyze_self": True},
        ]
        for variant in variants:
            with self.assertRaisesRegex(RuntimeError, "^guidance-busy$"):
                self.backend.start_guidance("account-a", "friend", **variant)
        self.release.set()
        self.idle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][12], "婉拒邀请")

    def test_job_started_during_context_read_still_rejects_a_different_draft(self):
        self.ready()
        original = self.source.messages
        def read_then_install_job(*args):
            rows = original(*args)
            key = ("account-a", "friend", self.selection["sourceId"], "friend")
            self.backend.api_guidance_jobs[key] = {
                "id": "other-request", "status": "queued", "_inputDigest": "different-input"}
            return rows
        with patch.object(self.source, "messages", read_then_install_job):
            with self.assertRaisesRegex(RuntimeError, "^guidance-busy$"):
                self.backend.start_guidance("account-a", "friend", draft="本次的不同要求")
        self.assertEqual(self.calls, [])

    def test_draft_bounds_reject_invalid_input_before_loading_chat_or_calling_provider(self):
        self.ready()
        with patch.object(self.source, "messages", wraps=self.source.messages) as read:
            for draft in (True, 1, [], "字" * 2001, "😀" * 2001, "a\0b", "a\x7fb"):
                with self.subTest(draft_type=type(draft).__name__):
                    with self.assertRaisesRegex(ValueError, "invalid guidance draft"):
                        self.backend.start_guidance("account-a", "friend", draft=draft)
            self.assertEqual(read.call_count, 0)
        self.assertEqual(self.calls, [])
        self.backend.start_guidance("account-a", "friend", draft="😀" * 2000)
        self.idle()
        self.assertEqual(self.calls[0][12], "😀" * 2000)

    def test_http_passes_draft_and_defaults_to_the_whole_current_conversation(self):
        self.ready()
        self.release.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.backend))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                draft = "想和大家确认出发时间。\n轻松一点。"
                payload = {"account": "account-a", "user": "group@chatroom", "draft": draft}
                connection.request("POST", "/api/model-guidance", json.dumps(payload),
                                   {"Content-Type": "application/json",
                                    "Origin": f"http://localhost:{server.server_port}"})
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 202, body)
                self.assertEqual(body["subject"], "group@chatroom")
                self.assertTrue(self.entered.wait(2))
                connection.request("POST", "/api/model-guidance",
                                   json.dumps({**payload, "draft": "我不想参加了，帮我婉拒。"}),
                                   {"Content-Type": "application/json",
                                    "Origin": f"http://localhost:{server.server_port}"})
                busy = connection.getresponse()
                rejection = json.loads(busy.read())
                self.assertEqual(busy.status, 409, rejection)
                self.assertEqual(rejection["error"], "guidance-busy")
                self.assertEqual(rejection["message"], "上一条建议仍在生成，请完成后再试")
                self.release.set()
                self.idle()
                self.assertEqual(self.calls[0][12], draft)
                self.assertEqual(self.calls[0][6], "auto")
            finally:
                connection.close()
        finally:
            self.release.set()
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_member_guidance_does_not_misattribute_another_member(self):
        self.ready()
        self.source.rows.insert(2, {"id": "third", "side": "other", "kind": "text",
                                   "text": "第三个人的无关意见", "senderId": "someone-else",
                                   "_sort": [2, "shard", 3]})
        self.backend.start_guidance("account-a", "group@chatroom", member="friend")
        self.idle()
        self.assertEqual(list(self.calls[0][5]), ["o1", "o2"])
        self.assertNotIn("third", [item["id"] for item in self.calls[0][4]])
        self.assertEqual([item["id"] for item in self.calls[0][4]
                          if item["sender"] == "SELF"], ["s1"],
                         "member filtering must keep my own wording for candidate replies")
        self.assertIsNotNone(self.backend.guidance("group@chatroom", "friend")["guidance"])
        self.assertIsNone(self.backend.guidance("group@chatroom", "someone-else")["guidance"])

    def test_group_scene_keeps_members_distinct_without_exposing_contact_ids(self):
        self.ready()
        self.source.rows.insert(2, {"id": "third", "side": "other", "kind": "text",
                                   "text": "我今天不去了。", "senderId": "someone-else",
                                   "_sort": [2, "shard", 3]})
        self.backend.start_guidance("account-a", "group@chatroom")
        self.idle()
        messages = {item["id"]: item for item in self.calls[0][4]}
        self.assertEqual(messages["o1"]["speaker"], messages["o2"]["speaker"])
        self.assertNotEqual(messages["o1"]["speaker"], messages["third"]["speaker"])
        self.assertNotIn("speaker", messages["s1"])
        self.assertTrue(all(item["speaker"].startswith("P") for item in messages.values()
                            if item["sender"] == "OTHER"))
        self.assertTrue(all("senderId" not in item for item in messages.values()))
        self.assertEqual(len(self.calls), 1)

    def test_group_draft_can_reference_existing_display_names_without_a_contact_lookup(self):
        self.ready()
        for item in self.source.rows:
            if item["side"] == "other":
                item["senderName"] = "陈安"
        self.source.rows.insert(2, {"id": "third", "side": "other", "kind": "text",
                                   "text": "我今天不去了。", "senderId": "someone-else",
                                   "senderName": "言希", "_sort": [2, "shard", 3]})
        self.source.rows.insert(3, {"id": "same-name", "side": "other", "kind": "text",
                                   "text": "我可以参加", "senderId": "other-person",
                                   "senderName": "陈安", "_sort": [3, "shard", 4]})
        self.source.rows.insert(4, {"id": "missing-name", "side": "other", "kind": "text",
                                   "text": "收到", "senderId": "raw-contact-id",
                                   "senderName": "raw-contact-id", "_sort": [4, "shard", 5]})
        self.backend.start_guidance("account-a", "group@chatroom", draft="帮我回复陈安")
        self.idle()
        self.assertEqual(len(self.calls), 1)
        messages = {item["id"]: item for item in self.calls[0][4]}
        self.assertEqual(messages["o1"]["speakerName"], "陈安")
        self.assertEqual(messages["third"]["speakerName"], "言希")
        self.assertEqual(messages["same-name"]["speakerName"], "陈安")
        self.assertNotEqual(messages["o1"]["speaker"], messages["same-name"]["speaker"])
        self.assertNotIn("speakerName", messages["missing-name"])
        self.assertTrue(all("senderId" not in item for item in messages.values()))

    def test_clear_while_waiting_for_capacity_never_reaches_provider(self):
        self.ready()
        with self.backend.api_pool.lease():
            self.backend.start_guidance("account-a", "friend")
            with self.backend.api_pool.condition:
                self.assertTrue(self.backend.api_pool.condition.wait_for(
                    lambda: bool(self.backend.api_pool.waiters["shared"]), timeout=3))
            self.backend.analysis_scope_clear("account-a", "friend", self.selection["sourceId"], "conversation")
            self.idle()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.backend.guidance("friend")["guidance"])

    def test_source_aba_and_reset_discard_inflight_output(self):
        self.ready()
        for change in ("aba", "clear"):
            with self.subTest(change=change):
                self.entered.clear()
                self.release.clear()
                self.backend.start_guidance("account-a", "friend")
                self.assertTrue(self.entered.wait(2))
                if change == "aba":
                    self.switch(1)
                    self.switch(0)
                else:
                    self.backend.analysis_scope_clear("account-a", "friend", self.selection["sourceId"], "conversation")
                self.release.set()
                self.idle()
                self.assertIsNone(self.backend.guidance("friend")["guidance"])

    def test_reset_during_preload_cannot_recreate_guidance(self):
        self.ready()
        original = self.source.messages
        def read_then_clear(*args):
            rows = original(*args)
            self.backend.analysis_scope_clear("account-a", "friend", self.selection["sourceId"], "conversation")
            return rows
        with patch.object(self.source, "messages", read_then_clear):
            with self.assertRaises((AccountChangedError, RuntimeError)):
                self.backend.start_guidance("account-a", "friend")
        self.idle()
        self.assertEqual(self.calls, [])

    def test_wrong_target_and_malformed_provider_shape_never_persist(self):
        self.ready()
        transforms = [
            lambda value: value | {"subtexts": [{"id": "not-the-target", "status": "uncertain"}]},
            lambda value: value | {"scenario": "leader"},
            lambda value: value | {"analyzeSelf": True},
            lambda value: value | {"subtexts": [{"id": [], "status": "uncertain"}]},
        ]
        with patch("backend_service.API_MODEL_RETRY_MAX", 0):
            for transform in transforms:
                self.reply_transform = transform
                self.backend.start_guidance("account-a", "friend")
                self.idle()
                result = self.backend.guidance("friend")
                self.assertEqual(result["job"]["status"], "error")
                self.assertEqual(result["job"]["error"], "invalid-guidance")
                self.assertIsNone(result["guidance"])


if __name__ == "__main__":
    unittest.main()
