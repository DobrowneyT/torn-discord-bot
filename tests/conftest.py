"""
Shared fixtures.

⚠️ `state.py` writes `state.json` next to the module, so every test that touches
settings must redirect it or the suite edits the developer's real bot state —
and on CI, silently accumulates a file nobody looks at.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import state  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    yield
