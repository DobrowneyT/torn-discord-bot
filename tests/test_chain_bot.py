"""
The live bot's I/O adapter (#785).

⚠️ Only the parts that can go wrong without raising. A board that silently
vanishes, or quietly becomes two standing boards, is the kind of failure nobody
reports because it looks like the bot simply stopped mattering.
"""

import asyncio

import discord
import pytest

import chain_bot_sender
import chain_runtime
import chain_settings
import chain_tenants


class FakeMessage:
    def __init__(self, id):
        self.id = id
        self.edits = 0
        self.deleted = False

    async def edit(self, **kwargs):
        self.edits += 1

    async def delete(self):
        self.deleted = True


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
    return chain_bot_sender.DiscordSender(FakeClient(channels))


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
    rt = chain_runtime.ChainRuntime.__new__(chain_runtime.ChainRuntime)
    assert chain_runtime.ChainRuntime.interval(rt) == 120


def test_the_interval_has_a_default_with_no_factions():
    rt = chain_runtime.ChainRuntime.__new__(chain_runtime.ChainRuntime)
    assert chain_runtime.ChainRuntime.interval(rt) == 300


# ── command sync scope ───────────────────────────────────────────────────────

def test_guild_ids_are_read_from_the_environment(monkeypatch):
    # ⚠️ Why this exists: a global sync takes up to an hour to propagate, during
    # which the commands are simply absent with no error anywhere. That hour is
    # spent believing the bot is broken.
    monkeypatch.setenv("CHAIN_GUILD_IDS", "111, 222")
    assert chain_runtime.guild_ids_from_env() == [111, 222]


def test_no_guild_ids_means_a_global_sync(monkeypatch):
    monkeypatch.delenv("CHAIN_GUILD_IDS", raising=False)
    assert chain_runtime.guild_ids_from_env() == []


def test_junk_in_the_guild_list_is_ignored_not_crashed_on(monkeypatch):
    # A stray comma or a pasted channel name must not stop the bot booting.
    monkeypatch.setenv("CHAIN_GUILD_IDS", "111,,not-an-id,222,")
    assert chain_runtime.guild_ids_from_env() == [111, 222]


# ── a 403 must say what to fix (from MonChoon's live setup) ──────────────────
#
# ⚠️ The live failure was `403 Forbidden (50001): Missing Access` repeating
# every five minutes with a full traceback and no indication that the answer was
# one checkbox — View Channel — on one channel. Decoding app_permissions by hand
# is what it took to find that; the bot should do it.

class FakePerms:
    def __init__(self, value):
        self.value = value


class PermChannel(FakeChannel):
    def __init__(self, perms_value, **kw):
        super().__init__(**kw)
        self._perms = perms_value
        self.mention = "#chain-watch"

        class _G:
            me = object()
        self.guild = _G()

    def permissions_for(self, _member):
        return FakePerms(self._perms)


ALL_PERMS = (1 << 10) | (1 << 11) | (1 << 14) | (1 << 16)


def test_a_forbidden_board_names_the_missing_permission(caplog):
    # Everything except View Channel — MonChoon's exact situation.
    ch = PermChannel(ALL_PERMS & ~(1 << 10),
                     send_error=discord.Forbidden(_Resp(403), "Missing Access"))
    with caplog.at_level("ERROR"):
        assert asyncio.run(sender({1: ch}).board(1, [], None)) is None
    msg = caplog.text
    assert "View Channel" in msg
    assert "Send Messages" not in msg, "only the MISSING ones, or the list is noise"
    assert "#chain-watch" in msg


def test_a_forbidden_ping_explains_itself_too(caplog):
    ch = PermChannel(ALL_PERMS & ~(1 << 11),
                     send_error=discord.Forbidden(_Resp(403), "Missing Permissions"))
    with caplog.at_level("ERROR"):
        asyncio.run(sender({1: ch}).say(1, "hi"))
    assert "Send Messages" in caplog.text


def test_a_403_with_every_permission_present_points_somewhere_else(caplog):
    # ⚠️ Category denies, forum channels, announcement channels. Saying
    # "missing: nothing" would be worse than useless.
    ch = PermChannel(ALL_PERMS,
                     send_error=discord.Forbidden(_Resp(403), "Missing Access"))
    with caplog.at_level("ERROR"):
        asyncio.run(sender({1: ch}).board(1, [], None))
    assert "category" in caplog.text


def test_a_forbidden_board_does_not_raise_into_the_tick():
    # ⚠️ It fires every cycle until somebody fixes the channel; it must not
    # take the other factions' boards down while it does.
    ch = PermChannel(0, send_error=discord.Forbidden(_Resp(403), "nope"))
    assert asyncio.run(sender({1: ch}).board(1, [], None)) is None


# ── /chain tidy: the bot's own old messages, and nothing else ───────────────
#
# ⚠️ Why this exists: pings sent before the clean-up feature shipped were never
# tracked, so nothing will ever remove them. Automatic clean-up cannot reach
# them; only a deliberate sweep can.

class Msg:
    def __init__(self, id, author_id):
        self.id = id
        self.author = type("A", (), {"id": author_id})()
        self.deleted = False

    async def delete(self):
        self.deleted = True


class HistoryChannel:
    def __init__(self, messages, forbidden=False):
        self._messages = messages
        self.forbidden = forbidden
        self.mention = "#chain"

        class _G:
            me = object()
        self.guild = _G()
        self.seen_limit = None

    def permissions_for(self, _m):
        return type("P", (), {"value": 0})()

    def history(self, limit=None, before=None):
        self.seen_limit = limit
        outer = self

        class _It:
            def __aiter__(self):
                if outer.forbidden:
                    raise discord.Forbidden(_Resp(403), "no history")
                return self._gen()

            async def _gen(self):
                for m in outer._messages:
                    yield m
        return _It()


BOT_ID = 7


def purger(channel):
    client = FakeClient({1: channel})
    client.user = type("U", (), {"id": BOT_ID})()
    return chain_bot_sender.DiscordSender(client)


def test_tidy_deletes_only_the_bots_own_messages():
    # ⚠️ Deleting anybody else's would be a different and much worse tool, and
    # needs Manage Messages — which this bot deliberately does not hold.
    mine, theirs = Msg(100, BOT_ID), Msg(101, 999)
    ch = HistoryChannel([mine, theirs])
    n = asyncio.run(purger(ch).purge_own(1, before_ms=0, keep_ids=set()))
    assert n == 1
    assert mine.deleted and not theirs.deleted


def test_tidy_never_deletes_the_board():
    # ⚠️ The board lives in the same channel unless the operator split them,
    # and it is old BY DESIGN — edited in place, never re-posted. Nothing else
    # would spare it.
    board, ping = Msg(500, BOT_ID), Msg(501, BOT_ID)
    ch = HistoryChannel([board, ping])
    n = asyncio.run(purger(ch).purge_own(1, before_ms=0, keep_ids={500}))
    assert n == 1
    assert not board.deleted and ping.deleted


def test_tidy_bounds_how_far_back_it_looks():
    # ⚠️ So a stray command cannot walk the whole history of a busy channel.
    ch = HistoryChannel([])
    asyncio.run(purger(ch).purge_own(1, before_ms=0, keep_ids=set(), limit=50))
    assert ch.seen_limit == 50


def test_tidy_survives_having_no_history_permission(caplog):
    ch = HistoryChannel([Msg(1, BOT_ID)], forbidden=True)
    with caplog.at_level("ERROR"):
        n = asyncio.run(purger(ch).purge_own(1, before_ms=0, keep_ids=set()))
    assert n == 0
    assert "tidy up" in caplog.text


def test_tidy_on_an_invisible_channel_is_a_no_op():
    client = FakeClient({})
    client.user = type("U", (), {"id": BOT_ID})()
    n = asyncio.run(chain_bot_sender.DiscordSender(client)
                    .purge_own(999, before_ms=0, keep_ids=set()))
    assert n == 0


# ── posting into a thread (MonChoon, 2026-09-26) ────────────────────────────
#
# ⚠️ Threads auto-archive, and an archived thread refuses writes. The board is
# EDITED rather than re-posted, so a board in a quiet thread silently stops
# updating — no error a reader would see, just a board frozen at whatever it
# last said. The OC watcher carries three strategies for this; the cheapest is
# enough here because the bot holds Manage Threads.

class FakeThread:
    def __init__(self, archived):
        self.archived = archived
        self.id = 77
        self.nudged = False
        self.sent = []

    async def edit(self, **kw):
        # ⚠️ Deliberately does NOT un-archive. The OC watcher tried exactly
        # this (its strategies A and B) and both are commented out in bot.py
        # because they did not work — Discord un-archives on a new MESSAGE.
        pass

    async def send(self, *a, **kw):
        # ⚠️ The wake NUDGE and an ordinary post both call send(); only the
        # nudge carries the single middle dot. Conflating them made a live
        # thread look "woken" by its own board post.
        if kw.get("content") == "\u00b7":
            self.nudged = True
        self.archived = False          # a message is what wakes it
        self.sent.append(kw)
        return FakeMessage(900)

    async def fetch_message(self, mid):
        return FakeMessage(mid)


def test_an_archived_thread_is_re_opened_before_the_board_is_EDITED(monkeypatch):
    # ⚠️ The edit path is the only one that needs it, and the only one that can
    # fail silently: an edit does not un-archive, so a board in a sleeping
    # thread would freeze at whatever it last said.
    th = FakeThread(archived=True)
    monkeypatch.setattr(discord, "Thread", FakeThread)
    client = FakeClient({1: th})
    asyncio.run(chain_bot_sender.DiscordSender(client).board(1, [], 55))
    assert th.nudged, "a board edited in an archived thread would silently freeze"


def test_a_ping_needs_no_nudge_because_posting_wakes_the_thread(monkeypatch):
    # ⚠️ MonChoon's point, and it is the difference between this bot's two
    # jobs: pings are always NEW messages, and a new message un-archives a
    # thread by itself. Nudging first would be a wasted call and a visible
    # flash for nothing.
    th = FakeThread(archived=True)
    monkeypatch.setattr(discord, "Thread", FakeThread)
    client = FakeClient({1: th})
    client.user = type("U", (), {"id": 7})()
    asyncio.run(chain_bot_sender.DiscordSender(client).say(1, "hi"))
    assert not th.nudged, "the ping itself is what wakes the thread"
    assert th.sent and th.archived is False


def test_a_live_thread_is_left_alone(monkeypatch):
    th = FakeThread(archived=False)
    monkeypatch.setattr(discord, "Thread", FakeThread)
    client = FakeClient({1: th})
    asyncio.run(chain_bot_sender.DiscordSender(client).board(1, [], 55))
    assert not th.nudged, "no need to nudge a thread that is already open"


def test_the_first_board_post_needs_no_nudge_either(monkeypatch):
    # ⚠️ Same rule: with no message to edit, the board is POSTED, and posting
    # wakes the thread.
    th = FakeThread(archived=True)
    monkeypatch.setattr(discord, "Thread", FakeThread)
    asyncio.run(chain_bot_sender.DiscordSender(FakeClient({1: th})).board(1, [], None))
    assert not th.nudged
    assert th.sent


def test_an_ordinary_channel_is_never_treated_as_a_thread():
    ch = FakeChannel()
    n = asyncio.run(chain_bot_sender.DiscordSender(FakeClient({1: ch})).board(1, [], None))
    assert n == 901


def test_the_wake_is_a_message_and_not_an_edit(monkeypatch):
    # ⚠️ The correction that matters. The OC watcher tried thread.edit(
    # archived=False) as strategies A and B; both are commented out in bot.py
    # because they did not work. Discord un-archives on a new MESSAGE, and an
    # edit to an existing message is not that — it also fails to bump the
    # thread in the sidebar, so a board edited inside a collapsed thread is a
    # board nobody sees change.
    th = FakeThread(archived=True)
    th.edits = []

    async def record_edit(**kw):
        th.edits.append(kw)

    th.edit = record_edit
    monkeypatch.setattr(discord, "Thread", FakeThread)
    asyncio.run(chain_bot_sender.DiscordSender(FakeClient({1: th})).board(1, [], 55))
    assert th.nudged, "should have posted to wake it"
    assert th.edits == [], "must not rely on thread.edit(archived=False)"


def test_the_wake_nudge_is_removed_again(monkeypatch):
    # ⚠️ Visible for a moment, then gone. Leaving a stray "·" in the channel
    # every time a thread archives would be its own kind of spam.
    th = FakeThread(archived=True)
    monkeypatch.setattr(discord, "Thread", FakeThread)
    # ⚠️ 55, not None: the nudge only happens on the EDIT path, because a post
    # wakes the thread by itself.
    asyncio.run(chain_bot_sender.DiscordSender(FakeClient({1: th})).board(1, [], 55))
    nudges = [m for m in th.sent if m.get("content") == "·"]
    assert len(nudges) == 1
    assert nudges, "and it must be deleted again — see FakeMessage.deleted"
