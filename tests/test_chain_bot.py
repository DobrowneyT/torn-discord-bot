"""
The live bot's I/O adapter (#785).

⚠️ Only the parts that can go wrong without raising. A board that silently
vanishes, or quietly becomes two standing boards, is the kind of failure nobody
reports because it looks like the bot simply stopped mattering.
"""

import asyncio

import discord
import pytest

import chain_bot
import chain_settings
import chain_tenants


class FakeMessage:
    def __init__(self, id):
        self.id = id
        self.edits = 0

    async def edit(self, **kwargs):
        self.edits += 1


class FakeChannel:
    def __init__(self, existing=None, fetch_error=None, send_error=None):
        self.existing = existing
        self.fetch_error = fetch_error
        self.send_error = send_error
        self.sent = []
        self.next_id = 900

    async def fetch_message(self, message_id):
        if self.fetch_error:
            raise self.fetch_error
        return self.existing

    async def send(self, *args, **kwargs):
        if self.send_error:
            raise self.send_error
        self.next_id += 1
        self.sent.append((args, kwargs))
        return FakeMessage(self.next_id)


class FakeClient:
    def __init__(self, channels):
        self._channels = channels

    def get_channel(self, cid):
        return self._channels.get(cid)


def sender(channels):
    return chain_bot.DiscordSender(FakeClient(channels))


def test_posts_when_there_is_no_message_yet():
    ch = FakeChannel()
    assert asyncio.run(sender({1: ch}).board(1, [], None)) == 901
    assert len(ch.sent) == 1


def test_edits_in_place_rather_than_re_posting():
    msg = FakeMessage(55)
    ch = FakeChannel(existing=msg)
    assert asyncio.run(sender({1: ch}).board(1, [], 55)) == 55
    assert msg.edits == 1 and ch.sent == []


def test_a_deleted_board_is_re_posted_rather_than_going_dark():
    # ⚠️ A board that vanishes is indistinguishable from a bot that died.
    ch = FakeChannel(fetch_error=discord.NotFound(_Resp(404), "gone"))
    assert asyncio.run(sender({1: ch}).board(1, [], 55)) == 901
    assert len(ch.sent) == 1


def test_a_transient_edit_failure_keeps_the_message_id():
    # ⚠️ Forgetting it would make the next tick post a SECOND standing board,
    # and then a third — each one edited forever afterwards.
    ch = FakeChannel(existing=FakeMessage(55),
                     fetch_error=discord.HTTPException(_Resp(500), "boom"))
    assert asyncio.run(sender({1: ch}).board(1, [], 55)) == 55
    assert ch.sent == []


def test_an_invisible_channel_is_survivable():
    assert asyncio.run(sender({}).board(999, [], None)) is None
    asyncio.run(sender({}).say(999, "hi"))          # must not raise


def test_a_failed_ping_does_not_raise_into_the_tick():
    # ⚠️ One undeliverable ping must not stop the other factions' boards.
    ch = FakeChannel(send_error=discord.HTTPException(_Resp(403), "no"))
    asyncio.run(sender({1: ch}).say(1, "hi"))


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "test"


# ── the loop interval ────────────────────────────────────────────────────────

def test_the_interval_is_the_shortest_across_factions():
    chain_tenants.add("forge", "https://f.example.com", 1, 10)
    chain_tenants.add("tnl", "https://t.example.com", 2, 20)
    chain_settings.set_value("forge", "board_refresh_seconds", "120")
    chain_settings.set_value("tnl", "board_refresh_seconds", "600")
    bot = chain_bot.ChainBot.__new__(chain_bot.ChainBot)
    assert chain_bot.ChainBot._interval(bot) == 120


def test_the_interval_has_a_default_with_no_factions():
    bot = chain_bot.ChainBot.__new__(chain_bot.ChainBot)
    assert chain_bot.ChainBot._interval(bot) == 300


# ── command sync scope ───────────────────────────────────────────────────────

def test_guild_ids_are_read_from_the_environment(monkeypatch):
    # ⚠️ Why this exists: a global sync takes up to an hour to propagate, during
    # which the commands are simply absent with no error anywhere. That hour is
    # spent believing the bot is broken.
    monkeypatch.setenv("CHAIN_GUILD_IDS", "111, 222")
    assert chain_bot.guild_ids_from_env() == [111, 222]


def test_no_guild_ids_means_a_global_sync(monkeypatch):
    monkeypatch.delenv("CHAIN_GUILD_IDS", raising=False)
    assert chain_bot.guild_ids_from_env() == []


def test_junk_in_the_guild_list_is_ignored_not_crashed_on(monkeypatch):
    # A stray comma or a pasted channel name must not stop the bot booting.
    monkeypatch.setenv("CHAIN_GUILD_IDS", "111,,not-an-id,222,")
    assert chain_bot.guild_ids_from_env() == [111, 222]
