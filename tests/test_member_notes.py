"""
Membership notes — joined and left (#942).

⚠️ **These could never post.** The drain was written for #943's war reports and
refused anything without a `war_id`, so every join and leave note failed on
every tick for ever. Nobody noticed until a new member joined forge and his
thread never appeared — the whole of #942, shipped and inert.

The lesson is in `test_every_dashboard_kind_can_be_rendered`: the dashboard
queued three kinds and the bot handled one, and nothing anywhere compared the
two lists.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import member_note_format as notes  # noqa: E402
import member_threads_poster as poster  # noqa: E402


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def entry(**over):
    e = {"id": 107, "member_id": "3750187", "kind": "joined",
         "payload": {"stint_id": 134, "member_name": "Noellee", "at": 1_791_741_901_300}}
    e.update(over)
    return e


@pytest.fixture
def forum():
    f = MagicMock()
    f.id = 555
    f.guild = MagicMock()
    created = MagicMock()
    created.thread = MagicMock(id=700)
    created.message = MagicMock(id=800)
    f.create_thread = AsyncMock(return_value=created)
    return f


class TestTheKindsMatch:
    def test_every_dashboard_kind_can_be_rendered(self):
        # ⚠️ THE test that was missing. `OUTBOX_KINDS` in
        # server/src/memberThreads.ts has three entries; this bot handled one.
        # A kind in one list and not the other is a note that queues and never
        # posts — silent, permanent, and invisible until somebody asks why a
        # thread never appeared.
        dashboard_kinds = {"joined", "left", "war_report"}
        handled = set(notes.KINDS) | {"war_report"}
        assert dashboard_kinds == handled, (
            "a kind the dashboard queues has no handler in the bot")

    def test_an_unknown_kind_renders_nothing_rather_than_inventing_text(self):
        # A note nobody designed, in somebody's permanent record, is worse than
        # one that fails loudly and gets noticed.
        assert notes.note_line("promoted", {"member_name": "X"}) is None


class TestTheLine:
    def test_a_join_names_the_member_and_the_moment(self):
        line = notes.note_line("joined", {"member_name": "Noellee", "at": 1_791_741_901_300})
        assert "Noellee" in line and "joined the faction" in line
        # ⚠️ Discord's own timestamp, so it renders in each reader's timezone.
        assert "<t:1791741901:f>" in line

    def test_a_leave_says_left(self):
        line = notes.note_line("left", {"member_name": "Gone", "at": 1_791_741_901_300})
        assert "left the faction" in line and "joined" not in line

    def test_it_prefers_the_EVENT_time_over_the_queue_time(self):
        # ⚠️ `queued_ms` is when the sweep noticed — its five-minute cadence at
        # best, and arbitrarily late after a restart.
        line = notes.note_line(
            "joined", {"member_name": "N", "at": 1_000_000_000_000},
            queued_ms=1_791_741_901_300)
        assert "<t:1000000000:f>" in line

    def test_it_falls_back_to_the_queue_time_for_older_rows(self):
        # Notes queued before the dashboard sent `at` still have to post.
        line = notes.note_line("joined", {"member_name": "N"}, queued_ms=1_791_741_901_300)
        assert "<t:1791741901:f>" in line

    def test_no_date_at_all_beats_a_wrong_one(self):
        line = notes.note_line("joined", {"member_name": "N"})
        assert "<t:" not in line
        assert line.endswith("joined the faction.")

    def test_a_missing_name_falls_back_to_the_id(self):
        assert "id 42" in notes.note_line("joined", {}, member_id=42)


class TestPosting:
    def test_a_NEW_member_gets_a_thread_created(self, forum):
        # ⚠️ The case that exposed the bug: a join is what creates the thread.
        with patch.object(poster.api, "mark_posted") as posted:
            ok, detail = run(poster.post_one(MagicMock(), "forge", entry(), {}, forum))
        assert ok and "created thread" in detail
        forum.create_thread.assert_awaited_once()
        kw = forum.create_thread.call_args.kwargs
        assert "[3750187]" in kw["name"]
        assert "joined the faction" in kw["content"]
        posted.assert_called_once()

    def test_an_existing_member_gets_the_note_appended(self, forum):
        thread = MagicMock(id=700, archived=False, parent_id=555)
        thread.name = "Noellee [3750187]"
        thread.send = AsyncMock(return_value=MagicMock(id=801))
        forum.guild.get_thread.return_value = thread
        with patch.object(poster.api, "mark_posted"):
            ok, detail = run(poster.post_one(
                MagicMock(), "forge", entry(kind="left"),
                {"3750187": {"thread_id": 700}}, forum))
        assert ok and "noted left" in detail
        forum.create_thread.assert_not_called()

    def test_a_note_is_PLAIN_TEXT_not_an_embed(self, forum):
        # ⚠️ A note is a log line. A join note dressed as a report would compete
        # with the war reports around it while saying far less.
        with patch.object(poster.api, "mark_posted"):
            run(poster.post_one(MagicMock(), "forge", entry(), {}, forum))
        assert "embed" not in forum.create_thread.call_args.kwargs

    def test_it_does_NOT_demand_a_war_id(self, forum):
        # The production bug, exactly: "entry has no war_id in its payload",
        # failing every tick for ever.
        with patch.object(poster.api, "mark_posted"), \
             patch.object(poster.api, "mark_failed") as failed:
            ok, _ = run(poster.post_one(MagicMock(), "forge", entry(), {}, forum))
        assert ok
        failed.assert_not_called()

    def test_a_truly_unknown_kind_still_fails_loudly(self, forum):
        ok, detail = run(poster.post_one(
            MagicMock(), "forge", entry(kind="promoted", payload={}), {}, forum))
        assert not ok and "unknown outbox kind" in detail
