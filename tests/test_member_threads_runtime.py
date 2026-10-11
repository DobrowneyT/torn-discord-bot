"""
The member-report drain loop (#943).

⚠️ What matters here is ISOLATION. Five factions share one bot process that also
runs the OC watcher, and the failure this guards against is one faction's
misconfiguration silencing the rest.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chain_tenants  # noqa: E402
import member_threads_poster as poster  # noqa: E402
import member_threads_runtime as runtime  # noqa: E402


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def tenants(*slugs):
    return [MagicMock(slug=s) for s in slugs]


class TestTheInterval:
    def test_defaults_to_two_minutes(self):
        with patch.dict(os.environ, {}, clear=True):
            assert runtime.interval() == 120

    def test_is_floored_because_claim_is_a_write(self):
        # ⚠️ A one-second poll across five tenants is a self-inflicted denial of
        # service on the dashboards, and `claim` mutates rows.
        with patch.dict(os.environ, {runtime.INTERVAL_ENV: "1"}):
            assert runtime.interval() == 30

    def test_is_capped(self):
        with patch.dict(os.environ, {runtime.INTERVAL_ENV: "999999"}):
            assert runtime.interval() == 3600

    def test_nonsense_falls_back_rather_than_crashing_the_loop(self):
        with patch.dict(os.environ, {runtime.INTERVAL_ENV: "soon"}):
            assert runtime.interval() == 120


class TestTheGate:
    def test_off_when_no_faction_has_a_forum(self):
        # ⚠️ Gated on a forum, NOT on a token. The control plane mints a token
        # for every tenant, so gating on that would switch this on everywhere
        # the moment the registry answers.
        with patch.object(chain_tenants, "all_tenants", return_value=tenants("forge", "tnl")), \
             patch.object(poster, "forum_id", return_value=0):
            assert runtime.enabled() is False

    def test_on_when_one_faction_has_one(self):
        with patch.object(chain_tenants, "all_tenants", return_value=tenants("forge", "tnl")), \
             patch.object(poster, "forum_id", side_effect=lambda s: 99 if s == "tnl" else 0):
            assert runtime.enabled() is True


class TestIsolation:
    def test_one_faction_blowing_up_does_not_stop_the_others(self):
        # ⚠️ THE test. One try around the whole sweep would mean the first
        # failing faction silences every faction after it in the list — a bug
        # that is also impossible to notice.
        seen = []

        async def drain(_client, slug):
            seen.append(slug)
            if slug == "tnl":
                raise RuntimeError("tnl's dashboard is on fire")
            return {"claimed": 0, "posted": 0, "failed": 0}

        with patch.object(chain_tenants, "all_tenants",
                          return_value=tenants("forge", "tnl", "mayhem")), \
             patch.object(poster, "drain_once", drain):
            run(runtime.MemberThreadsRuntime(MagicMock()).tick_all())

        assert seen == ["forge", "tnl", "mayhem"]

    def test_a_tick_that_raises_does_not_kill_the_loop(self):
        client = MagicMock()
        # Closed after one pass, so the loop exits rather than spinning.
        client.is_closed.side_effect = [False, True]
        with patch.object(runtime.MemberThreadsRuntime, "tick_all",
                          AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(runtime.asyncio, "sleep", AsyncMock()):
            # Must return normally: an unhandled error here would stop every
            # faction's reports silently and for ever.
            run(runtime.MemberThreadsRuntime(client)._run())

    def test_quiet_ticks_are_not_logged(self):
        # A log line every two minutes per faction for "nothing to do" buries
        # the lines that matter.
        with patch.object(chain_tenants, "all_tenants", return_value=tenants("forge")), \
             patch.object(poster, "drain_once",
                          AsyncMock(return_value={"claimed": 0, "posted": 0, "failed": 0})), \
             patch.object(runtime.log, "info") as info:
            run(runtime.MemberThreadsRuntime(MagicMock()).tick_all())
        info.assert_not_called()

    def test_a_tick_that_did_something_IS_logged(self):
        with patch.object(chain_tenants, "all_tenants", return_value=tenants("forge")), \
             patch.object(poster, "drain_once",
                          AsyncMock(return_value={"claimed": 1, "posted": 1, "failed": 0})), \
             patch.object(runtime.log, "info") as info:
            run(runtime.MemberThreadsRuntime(MagicMock()).tick_all())
        info.assert_called_once()
