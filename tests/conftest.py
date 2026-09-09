"""Test setup: make the repo's packages importable.

`pipecat_demo` is a normal package at the repo root. `agentnexus_mock` lives in
web-demo/, which is not a package (it's a script directory), so its path is added
explicitly -- the memory tests run against that real mock rather than a second copy of
it (see tests/test_memory.py).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

for path in (REPO_ROOT, REPO_ROOT / "web-demo"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
