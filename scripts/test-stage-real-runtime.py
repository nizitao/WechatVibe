"""The runtime stager must not package upstream optional dependencies that the bridge never imports.

`wechatauto-replica` declares `imageio-ffmpeg`, `pyautogui` and `winsdk`. They were skipped only
when missing, so a build host with `imageio-ffmpeg` installed shipped its unused GPL ffmpeg
executable in 1.3.0 and 1.3.5, although THIRD_PARTY_NOTICES describes the package as absent.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


def load(filename: str):
    spec = importlib.util.spec_from_file_location(
        Path(filename).stem.replace("-", "_"), Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load("stage-real-runtime.py")
OPTIONAL = ("imageio-ffmpeg", "pyautogui", "winsdk")


class Distribution:
    def __init__(self, name: str, requires: tuple[str, ...] = ()):
        self.metadata = {"Name": name}
        self.requires = list(requires)
        self.files = ["RECORD"]


class OptionalDependencyTests(unittest.TestCase):
    def test_declared_optional_packages_stay_out_even_when_installed(self):
        installed = {name.lower(): Distribution(name) for name in stage.PYTHON_ROOT_PACKAGES}
        installed["wechatauto-replica"] = Distribution("wechatauto-replica", ("imageio-ffmpeg>=0.4", *OPTIONAL[1:]))
        installed.update({name: Distribution(name) for name in OPTIONAL})
        requested = []

        def distribution(name: str):
            requested.append(name.lower())
            return installed[name.lower()]

        with mock.patch.object(stage.metadata, "distribution", distribution):
            chosen, omitted = stage.python_distributions()
        self.assertEqual(omitted, list(OPTIONAL))
        self.assertEqual(len(chosen), len(stage.PYTHON_ROOT_PACKAGES))
        self.assertFalse(set(OPTIONAL) & {dist.metadata["Name"] for dist in chosen})
        self.assertFalse(set(OPTIONAL) & set(requested))


if __name__ == "__main__":
    unittest.main()
