"""
Pytest configuration for the repository root.

Puts the repo root on sys.path so tests under tests/ can import the top-level
modules (transcribe_core, service, ...) no matter how pytest is started. Without
this, `pytest tests/` collects from the tests/ directory only, so the root
modules are not importable and collection fails; just `python -m pytest` would
happen to work because it adds the current directory. This makes both behave the
same, locally and in CI.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
