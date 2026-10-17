"""Prepare and open a source client that only owns synthetic acceptance data.

The isolated tree uses the current reviewed source and the normal desktop entry.
Only authored conversations replace the chat source. Existing assistant templates
and encrypted model settings are seeded explicitly, never personal chat histories.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
MARKER = ".synthetic-client.json"
PROFILE = None
SUPPORT = {
    "tests/support/synthetic_client.py": "bridge/synthetic_client.py",
    "tests/support/synthetic_chat_server.py": "bridge/chat_server.py",
    "bridge/chat_server.py": "bridge/real_classifier.py",
}
# The child gets a fresh environment. In particular this excludes API credentials,
# proxies, OpenCode global configuration, model paths, and update/bridge identities.
ENV_ALLOWLIST = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS",
    "USERNAME", "USERDOMAIN", "OS",
})


def linked(path):
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & 0x400)


def plain_path(path):
    for part in (path, *path.parents):
        if linked(part):
            raise ValueError(f"Test path may not traverse a link: {part.name}")


def target_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe test-client relative path")
    target = root / relative
    plain_path(target)
    return target


def atomic_bytes(target, value):
    plain_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def copy_public(source, target):
    plain_path(source)
    if not source.is_file():
        raise ValueError(f"Missing public test-client input: {source.name}")
    # Replace the directory entry, rather than writing through a pre-existing
    # hardlink that could alias a source file.
    atomic_bytes(target, source.read_bytes())


def module_from_file(path, name):
    plain_path(path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def assert_not_running(child):
    runtime = target_path(child, ".local/real-client-runtime")
    if not runtime.is_dir():
        return
    import psutil
    expected_script = str((child / "bridge/chat_server.py").resolve()).casefold()
    for record in runtime.rglob("bridge-*.json"):
        plain_path(record)
        if record.stat().st_size > 16384:
            raise ValueError("Synthetic bridge record is invalid")
        value = json.loads(record.read_text(encoding="utf-8"))
        pid = value.get("pid")
        if type(pid) is not int or pid <= 0:
            raise ValueError("Synthetic bridge record is invalid")
        try:
            command = psutil.Process(pid).cmdline()
            if len(command) == 2 and str(Path(command[1]).resolve()).casefold() == expected_script:
                raise ValueError("Close the existing test window before refreshing test source files")
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            raise ValueError("Cannot verify that the previous test client has stopped") from error


def public_files(manifest):
    files = set(manifest.PUBLIC_FILES)
    files.update("scripts/" + name for name in manifest.SCRIPTS)
    files.update("bridge/" + name for name in manifest.BRIDGE)
    files.update("electron/laya/" + name for name in manifest.LAYA)
    files.update("native-reader/wr/" + name for name in manifest.NATIVE_READER)
    for name in files:
        parts = Path(name).parts
        if not parts or parts[0] in {".local", "output", "tests", "node_modules", "runtime"}:
            raise ValueError("Public allowlist contains a private or runtime path")
    return sorted(files)


def same_file(left, right):
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return False


def dependency_link(source, target):
    plain_path(source)
    plain_path(target.parent)
    if not source.is_dir():
        raise ValueError("Install source dependencies before starting the test client")
    if os.path.lexists(target):
        if not linked(target) or target.resolve() != source.resolve():
            raise ValueError("Test dependencies do not point to the source dependency directory")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # cmd's built-in mklink is used only with literal, metacharacter-free
        # paths; JSON/string quoting alone would not escape cmd operators.
        if any(char in str(source) + str(target) for char in '&|<>^%!"\r\n'):
            raise ValueError("Dependency path contains unsupported command characters")
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(target), str(source)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError("Unable to create the test-only dependency junction")
    else:
        target.symlink_to(source, target_is_directory=True)
    if target.resolve() != source.resolve():
        raise ValueError("Test dependency link verification failed")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def link_public_binary(source, target):
    plain_path(source)
    plain_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if same_file(source, target):
        return
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        os.link(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def stage_models(source, child, manifest):
    candidates = (
        source / ".models/laya",
        source / "release-real-client/win-unpacked/resources/client/.models/laya",
    )
    for folder in candidates:
        if not folder.is_dir():
            continue
        plain_path(folder)
        checked = []
        for name in manifest.MODEL_FILES:
            original = target_path(folder, name)
            expected_size, expected_hash = manifest.MODEL_PINS[name]
            if (not original.is_file() or original.stat().st_size != expected_size or
                    digest(original) != expected_hash):
                break
            checked.append((original, target_path(child, ".models/laya/" + name)))
        else:
            for original, target in checked:
                link_public_binary(original, target)
            return {"state": "ready", "files": len(checked)}
    existing = target_path(child, ".models/laya")
    if existing.exists() and any(existing.iterdir()):
        raise ValueError("Existing test Laya bundle cannot be refreshed from a verified source; no client was started")
    return {"state": "missing", "message": "No validated local Laya bundle; no download was attempted"}


def stage_advisor(source, child):
    setup = module_from_file(source / "scripts/setup-advisor-engine.py", "synthetic_advisor_setup")
    for folder in (source / "runtime/opencode", source / ".local/advisor-engine"):
        if not folder.is_dir():
            continue
        plain_path(folder)
        try:
            setup.checked_engine(folder)
        except (OSError, ValueError):
            continue
        link_public_binary(folder / "opencode.exe", target_path(child, "runtime/opencode/opencode.exe"))
        copy_public(folder / "manifest.json", target_path(child, "runtime/opencode/manifest.json"))
        return {"state": "ready", "version": setup.VERSION}
    existing = target_path(child, "runtime/opencode")
    if existing.exists() and any(existing.iterdir()):
        raise ValueError("Existing test advisor engine cannot be refreshed from a verified source; no client was started")
    return {"state": "missing", "message": "No verified local advisor engine; no download was attempted"}


def seed_settings(source, child):
    """Preserve real configured templates/settings without importing account data."""
    marker = target_path(child, ".settings-seeded.json")
    if marker.exists():
        return
    allowed = (".local/advisor/config.json",
               ".local/real-client-runtime/api-model-source.json",
               ".local/real-client-runtime/inference-settings.json",
               ".local/real-client-runtime/analysis-workers.json",
               ".local/real-client-runtime/api-workers.json")
    copied = []
    for relative in allowed:
        original = target_path(source, relative)
        if not original.is_file():
            continue
        value = json.loads(original.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("The source settings are not a JSON object")
        target = target_path(child, relative)
        if target.exists():
            backup = target_path(child, ".local/settings-before-integration/" + relative.removeprefix(".local/"))
            if not backup.exists():
                copy_public(target, backup)
        copy_public(original, target)
        copied.append(relative)
    atomic_bytes(marker, (json.dumps({"schema": 1, "files": copied}, indent=2) + "\n").encode())


def prepare(source=SOURCE_ROOT, *, manifest=None, include_dependencies=True, include_models=True,
            include_advisor=True):
    source = Path(source).absolute()
    plain_path(source)
    child = source / ".local/test-client/client"
    plain_path(child)
    marker = {"schema": 1, "mode": "synthetic", "sourceRoot": str(source)}
    marker_path = target_path(child, MARKER)
    if child.exists():
        if marker_path.exists():
            if json.loads(marker_path.read_text(encoding="utf-8")) != marker:
                raise ValueError("Existing test-client identity does not match this source checkout")
        elif any(child.iterdir()):
            raise ValueError("Refusing to overwrite an unmarked test directory")
    assert_not_running(child)
    child.mkdir(parents=True, exist_ok=True)
    manifest = manifest or module_from_file(source / "scripts/stage-real-client.py", "synthetic_public_stage")
    mappings = {name: name for name in public_files(manifest)}
    # Keep the real classifier, replace only the staged server entry point.
    mappings.pop("bridge/chat_server.py", None)
    for origin, destination in SUPPORT.items():
        mappings[origin] = destination
    for origin, destination in mappings.items():
        original = target_path(source, origin)
        if not original.is_file():
            raise ValueError(f"Missing public test-client input: {origin}")
        plain_path(original)
        target_path(child, destination)
    atomic_bytes(marker_path, (json.dumps(marker, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for origin, destination in mappings.items():
        copy_public(source / origin, target_path(child, destination))
    copy_public(source / "package.json", target_path(child, "package.json"))
    seed_settings(source, child)
    if include_dependencies:
        dependency_link(source / "node_modules", child / "node_modules")
    models = stage_models(source, child, manifest) if include_models else {"state": "skipped"}
    advisor = stage_advisor(source, child) if include_advisor else {"state": "skipped"}
    return {"root": str(child), "mode": "synthetic", "source": "current-integration", "profile": PROFILE,
            "copiedFiles": len(mappings) + 2, "models": models, "advisor": advisor}


def launch_environment(child, inherited=None, *, python=None, node=None):
    inherited = os.environ if inherited is None else inherited
    child = Path(child)
    environment = {key: value for key, value in inherited.items() if key.upper() in ENV_ALLOWLIST}
    os_root = child / ".local/test-os"
    locations = {"TEMP": os_root / "temp", "TMP": os_root / "temp",
                 "LOCALAPPDATA": os_root / "Local", "APPDATA": os_root / "Roaming",
                 "USERPROFILE": os_root / "user", "HOME": os_root / "user"}
    for key, directory in locations.items():
        target_path(child, directory.relative_to(child)).mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    environment.update({"WECHATVIBE_CLIENT_ROOT": str(child),
                        "WECHATVIBE_SYNTHETIC": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
                        "WECHATVIBE_PYTHON": str(python or Path(sys.executable).resolve()),
                        "WECHATVIBE_NODE": str(node or shutil.which("node") or "node")})
    return environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="refresh isolated files without opening a window")
    args = parser.parse_args(argv)
    try:
        result = prepare()
        if not args.prepare_only:
            child = Path(result["root"])
            executable = SOURCE_ROOT / "node_modules/electron/dist/electron.exe"
            if not executable.is_file():
                raise ValueError("Source Electron runtime is missing; install project dependencies first")
            environment = launch_environment(child)
            log_path = child / ".local/test-desktop.log"
            with log_path.open("ab", buffering=0) as output:
                process = subprocess.Popen([str(executable), str(child)], cwd=child, env=environment,
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            result["pid"] = process.pid
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        parser.exit(1, f"Test client was not started: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
