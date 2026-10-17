"""Synthetic catalog compilation checks; no source, account DB or provider use."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from advisor_contracts import AdvisorError
from advisor_packages import parse_files
from advisor_service import AdvisorService
from test_advisor_support import FakeSource


class ReplySettingsTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parent.parent / ".local/implementation-tests/advisor-reply-settings"
        artifacts.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = FakeSource()
        self.service = AdvisorService(self.source, object(), self.root,
                                      runtime_factory=lambda: self.fail("runtime must stay idle"))
        self.account_guard = patch.object(self.service.stores, "account",
                                         side_effect=AssertionError("account data must stay closed"))
        self.account_guard.start()
        self.addCleanup(self.account_guard.stop)

    def assert_code(self, expected, function):
        with self.assertRaises(AdvisorError) as raised:
            function()
        self.assertEqual(raised.exception.code, expected)

    def package(self, reference="Reply naturally and briefly."):
        return parse_files({
            "SKILL.md": "---\nname: Sample\ndescription: Synthetic package\n---\n# Sample\n[Reply](references/reply.md)\n",
            "references/reply.md": "# Reply\n" + reference,
            "scripts/memory.py": "raise RuntimeError('never execute')",
            "LICENSE": "MIT License\nSynthetic fixture only.",
        }, {"kind": "local", "label": "synthetic"})

    def test_prompt_and_skills_are_detached_without_any_account_or_runtime(self):
        settings = self.service.reply_settings("builtin:advisor", 1, "How to reply?")
        agent = self.service.stores.config.agent("builtin:advisor")
        skill = next(item for item in self.service.stores.config.skills()
                     if item["id"] == agent["skillIds"][0])
        self.assertEqual(set(settings), {"agentId", "agentRevision", "stamp", "prompt", "skills"})
        self.assertEqual(settings["prompt"], agent["prompt"])
        self.assertEqual(settings["skills"], [{"id": skill["id"], "content": skill["content"]}])
        self.assertEqual({key: settings[key] for key in ("agentId", "agentRevision", "stamp")},
                         self.service.reply_identity(agent["id"], agent["revision"]))
        self.service.assert_reply_settings(settings)
        settings["skills"][0]["content"] = "modified detached snapshot"
        self.assertEqual(self.service.reply_settings(agent["id"], 1)["skills"][0]["content"], skill["content"])
        self.assertEqual(self.source.identity_calls, 0)
        self.assertEqual(self.source.page_calls, [])
        self.assertIsNone(self.service._runtime)
        self.assertFalse((self.root / ".local/advisor-data").exists())

    def test_different_assistants_have_distinct_identity_and_content(self):
        first = self.service.reply_settings("builtin:advisor", 1)
        second = self.service.reply_settings("builtin:empathy", 1)
        self.assertNotEqual(first["stamp"], second["stamp"])
        self.assertNotEqual(first["prompt"], second["prompt"])
        self.assertNotEqual(first["skills"], second["skills"])

    def test_imported_package_reuses_references_and_disabled_capability_notice(self):
        package = self.package()
        imported = self.service.stores.config.import_assistant(package)
        settings = self.service.reply_settings(imported["agent"]["id"], 1, "Reply naturally")
        skill = settings["skills"][0]
        self.assertEqual(set(skill), {"id", "content"})
        self.assertTrue(skill["content"].startswith(package["entry"]["content"]))
        self.assertIn("Reply naturally and briefly.", skill["content"])
        self.assertIn(package["compatibility"]["hostInstructions"], skill["content"])
        self.assertNotIn("raise RuntimeError", skill["content"])
        self.assertNotIn(str(self.root), str(settings))
        self.assertNotIn("package", settings)

    def test_revision_unknown_disabled_and_malformed_selection_are_rejected(self):
        self.assert_code("agent-unknown", lambda: self.service.reply_identity("agent:missing", 1))
        for revision in (True, False, 0, -1, None, "1", 1.0):
            self.assert_code("invalid-request", lambda revision=revision:
                             self.service.reply_identity("builtin:advisor", revision))
        self.assert_code("agent-changed", lambda: self.service.reply_identity("builtin:advisor", 2))
        self.service.stores.config.set_enabled("builtin:advisor", False)
        self.assert_code("agent-disabled", lambda: self.service.reply_identity("builtin:advisor", 2))

    def test_template_edit_and_skill_deletion_invalidate_frozen_settings(self):
        settings = self.service.reply_settings("builtin:advisor", 1)
        agent = self.service.stores.config.agent("builtin:advisor")
        self.service.stores.config.save_agent({**agent, "prompt": "New synthetic prompt"})
        self.assert_code("agent-changed", lambda: self.service.assert_reply_settings(settings))
        settings = self.service.reply_settings("builtin:advisor", 2)
        self.service.stores.config.delete_skill(agent["skillIds"][0])
        self.assert_code("agent-changed", lambda: self.service.assert_reply_settings(settings))

    def test_reference_only_changes_invalidate_identity_without_revision_change(self):
        imported = self.service.stores.config.import_assistant(self.package())
        agent = imported["agent"]
        settings = self.service.reply_settings(agent["id"], 1)
        replacement = self.package("Reply with a different referenced approach.")
        with self.service.stores.config.lock:
            document = self.service.stores.config._load()
            saved = next(row for row in document["skills"] if row["id"] == imported["skill"]["id"])
            saved["package"] = replacement
        self.assertEqual(self.service.stores.config.agent(agent["id"])["revision"], 1)
        self.assert_code("agent-changed", lambda: self.service.assert_reply_settings(settings))

    def test_oversize_required_skill_is_rejected_instead_of_truncated_or_dropped(self):
        skill = self.service.stores.config.import_skill("Long", "", "x" * 24000)
        agent = self.service.stores.config.save_agent({"name": "Long assistant", "description": "",
                                                     "prompt": "Reply briefly", "skillIds": [skill["id"]]})
        self.assert_code("context-too-long", lambda: self.service.reply_settings(agent["id"], 1,
                                                                                 context_tokens=4096))
        settings = self.service.reply_settings(agent["id"], 1, context_tokens=100000)
        self.assertEqual(settings["skills"][0]["content"], "x" * 24000)

    def test_identity_and_assertion_do_not_compile_references(self):
        with patch.object(self.service, "_runtime_skills", side_effect=AssertionError("no reference compilation")):
            identity = self.service.reply_identity("builtin:advisor", 1)
            self.service.assert_reply_settings(identity)

    def test_invalid_question_and_context_limits_are_explicit(self):
        for question in (None, [], "x" * 16001, "bad\0input"):
            self.assert_code("invalid-request", lambda question=question:
                             self.service.reply_settings("builtin:advisor", 1, question))
        for context_tokens in (True, 4095, 1000001, "32768"):
            self.assert_code("invalid-request", lambda context_tokens=context_tokens:
                             self.service.reply_settings("builtin:advisor", 1, context_tokens=context_tokens))


if __name__ == "__main__":
    unittest.main()
