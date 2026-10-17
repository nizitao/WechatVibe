#!/usr/bin/env python3
"""Staged test-client entry point. No production-data fallback is supported."""
from pathlib import Path
import sys

bridge_dir = str(Path(__file__).resolve().parent)
if bridge_dir not in sys.path:
    sys.path.insert(0, bridge_dir)

from real_classifier import classify
from synthetic_client import main

if __name__ == "__main__":
    raise SystemExit(main(classify))
