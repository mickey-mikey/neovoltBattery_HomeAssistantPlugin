"""Pytest configuration — makes ``custom_components`` importable.

The unit tests exercise data models, validators and manager state. The
options-flow regression tests additionally use a real Home Assistant
instance and config-entry lifecycle in a temporary configuration directory.
``pycryptodome`` and ``homeassistant`` are listed in requirements_test.txt;
CI installs them.

The unit modules use ``pytest.importorskip`` in bare developer sandboxes.
The runtime module requires its dependencies and never silently skips them.
"""
from __future__ import annotations

import os
import sys

# HA 2026 installs its supported voluptuous compatibility module on import.
# Let HA initialize that module before individual test files import voluptuous.
# Keep the existing dependency-free test collection behavior when HA is absent.
try:
    import homeassistant  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != "homeassistant":
        raise

# Make ``custom_components.bytewatt`` importable from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
