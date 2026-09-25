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


# ⚠️ A fake stands in for I/O, never for an API's SHAPE. `attach` called
# `client.add_listener(...)` — a `commands.Bot` method that a plain
# `discord.Client` does not have — and the suite passed because the fake client
# had one only because it was written that way. It failed the moment it met a
# real client. Anything that touches discord.py's API surface is exercised
# against the real class.


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    yield
