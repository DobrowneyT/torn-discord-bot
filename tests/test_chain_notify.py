"""
Dashboard nudges and the debounce (#785 follow-up).

⚠️ The debounce is the whole reason this exists rather than just polling faster.
Leadership filling a rota assigns eight slots in twenty seconds; without
coalescing that is eight redraws, eight Discord edits, and a board that flickers
while somebody works. These tests are all about "how many redraws happened".
"""

import asyncio
import json
import os

import aiohttp
import pytest

import chain_notify


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


class Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, slug):
        self.calls.append(slug)


# ── the debounce ─────────────────────────────────────────────────────────────

def test_a_clump_of_nudges_becomes_one_redraw(loop):
    rec = Recorder()
    server = chain_notify.NotifyServer(rec, path=None, debounce_seconds=0.05)

    async def go():
        for _ in range(8):          # leadership filling a rota
            server.nudge("forge")
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.15)

    loop.run_until_complete(go())
    assert rec.calls == ["forge"], "eight sign-ups must be one redraw"


def test_a_steady_trickle_redraws_once_after_it_stops(loop):
    # ⚠️ Restart-on-nudge, not fire-every-window: a sustained stream of changes
    # should settle into ONE redraw when it ends, not one per window forever.
    rec = Recorder()
    server = chain_notify.NotifyServer(rec, path=None, debounce_seconds=0.06)

    async def go():
        for _ in range(6):
            server.nudge("forge")
            await asyncio.sleep(0.03)   # always inside the window
        assert rec.calls == [], "must not have fired while changes kept arriving"
        await asyncio.sleep(0.12)

    loop.run_until_complete(go())
    assert rec.calls == ["forge"]


def test_separate_factions_debounce_independently(loop):
    # ⚠️ One faction's busy evening must not delay another's board.
    rec = Recorder()
    server = chain_notify.NotifyServer(rec, path=None, debounce_seconds=0.05)

    async def go():
        server.nudge("forge")
        server.nudge("tnl")
        await asyncio.sleep(0.15)

    loop.run_until_complete(go())
    assert sorted(rec.calls) == ["forge", "tnl"]


def test_a_failing_redraw_does_not_kill_later_ones(loop):
    # ⚠️ It runs in a bare task; an escaping exception is an unretrievable
    # warning on stderr and a redraw that silently stopped happening.
    calls = []

    async def flaky(slug):
        calls.append(slug)
        raise RuntimeError("discord is down")

    server = chain_notify.NotifyServer(flaky, path=None, debounce_seconds=0.03)

    async def go():
        server.nudge("forge")
        await asyncio.sleep(0.08)
        server.nudge("forge")
        await asyncio.sleep(0.08)

    loop.run_until_complete(go())
    assert calls == ["forge", "forge"]


# ── the socket ───────────────────────────────────────────────────────────────

def test_end_to_end_over_a_real_unix_socket(loop, tmp_path):
    rec = Recorder()
    path = str(tmp_path / "notify.sock")
    server = chain_notify.NotifyServer(rec, path=path, debounce_seconds=0.03)

    async def go():
        assert await server.start() is True
        conn = aiohttp.UnixConnector(path=path)
        async with aiohttp.ClientSession(connector=conn) as s:
            async with s.post("http://localhost/changed",
                              json={"slug": "forge"}) as r:
                assert (await r.json())["ok"] is True
        await asyncio.sleep(0.1)
        await server.stop()

    loop.run_until_complete(go())
    assert rec.calls == ["forge"]


def test_junk_is_answered_200_and_ignored(loop, tmp_path):
    # ⚠️ 200 even for nonsense. An error status gives the dashboard something to
    # retry, and retrying a redraw nudge is never worth a member's request time.
    rec = Recorder()
    path = str(tmp_path / "notify.sock")
    server = chain_notify.NotifyServer(rec, path=path, debounce_seconds=0.02)

    async def go():
        await server.start()
        conn = aiohttp.UnixConnector(path=path)
        async with aiohttp.ClientSession(connector=conn) as s:
            async with s.post("http://localhost/changed", data="not json") as r:
                assert r.status == 200
                assert (await r.json())["ok"] is False
            async with s.post("http://localhost/changed", json={}) as r:
                assert (await r.json())["ok"] is False
        await asyncio.sleep(0.06)
        await server.stop()

    loop.run_until_complete(go())
    assert rec.calls == []


def test_a_stale_socket_file_is_replaced_rather_than_disabling_push(loop, tmp_path):
    # ⚠️ An unclean shutdown leaves the file behind; bind() then fails with
    # EADDRINUSE and the bot starts with push silently off FOREVER.
    path = str(tmp_path / "notify.sock")
    open(path, "w").close()
    server = chain_notify.NotifyServer(Recorder(), path=path, debounce_seconds=0.02)

    async def go():
        assert await server.start() is True
        await server.stop()

    loop.run_until_complete(go())


def test_no_socket_configured_is_not_an_error(loop):
    # Push is an optimisation; its absence is the old behaviour, not a failure.
    server = chain_notify.NotifyServer(Recorder(), path=None)
    assert loop.run_until_complete(server.start()) is False


def test_an_unusable_path_falls_back_to_polling(loop):
    server = chain_notify.NotifyServer(
        Recorder(), path="/proc/cannot/exist/notify.sock", debounce_seconds=0.02)
    assert loop.run_until_complete(server.start()) is False
