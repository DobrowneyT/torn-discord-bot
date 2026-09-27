"""
Chain Watch riding on the existing OC bot (#785, follow-up).

⚠️ What these defend is the OC watcher, not Chain Watch. It has been running for
months; Chain Watch is what changed. Every failure mode below ends with the OC
watcher still working.
"""

import os

import discord
import pytest

import bot as bot_mod
import chain_runtime


def make(chain=False):
    """Build an OCWatcher without touching the gateway."""
    return bot_mod.OCWatcher(channel_id=1, api_key="k", chain=chain)


def test_chain_is_off_unless_asked_for():
    # ⚠️ Adding this module to a running bot must change nothing by itself.
    assert make().chain is None


def test_chain_attaches_to_the_same_client_not_a_second_one():
    # ⚠️ The whole point. Two processes on one token open two gateway
    # connections and Discord routes each interaction to only one — the
    # override button would silently stop working about half the time.
    b = make(chain=True)
    assert b.chain is not None
    assert b.chain.client is b


def test_the_members_intent_is_only_requested_when_chain_is_on():
    # It is a privileged intent; asking for it when unused means the bot stops
    # booting the day somebody tightens the portal settings.
    assert make().intents.members is False
    assert make(chain=True).intents.members is True


def test_the_override_view_is_still_registered_with_chain_on():
    b = make(chain=True)
    assert b.view is not None
    assert b.view.children, "the persistent override button must survive"


# ── the opt-in gate ──────────────────────────────────────────────────────────

def test_a_configured_token_is_what_turns_chain_on(monkeypatch):
    for k in list(os.environ):
        if k.startswith("CHAIN_WATCH_TOKEN_"):
            monkeypatch.delenv(k, raising=False)
    assert chain_runtime.chain_enabled() is False
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "abc")
    assert chain_runtime.chain_enabled() is True


def test_an_empty_token_does_not_count(monkeypatch):
    # ⚠️ A commented-out or blanked line in .env reads as "not configured",
    # rather than starting a poller that 401s every five minutes forever.
    for k in list(os.environ):
        if k.startswith("CHAIN_WATCH_TOKEN_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "")
    assert chain_runtime.chain_enabled() is False


def test_guild_ids_parse_the_same_way_from_either_host(monkeypatch):
    monkeypatch.setenv("CHAIN_GUILD_IDS", "111, ,nope,222")
    assert chain_runtime.guild_ids_from_env() == [111, 222]
