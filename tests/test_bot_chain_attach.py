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


# ── /rw-overview shares the tree and nothing else (#811) ────────────────────

def test_rw_overview_is_registered_when_its_own_token_is_set(monkeypatch):
    # ⚠️ discord.py allows one CommandTree per client, so /rw-overview attaches
    # to the same tree as /chain. Verify it actually lands there — a command
    # that fails to register produces no error, it is simply absent.
    import chain_runtime, asyncio
    monkeypatch.setenv("WAR_OVERVIEW_TOKEN_FORGE", "y" * 64)
    monkeypatch.setenv("CHAIN_GUILD_IDS", "")
    client = discord.Client(intents=discord.Intents.default())
    rt = chain_runtime.ChainRuntime(client, guild_ids=[])

    async def no_sync(*a, **k):
        return []
    monkeypatch.setattr(rt.tree, "sync", no_sync)
    asyncio.run(rt.setup())

    names = [c.name for c in rt.tree.get_commands()]
    assert "rw-overview" in names
    assert "chain" in names          # and it did not displace Chain Watch


def test_rw_overview_is_absent_without_its_own_token(monkeypatch):
    # ⚠️ Gated independently: a command that can only answer "not configured"
    # is worse than no command — somebody finds it and files a bug against a
    # feature nobody turned on.
    import chain_runtime, asyncio
    for k in list(os.environ):
        if k.startswith("WAR_OVERVIEW_TOKEN_"):
            monkeypatch.delenv(k, raising=False)
    client = discord.Client(intents=discord.Intents.default())
    rt = chain_runtime.ChainRuntime(client, guild_ids=[])

    async def no_sync(*a, **k):
        return []
    monkeypatch.setattr(rt.tree, "sync", no_sync)
    asyncio.run(rt.setup())

    assert "rw-overview" not in [c.name for c in rt.tree.get_commands()]
