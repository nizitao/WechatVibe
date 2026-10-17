"""Bridge modules the desktop side runs as `python <path>` must import cleanly
under the bundled Windows Python.

That interpreter uses an isolated ._pth, so unlike a normal CPython it does NOT add
the invoked script's directory to sys.path. 1.3.5 was blocked because
installation_gate.py imported its sibling process_lock.py without adding that
directory, so the packaged shell could never open a window.

Checks:
  1. every entrypoint that imports a sibling anywhere (including imports nested in a
     function body) must restore its own directory before that import;
  2. every entrypoint must survive a pure import probe: runpy executes the module level
     code with a non-main run name under `-P` and with the script directory forced off
     sys.path. That reproduces the bundled isolation while keeping
     `if __name__ == "__main__"` closed, so long-running servers such as chat_server.py
     never start a real service and the suite stays on synthetic data.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge"
SHELLS = sorted(ROOT.glob("scripts/*.cjs")) + sorted(ROOT.glob("scripts/*.py"))
# Only real path joins count: a mention such as "bridge/profile_signals.py" in a doc
# string, or a staging allowlist entry like "backend_contracts.py", is not an execution.
INVOCATIONS = (
    re.compile(r'"bridge",\s*"([A-Za-z_][A-Za-z0-9_]*\.py)"'),
    re.compile(r'"bridge"\s*/\s*"([A-Za-z_][A-Za-z0-9_]*\.py)"'),
)
# Documented user-run tool: the release notes and the acceptance document tell the user to
# start it directly, so it needs the same guarantee as the shell entrypoints.
EXTRA_ENTRYPOINTS = ("payload_recovery.py",)
# The entrypoints restore their directory in two equivalent styles: a module level
# sys.path.insert(0, str(Path(__file__).resolve().parent)), or a variable computed from
# __file__ and inserted just before the sibling import. Both are accepted; what matters
# is that some sys.path insert/append really derives from this file's own location.
# Runs the entrypoint as `python <path>` would, minus the __main__ guard, with the script
# directory forced off sys.path so a missing self-insert is actually caught.
PROBE_DRIVER = (
    "import pathlib, runpy, sys\n"
    "script = pathlib.Path(sys.argv[1]).resolve()\n"
    "sys.path[:] = [p for p in sys.path if pathlib.Path(p or '.').resolve() != script.parent]\n"
    "runpy.run_path(str(script), run_name='__entrypoint_probe__')\n"
)
PROBE_TIMEOUT_SECONDS = 30


def direct_entrypoints() -> list[str]:
    names: set[str] = set()
    for shell in SHELLS:
        text = shell.read_text(encoding="utf-8", errors="ignore")
        for pattern in INVOCATIONS:
            names.update(pattern.findall(text))
    names.update(EXTRA_ENTRYPOINTS)
    return sorted(names)


def restores_own_directory(source: str) -> bool:
    """True when sys.path is extended with a path derived from this file's location."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    from_file: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(child, ast.Name) and child.id == "__file__" for child in ast.walk(node.value)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    from_file.add(target.id)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("insert", "append")):
            continue
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "path" and
                isinstance(owner.value, ast.Name) and owner.value.id == "sys"):
            continue
        for argument in node.args:
            for child in ast.walk(argument):
                if isinstance(child, ast.Name) and (child.id == "__file__" or child.id in from_file):
                    return True
    return False


def sibling_imports(source: str) -> set[str]:
    """Every sibling module imported anywhere, including inside function bodies."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [(node.module or "").split(".")[0]]
        for name in names:
            if name and name != Path(__file__).stem and (BRIDGE / (name + ".py")).is_file():
                found.add(name)
    return found


class ScriptEntrypointTests(unittest.TestCase):
    def test_shells_reference_at_least_one_entrypoint(self) -> None:
        self.assertTrue(direct_entrypoints(), "no directly invoked bridge script was found")

    def test_entrypoints_restore_their_own_directory_before_sibling_imports(self) -> None:
        for name in direct_entrypoints():
            path = BRIDGE / name
            self.assertTrue(path.is_file(), "referenced bridge script is missing: " + name)
            source = path.read_text(encoding="utf-8")
            if sibling_imports(source) and not restores_own_directory(source):
                self.fail(
                    name + " imports a sibling module but never inserts its own directory"
                    " into sys.path; the packaged Python would fail to import it."
                )

    def test_entrypoints_import_without_the_script_directory(self) -> None:
        for name in direct_entrypoints():
            path = BRIDGE / name
            try:
                completed = subprocess.run(
                    [sys.executable, "-P", "-c", PROBE_DRIVER, str(path)],
                    capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, cwd=ROOT,
                )
            except subprocess.TimeoutExpired:
                self.fail(name + " did not finish the import probe within the timeout.")
            combined = (completed.stdout or "") + (completed.stderr or "")
            for failure in ("ModuleNotFoundError", "ImportError"):
                self.assertNotIn(
                    failure, combined,
                    name + " cannot import its siblings without the script directory: "
                    + combined.strip()[-400:],
                )
            # Any other startup error (SyntaxError, a bad constant, a failing top level
            # statement) must fail the probe rather than pass silently.
            self.assertEqual(completed.returncode, 0,
                name + " failed the import probe: " + combined.strip()[-400:])


if __name__ == "__main__":
    unittest.main()
