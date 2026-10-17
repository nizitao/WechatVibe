"""Open official WechatVibe 1.3.0 with authored conversations only."""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "1e9e9b9f329e49c90f7a99d9a588268ae0f74c86"
# User-approved MBTI fix on the release baseline; no other working-tree files.
MBTI_OVERRIDES = ("electron/laya/personality.ts", "electron/api-portrait-classifier.ts",
                  "bridge/backend_contracts.py")
SPEC = importlib.util.spec_from_file_location("test_stage_helpers", ROOT / "scripts/start-test-client.py")
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


def release_bytes(path):
    return subprocess.check_output(["git", "show", f"{RELEASE}:{path}"], cwd=ROOT)


def release_manifest():
    names = {"SCRIPTS", "BRIDGE", "NATIVE_READER", "LAYA", "MODEL_FILES", "PUBLIC_FILES"}
    values = {}
    for item in ast.parse(release_bytes("scripts/stage-real-client.py")).body:
        if isinstance(item, ast.Assign) and len(item.targets) == 1:
            target = item.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                values[target.id] = ast.literal_eval(item.value)
    if set(values) != names:
        raise ValueError("The release public file list is incomplete")
    pins = json.loads(release_bytes("scripts/model-files.json"))
    values["MODEL_PINS"] = {name: (entry["bytes"], entry["sha256"])
                            for name, entry in pins["files"].items()}
    return SimpleNamespace(**values)


def assert_stopped(child):
    import psutil
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = process.info["cmdline"] or []
            if any(value.casefold().rstrip("/\\") in {
                    str(child).casefold(), str(child / "bridge/chat_server.py").casefold()}
                   for value in command[1:]):
                raise ValueError("Close the existing 1.3.0 test window before refreshing it")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def seed_configuration(child):
    """Copy explicit settings only, never histories, account stores or results."""
    files = (".local/advisor/config.json",
             ".local/real-client-runtime/inference-settings.json",
             ".local/real-client-runtime/analysis-workers.json")
    for relative in files:
        original, target = HELPERS.target_path(ROOT, relative), HELPERS.target_path(child, relative)
        if original.is_file() and not target.exists():
            HELPERS.copy_public(original, target)
    relative = ".local/real-client-runtime/api-model-source.json"
    original, target = HELPERS.target_path(ROOT, relative), HELPERS.target_path(child, relative)
    if original.is_file() and not target.exists():
        raw = json.loads(original.read_text(encoding="utf-8"))
        if raw.get("version") == 1:
            HELPERS.copy_public(original, target)
        elif raw.get("version") == 2:
            # Release 1.3.0 supports one profile. Copy only the active one, with
            # its encrypted credential unchanged and without decrypting it.
            sys.path.insert(0, str(ROOT / "bridge"))
            try:
                from model_source import ModelSourceStore
                saved = ModelSourceStore(original, root=ROOT).saved_selection()
            finally:
                sys.path.pop(0)
            selected = saved.get("api")
            if not selected:
                if saved["selectedMode"] == "local":
                    return
                raise ValueError("No active API profile is available for the 1.3.0 settings copy")
            fields = ("protocol", "baseUrl", "model", "contextTokens", "encryptedKey")
            document = {"version": 1, "sourceId": saved["sourceId"],
                        "selectedMode": saved["selectedMode"],
                        "api": {key: selected.get(key) for key in fields}}
            HELPERS.atomic_bytes(target, (json.dumps(document, ensure_ascii=False) + "\n").encode())
        else:
            raise ValueError("Unsupported model configuration version; nothing was decrypted")


def prepare():
    HELPERS.plain_path(ROOT)
    child = HELPERS.target_path(ROOT, ".local/test-client-v1.3.0/client")
    marker = {"schema": 1, "mode": "synthetic", "sourceRoot": str(ROOT), "releaseCommit": RELEASE}
    marker_path = HELPERS.target_path(child, ".synthetic-client.json")
    if child.exists():
        if marker_path.is_file():
            if json.loads(marker_path.read_text(encoding="utf-8")) != marker:
                raise ValueError("Existing test directory belongs to a different version")
        elif any(child.iterdir()):
            raise ValueError("Refusing to overwrite an unmarked test directory")
    assert_stopped(child)
    manifest = release_manifest()
    files = sorted(set(HELPERS.public_files(manifest)) | {"package.json"})
    archive = subprocess.check_output(["git", "archive", "--format=tar", RELEASE, *files], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        entries = {item.name: item for item in source.getmembers() if item.isfile()}
        if set(entries) != set(files):
            raise ValueError("The release archive differs from the public file list")
        HELPERS.atomic_bytes(marker_path, (json.dumps(marker, ensure_ascii=False) + "\n").encode())
        for name in files:
            target = "bridge/real_classifier.py" if name == "bridge/chat_server.py" else name
            HELPERS.atomic_bytes(HELPERS.target_path(child, target), source.extractfile(entries[name]).read())
    for origin, target in (
        ("release_synthetic_chat_server.py", "chat_server.py"),
        ("synthetic_fixtures.py", "synthetic_fixtures.py"),
    ):
        HELPERS.copy_public(ROOT / "tests/support" / origin, HELPERS.target_path(child, "bridge/" + target))
    overrides = []
    for name in MBTI_OVERRIDES:
        HELPERS.copy_public(ROOT / name, HELPERS.target_path(child, name))
        overrides.append({"file": name, "sha256": HELPERS.digest(child / name)})
    HELPERS.atomic_bytes(HELPERS.target_path(child, ".mbti-override.json"),
                        (json.dumps({"base": RELEASE, "files": overrides}, indent=2) + "\n").encode())
    seed_configuration(child)
    HELPERS.dependency_link(ROOT / "node_modules", child / "node_modules")
    models = HELPERS.stage_models(ROOT, child, manifest)
    advisor = HELPERS.stage_advisor(ROOT, child)
    return {"root": str(child), "version": "1.3.0", "releaseCommit": RELEASE,
            "mbtiFix": "contextual-evidence-v4",
            "conversations": 5, "publicFiles": len(files), "models": models, "advisor": advisor}


def launch_environment(child):
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("WECHATVIBE_", "WECHATAUTO_", "OPENCODE_"))
                   and key.upper() not in {"CHATUI_PORT", "ELECTRON_RUN_AS_NODE", "PYTHONPATH",
                                           "PYTHONHOME", "NODE_OPTIONS", "NODE_PATH"}}
    temporary = HELPERS.target_path(child, ".local/test-temp")
    temporary.mkdir(parents=True, exist_ok=True)
    environment.update({"WECHATVIBE_CLIENT_ROOT": str(child),
                        "TEMP": str(temporary), "TMP": str(temporary),
                        "WECHATVIBE_PYTHON": str(Path(sys.executable).resolve()),
                        "WECHATVIBE_NODE": shutil.which("node") or "node",
                        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        result = prepare()
        if not args.prepare_only:
            child = Path(result["root"])
            executable = ROOT / "node_modules/electron/dist/electron.exe"
            if not executable.is_file():
                raise ValueError("The installed Electron runtime is missing")
            with (child / ".local/test-desktop.log").open("ab", buffering=0) as output:
                process = subprocess.Popen([str(executable), str(child)], cwd=child,
                    env=launch_environment(child), stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            result["pid"] = process.pid
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"The 1.3.0 test client was not started: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
