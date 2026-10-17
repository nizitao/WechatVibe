"""Synthetic-only launch checks included in the normal Python regression suite."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("synthetic_launcher", ROOT / "scripts/start-test-client.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class SyntheticStageTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".local/synthetic-launch-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.child = self.source / ".local/test-client/client"
        self.manifest = SimpleNamespace(PUBLIC_FILES=("chatui/index.html", "LICENSE"),
                                        SCRIPTS=("desktop-main.cjs",), BRIDGE=("chat_server.py",),
                                        LAYA=(), NATIVE_READER=())
        for name in ("chatui/index.html", "LICENSE", "scripts/desktop-main.cjs", *launcher.SUPPORT):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public synthetic input: " + name, encoding="utf-8")
        (self.source / "package.json").write_text(json.dumps({"version": "1.3.0", "main": "scripts/desktop-main.cjs"}))
        private = self.source / ".local/real-client-runtime/api-model-source.json"
        private.parent.mkdir(parents=True)
        private.write_text(json.dumps({"version": 2, "profiles": [], "selectedMode": "local"}))

    def prepare(self):
        return launcher.prepare(self.source, manifest=self.manifest, include_dependencies=False,
                                include_models=False, include_advisor=False)

    def test_whitelist_copies_public_code_and_replaces_only_test_entry(self):
        result = self.prepare()
        self.assertEqual(result["root"], str(self.child))
        self.assertIn("synthetic_chat_server", (self.child / "bridge/chat_server.py").read_text())
        self.assertIn("bridge/chat_server.py", (self.child / "bridge/real_classifier.py").read_text())
        self.assertEqual(json.loads((self.child / "package.json").read_text())["main"],
                         "scripts/desktop-main.cjs")
        self.assertEqual((self.child / ".local/real-client-runtime/api-model-source.json").read_bytes(),
                         (self.source / ".local/real-client-runtime/api-model-source.json").read_bytes())
        self.assertFalse((self.child / "scripts/synthetic_desktop_main.cjs").exists())
        self.assertFalse((self.child / "tests").exists())
        self.assertFalse((self.child / "output").exists())

    def test_refresh_keeps_only_existing_test_data_and_refreshes_source(self):
        self.prepare()
        own = self.child / ".local/real-client-data/example.sqlite3"
        own.parent.mkdir(parents=True)
        own.write_bytes(b"synthetic-cache")
        (self.source / "chatui/index.html").write_text("refreshed public UI")
        self.prepare()
        self.assertEqual(own.read_bytes(), b"synthetic-cache")
        self.assertEqual((self.child / "chatui/index.html").read_text(), "refreshed public UI")

    def test_rejects_unknown_or_wrong_marker_without_mutation(self):
        self.child.mkdir(parents=True)
        owned = self.child / "do-not-touch.txt"
        owned.write_text("keep")
        with self.assertRaisesRegex(ValueError, "unmarked"):
            self.prepare()
        (self.child / launcher.MARKER).write_text(json.dumps({"schema": 1, "mode": "production"}))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.prepare()
        self.assertEqual(owned.read_text(), "keep")

    def test_rejects_private_allowlist_entry(self):
        self.manifest.PUBLIC_FILES += (".local/real-client-runtime/api-model-source.json",)
        with self.assertRaisesRegex(ValueError, "private"):
            self.prepare()
        self.assertFalse((self.child / launcher.MARKER).exists())

    def test_rejects_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            launcher.target_path(self.child, "../outside")

    def test_atomic_refresh_does_not_write_through_existing_hardlink(self):
        self.prepare()
        target = self.child / "chatui/index.html"
        target.unlink()
        original = self.source / "unrelated-synthetic.txt"
        original.write_text("unchanged")
        os.link(original, target)
        self.prepare()
        self.assertEqual(original.read_text(), "unchanged")
        self.assertNotEqual(target.read_text(), "unchanged")

    def test_environment_does_not_inherit_credentials_or_control_state(self):
        inherited = {"PATH": os.environ.get("PATH", ""), "OPENAI_API_KEY": "private-fixture",
                     "ANTHROPIC_API_KEY": "private-fixture", "HTTPS_PROXY": "credential-fixture",
                     "WECHATVIBE_PROFILE": "production", "CHATUI_PORT": "45678",
                     "WECHATVIBE_UPDATE_VALIDATE": "1", "LAYA_MODEL_DIR": "outside",
                     "OPENCODE_CONFIG": "outside", "LOCALAPPDATA": "outside"}
        result = launcher.launch_environment(self.child, inherited, python=Path("F:/Python/python.exe"),
                                             node=Path("F:/node/node.exe"))
        self.assertNotIn("WECHATVIBE_PROFILE", result)
        self.assertEqual(result["WECHATVIBE_CLIENT_ROOT"], str(self.child))
        self.assertNotIn("CHATUI_PORT", result)
        self.assertNotIn("HTTPS_PROXY", result)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertNotIn("ANTHROPIC_API_KEY", result)
        self.assertNotIn("OPENCODE_CONFIG", result)
        self.assertNotIn("WECHATVIBE_UPDATE_VALIDATE", result)
        for name in ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "HOME", "USERPROFILE"):
            self.assertTrue(Path(result[name]).is_relative_to(self.child))

    def test_missing_support_does_not_start_or_create_identity(self):
        (self.source / "tests/support/synthetic_client.py").unlink()
        with self.assertRaisesRegex(ValueError, "Missing public"):
            self.prepare()
        self.assertFalse((self.child / launcher.MARKER).exists())

    def test_existing_dependency_directory_is_not_replaced(self):
        original = self.source / "node_modules"
        original.mkdir()
        target = self.child / "node_modules"
        target.mkdir(parents=True)
        (target / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "do not point"):
            launcher.dependency_link(original, target)
        self.assertEqual((target / "keep.txt").read_text(), "keep")

    def test_running_synthetic_bridge_blocks_source_refresh(self):
        self.prepare()
        runtime = self.child / ".local/real-client-runtime/synthetic"
        runtime.mkdir(parents=True)
        (runtime / "bridge-123.json").write_text(json.dumps({"pid": 123}))
        process = SimpleNamespace(cmdline=lambda: ["python", str(self.child / "bridge/chat_server.py")])
        with patch("psutil.Process", return_value=process):
            with self.assertRaisesRegex(ValueError, "Close the existing test window"):
                self.prepare()
        # A recycled PID belonging to another command is not ours to stop/block.
        process.cmdline = lambda: ["python", str(self.source / "unrelated.py")]
        with patch("psutil.Process", return_value=process):
            self.prepare()

    def test_linked_code_and_data_paths_are_rejected(self):
        original = self.source / "public-dir"
        original.mkdir()
        link = self.source / "linked-dir"
        launcher.dependency_link(original, link)
        with self.assertRaisesRegex(ValueError, "traverse a link"):
            launcher.target_path(link, "data.json")

    def test_model_missing_does_not_download_or_copy_configuration(self):
        self.manifest.MODEL_FILES = ("model.onnx",)
        self.manifest.MODEL_PINS = {"model.onnx": (3, "a" * 64)}
        self.assertEqual(launcher.stage_models(self.source, self.child, self.manifest)["state"], "missing")
        self.assertFalse((self.child / ".models").exists())

    def test_model_bundle_is_verified_and_hardlinked_without_duplicate_bytes(self):
        bundle = self.source / ".models/laya"
        bundle.mkdir(parents=True)
        (bundle / "model.onnx").write_bytes(b"model")
        self.manifest.MODEL_FILES = ("model.onnx",)
        self.manifest.MODEL_PINS = {"model.onnx": (5, launcher.digest(bundle / "model.onnx"))}
        (bundle / "private-settings.json").write_text("synthetic-secret-not-a-model")
        result = launcher.stage_models(self.source, self.child, self.manifest)
        self.assertEqual(result["state"], "ready")
        self.assertTrue(os.path.samefile(bundle / "model.onnx", self.child / ".models/laya/model.onnx"))
        self.assertFalse((self.child / ".models/laya/private-settings.json").exists())
        (bundle / "model.onnx").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "cannot be refreshed"):
            launcher.stage_models(self.source, self.child, self.manifest)

    def test_invalid_previous_advisor_engine_blocks_start_instead_of_reusing_it(self):
        previous = self.child / "runtime/opencode"
        previous.mkdir(parents=True)
        (previous / "opencode.exe").write_bytes(b"synthetic-old-binary")
        with patch.object(launcher, "module_from_file", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, "advisor engine cannot be refreshed"):
                launcher.stage_advisor(self.source, self.child)

    def test_desktop_wrapper_pins_identity_and_blocks_escaping_host_actions(self):
        self.prepare()
        node = shutil.which("node")
        if not node:
            self.skipTest("Node runtime unavailable")
        script = r'''
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assert = require('node:assert/strict'), { EventEmitter } = require('node:events');
const app = new EventEmitter(); app.setName = name => { app.name = name; };
const handlers = new Map(); const ipcMain = { handle: (name, fn) => handlers.set(name, fn) };
const fakeProcess = { env: { PATH: 'fixture-path', OPENAI_API_KEY: 'fixture-secret',
  HTTP_PROXY: 'fixture-proxy', WECHATVIBE_UPDATE_VALIDATE: '1', WECHATVIBE_PROFILE: 'production' },
  argv: ['electron', 'child', '--profile', 'production'] };
let loaded = false;
const root = process.argv[2];
const context = { process: fakeProcess, __dirname: path.join(root, 'scripts'),
  require: name => { if (name === 'electron') return { app, ipcMain };
    if (name === './desktop-main.cjs') { loaded = true; return {}; }
    return require(name); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
assert(loaded);
assert.equal(fakeProcess.env.WECHATVIBE_PROFILE, 'synthetic');
assert.equal(fakeProcess.env.WECHATVIBE_CLIENT_ROOT, root);
assert.equal(fakeProcess.env.OPENAI_API_KEY, undefined);
assert.equal(fakeProcess.env.HTTP_PROXY, undefined);
assert.equal(fakeProcess.env.WECHATVIBE_UPDATE_VALIDATE, undefined);
assert.deepEqual(fakeProcess.argv, ['electron', 'child', '--profile=synthetic']);
for (const name of ['TEMP','TMP','LOCALAPPDATA','APPDATA','HOME','USERPROFILE']) {
  assert(fakeProcess.env[name].startsWith(root + path.sep));
}
ipcMain.handle('real-client:model-download', () => 'must-not-run');
ipcMain.handle('real-client:data-root-choose-directory', () => 'must-not-run');
ipcMain.handle('real-client:copy-draft', () => 'still-available');
assert.equal(handlers.get('real-client:model-download')().phase, 'blocked');
assert.equal(handlers.get('real-client:data-root-choose-directory')(), null);
assert.equal(handlers.get('real-client:copy-draft')(), 'still-available');
'''
        result = subprocess.run([node, "-e", script, str(ROOT / "tests/support/synthetic_desktop_main.cjs"),
                                 str(self.child)], cwd=self.source, capture_output=True, text=True,
                                encoding="utf-8", timeout=15,
                                env=launcher.launch_environment(self.child))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
