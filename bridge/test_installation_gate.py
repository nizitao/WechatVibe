"""Race/ownership checks over synthetic installation metadata and child processes."""
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installation_gate import InstallationGate, InstallationBusy, process_identity, user_gate_home


def registered_child(root, directory, ready, release):
    gate = InstallationGate(root, directory=directory)
    gate.register(role="shell")
    ready.set()
    release.wait(10)
    gate.close()


def racing_child(root, directory, start, release, results, action, token=None):
    gate = InstallationGate(root, directory=directory)
    start.wait(5)
    try:
        if action == "claim":
            value = gate.claim(owner_pid=os.getpid(), parent_pid=os.getppid())
        else:
            gate.register(role="launcher", token=token)
            value = True
        results.put((action, True, value))
        release.wait(10)
    except InstallationBusy:
        results.put((action, False, None))


class InstallationGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "synthetic-install"
        self.directory = Path(temporary.name) / "gate"
        self.gate = InstallationGate(self.root, directory=self.directory)
        self.context = multiprocessing.get_context("spawn")

    def test_every_profile_blocks_exclusive_claim_until_its_shell_exits(self):
        ready, release = self.context.Event(), self.context.Event()
        child = self.context.Process(target=registered_child, args=(self.root, self.directory, ready, release))
        child.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaises(InstallationBusy):
                self.gate.claim(owner_pid=os.getpid(), parent_pid=os.getppid())
        finally:
            release.set()
            child.join(5)
        self.assertEqual(child.exitcode, 0)
        token = self.gate.claim(owner_pid=os.getpid(), parent_pid=os.getppid())
        self.gate.release_claim(owner_pid=os.getpid(), token=token)

    def test_same_installation_uses_same_gate_with_different_temp_environments(self):
        local = self.root.parent / "synthetic-local-appdata"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local), "TEMP": str(self.root / "temp-a"),
                                     "TMP": str(self.root / "temp-a")}):
            first = InstallationGate(self.root)
            token = first.claim(owner_pid=os.getpid(), parent_pid=None)
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local), "TEMP": str(self.root / "temp-b"),
                                         "TMP": str(self.root / "temp-b")}):
                second = InstallationGate(self.root)
                self.assertEqual(first.directory, second.directory)
                with self.assertRaises(InstallationBusy):
                    second.register(role="shell", token="wrong")
                self.assertNotEqual(second.directory, InstallationGate(self.root / "other").directory)
        finally:
            first.release_claim(owner_pid=os.getpid(), token=token)

    @unittest.skipUnless(os.name == "nt", "Windows known-folder fallback")
    def test_missing_localappdata_uses_known_folder_and_refuses_failed_resolution(self):
        local = self.root.parent / "synthetic-known-folder"
        def known_folder(_window, identifier, _token, _flags, buffer):
            self.assertEqual(identifier, 0x1C)
            buffer.value = str(local)
            return 0
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}), \
                patch("ctypes.windll.shell32.SHGetFolderPathW", side_effect=known_folder):
            self.assertEqual(user_gate_home(), local / "WechatVibe" / "installation-gates")
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}), \
                patch("ctypes.windll.shell32.SHGetFolderPathW", return_value=1):
            with self.assertRaises(InstallationBusy):
                user_gate_home()

    def test_update_claim_blocks_new_launcher_and_manual_window(self):
        token = self.gate.claim(owner_pid=os.getpid(), parent_pid=os.getppid())
        for role in ("launcher", "bridge", "shell"):
            with self.subTest(role=role), self.assertRaises(InstallationBusy):
                InstallationGate(self.root, directory=self.directory).register(role=role, token="wrong")
        self.gate.release_claim(owner_pid=os.getpid(), token=token)
        self.gate.register(role="launcher")
        self.gate.close()

    def test_claim_and_launch_race_has_exactly_one_winner(self):
        start, release, results = self.context.Event(), self.context.Event(), self.context.Queue()
        children = [self.context.Process(target=racing_child, args=(self.root, self.directory, start, release, results, action))
                    for action in ("claim", "register")]
        for child in children:
            child.start()
        start.set()
        try:
            outcomes = [results.get(timeout=5) for _ in children]
            self.assertEqual(sum(int(item[1]) for item in outcomes), 1)
        finally:
            release.set()
            for child in children:
                child.join(5)
                self.assertEqual(child.exitcode, 0)
            results.close()

    def test_token_alone_does_not_authorize_an_unrelated_parent_process(self):
        start, release, results = self.context.Event(), self.context.Event(), self.context.Queue()
        child = self.context.Process(target=racing_child,
            args=(self.root, self.directory, start, release, results, "claim"))
        child.start()
        start.set()
        try:
            _action, success, token = results.get(timeout=5)
            self.assertTrue(success)
            with self.assertRaises(InstallationBusy):
                self.gate.register(role="shell", token=token)
        finally:
            release.set()
            child.join(5)
            results.close()
        self.gate.register(role="shell")  # dead updater claim is reclaimed under the OS lock
        self.gate.close()

    def test_helper_descendant_can_register_only_with_matching_token(self):
        token = self.gate.claim(owner_pid=os.getpid(), parent_pid=os.getppid())
        start, release, results = self.context.Event(), self.context.Event(), self.context.Queue()
        child = self.context.Process(target=racing_child,
            args=(self.root, self.directory, start, release, results, "register", token))
        child.start()
        start.set()
        try:
            self.assertTrue(results.get(timeout=5)[1])
        finally:
            release.set()
            child.join(5)
            results.close()
            self.gate.release_claim(owner_pid=os.getpid(), token=token)

    def test_reused_pid_does_not_keep_stale_ownership(self):
        self.gate.register(role="shell")
        state = json.loads(self.gate.state_path.read_text())
        state["processes"][0]["created"] -= 100
        self.gate.state_path.write_text(json.dumps(state))
        token = self.gate.claim(owner_pid=os.getpid(), parent_pid=os.getppid())
        self.assertEqual(json.loads(self.gate.state_path.read_text())["processes"], [])
        self.gate.release_claim(owner_pid=os.getpid(), token=token)

    def test_cannot_exempt_an_unrelated_live_process_as_parent(self):
        with self.assertRaises(InstallationBusy):
            self.gate.claim(owner_pid=os.getpid(), parent_pid=os.getpid())


if __name__ == "__main__":
    unittest.main()
