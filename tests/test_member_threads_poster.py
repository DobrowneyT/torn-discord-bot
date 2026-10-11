"""
Posting one member's report into their thread (#943).

⚠️ The rules worth testing here are the ones whose failure is QUIET and
destructive: claiming rows we cannot post (which burns the ten-minute window
every tick), recreating a thread that was deleted (which destroys a running log
while looking like success), and posting a second embed for a war that already
has one.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rw_settings  # noqa: E402
import member_threads_poster as poster  # noqa: E402


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


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


@pytest.fixture
def facts():
    return {
        "war": {"id": 42}, "member": {"id": 101, "name": "Someone"},
        "score": None, "summary": None,
        "dibs": {"state": "disabled", "counts": None},
        "roll_call": None, "stacks": None,
    }


def fake_thread(tid=700, *, archived=False, member_id="101", parent=555, name=None):
    """
    ⚠️ `name` must be set as an ATTRIBUTE, not a MagicMock kwarg — `name` is
    consumed by the mock's own constructor, so `MagicMock(name="x")` names the
    mock and leaves `.name` a child mock. That is what made the first version of
    these fixtures fail the identity check.
    """
    t = MagicMock(id=tid, archived=archived, parent_id=parent)
    t.name = name if name is not None else f"Someone [{member_id}]"
    t.send = AsyncMock(return_value=MagicMock(id=801))
    t.edit = AsyncMock()
    return t


def entry(**over):
    e = {"id": 1, "member_id": "101", "kind": "war_report",
         "payload": {"war_id": 42, "member_name": "Someone"}}
    e.update(over)
    return e


class TestTheForumGate:
    def test_unset_forum_claims_nothing(self):
        # ⚠️ THE one that matters. Claiming and then failing every entry would
        # burn the ten-minute window on every tick and fill `last_error` with a
        # message about our own configuration, on rows that are perfectly fine.
        with patch.object(poster, "_resolve_forum", AsyncMock(return_value=None)), \
             patch.object(poster.api, "claim") as claim:
            counts = run(poster.drain_once(MagicMock(), "forge"))
        claim.assert_not_called()
        assert counts == {"claimed": 0, "posted": 0, "failed": 0}

    def test_a_text_channel_is_refused_rather_than_posted_into(self):
        # A text channel would accept posts and produce no threads, so every
        # member's "running log" would be one flat stream in a shared channel.
        client = MagicMock()
        client.get_channel.return_value = MagicMock(spec=discord.TextChannel)
        # ⚠️ Patched on `rw_settings`, which is what the poster reads since
        # #944. Patching `chain_settings` here left this test passing for the
        # wrong reason: the real unset value short-circuited before the type
        # check it claims to exercise ever ran.
        with patch.object(rw_settings, "get", return_value=999), \
             patch.object(rw_settings, "adopt_from_chain", return_value=False):
            assert run(poster._resolve_forum(client, "forge")) is None

    def test_unset_is_zero_not_a_fallback(self):
        with patch.object(rw_settings, "get", return_value=0), \
             patch.object(rw_settings, "adopt_from_chain", return_value=False):
            assert poster.forum_id("forge") == 0
            assert run(poster._resolve_forum(MagicMock(), "forge")) is None


class TestADeletedThread:
    def test_is_never_recreated(self, forum, facts):
        # ⚠️ MonChoon: "fail loudly if something gets deleted, renamed." A fresh
        # empty thread claiming to be somebody's running log destroys the
        # history while looking like success.
        forum.guild.get_thread.return_value = None
        forum.guild.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        threads = {"101": {"thread_id": 700}}
        with patch.object(poster.api, "report", return_value=facts):
            with pytest.raises(poster.ThreadGone):
                run(poster.post_one(MagicMock(), "forge", entry(), threads, forum))
        forum.create_thread.assert_not_called()

    def test_drain_releases_the_entry_and_says_so(self, forum, facts):
        forum.guild.get_thread.return_value = None
        forum.guild.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        with patch.object(poster, "_resolve_forum", AsyncMock(return_value=forum)), \
             patch.object(poster.api, "claim", return_value={
                 "pending": [entry()], "threads": {"101": {"thread_id": 700}}}), \
             patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_failed") as failed:
            counts = run(poster.drain_once(MagicMock(), "forge"))
        assert counts["failed"] == 1 and counts["posted"] == 0
        # The reason reaches the dashboard, not just the log.
        assert "thread gone" in failed.call_args[0][2]
        forum.create_thread.assert_not_called()


class TestARepurposedThread:
    """
    ⚠️ #944's "renamed" case, and the reasoning that makes it a rule.

    A rename does not break the id lookup, so a renamed thread keeps working —
    and it must, because a Torn name change is a legitimate reason to rename
    one. What must NOT happen is posting member A's performance into a thread
    somebody has repurposed for member B: that is a permanent, named record
    about the wrong person, in a channel leadership reads.
    """

    def test_a_rename_that_keeps_the_id_is_fine(self, forum, facts):
        forum.guild.get_thread.return_value = fake_thread(name="NewTornName [101]")
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"):
            ok, _ = run(poster.post_one(
                MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))
        assert ok

    def test_a_rename_that_DROPS_the_id_is_refused(self, forum, facts):
        thread = fake_thread(name="old stuff - reuse me")
        forum.guild.get_thread.return_value = thread
        with patch.object(poster.api, "report", return_value=facts):
            with pytest.raises(poster.ThreadMismatch, match="101"):
                run(poster.post_one(
                    MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))
        thread.send.assert_not_called()
        forum.create_thread.assert_not_called()

    def test_a_thread_repurposed_for_ANOTHER_member_is_refused(self, forum, facts):
        # The failure this whole check exists for.
        forum.guild.get_thread.return_value = fake_thread(name="SomebodyElse [999]")
        with patch.object(poster.api, "report", return_value=facts):
            with pytest.raises(poster.ThreadMismatch):
                run(poster.post_one(
                    MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))

    def test_a_thread_MOVED_to_another_channel_is_refused(self, forum, facts):
        # ⚠️ Its id still resolves, so posting would SUCCEED — into a forum
        # nobody chose, publishing roll-call and dibs data to an unintended
        # audience.
        forum.guild.get_thread.return_value = fake_thread(parent=999)
        with patch.object(poster.api, "report", return_value=facts):
            with pytest.raises(poster.ThreadMismatch, match="999"):
                run(poster.post_one(
                    MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))

    def test_the_identity_check_runs_AFTER_unarchiving(self, forum, facts):
        # Otherwise an archived-but-perfectly-fine thread reads as a mismatch.
        thread = fake_thread(archived=True)
        forum.guild.get_thread.return_value = thread
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"):
            ok, _ = run(poster.post_one(
                MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))
        assert ok
        thread.edit.assert_awaited_once_with(archived=False)

    def test_drain_reports_it_loudly_and_does_not_recreate(self, forum, facts):
        forum.guild.get_thread.return_value = fake_thread(name="repurposed")
        with patch.object(poster, "_resolve_forum", AsyncMock(return_value=forum)), \
             patch.object(poster.api, "claim", return_value={
                 "pending": [entry()], "threads": {"101": {"thread_id": 700}}}), \
             patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_failed") as failed, \
             patch.object(poster.log, "error") as errored:
            counts = run(poster.drain_once(MagicMock(), "forge"))
        assert counts["failed"] == 1 and counts["posted"] == 0
        assert "thread mismatch" in failed.call_args[0][2]
        errored.assert_called_once()
        forum.create_thread.assert_not_called()

    def test_one_mismatch_does_not_abort_the_rest_of_the_run(self, forum, facts):
        # ⚠️ #944's last criterion. The broken member is in the MIDDLE, because
        # a run that stops at the first problem would still pass a test where
        # the problem is last.
        good, bad = fake_thread(701, member_id="100"), fake_thread(702, name="repurposed")
        third = fake_thread(703, member_id="102")
        forum.guild.get_thread.side_effect = lambda tid: {701: good, 702: bad, 703: third}[tid]
        pending = [entry(id=1, member_id="100"), entry(id=2, member_id="101"),
                   entry(id=3, member_id="102")]
        registry = {"100": {"thread_id": 701}, "101": {"thread_id": 702},
                    "102": {"thread_id": 703}}
        with patch.object(poster, "_resolve_forum", AsyncMock(return_value=forum)), \
             patch.object(poster.api, "claim", return_value={
                 "pending": pending, "threads": registry}), \
             patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"), \
             patch.object(poster.api, "mark_failed"), \
             patch.object(poster.asyncio, "sleep", AsyncMock()):
            counts = run(poster.drain_once(MagicMock(), "forge"))
        assert counts == {"claimed": 3, "posted": 2, "failed": 1}


class TestPostingAndEditing:
    def test_a_member_with_no_thread_gets_one_created(self, forum, facts):
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted") as posted:
            ok, detail = run(poster.post_one(MagicMock(), "forge", entry(), {}, forum))
        assert ok and "created thread" in detail
        # ⚠️ Recorded the instant Discord confirms. A message Discord accepted
        # but we never recorded is the one failure that duplicates a permanent
        # record: the next drain posts a second embed into the same thread.
        posted.assert_called_once()
        assert posted.call_args.kwargs["message_id"] == 800
        assert posted.call_args.kwargs["thread_id"] == 700

    def test_the_thread_name_carries_the_member_id(self, forum, facts):
        # Two members have shared a display name, and Torn names change.
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"):
            run(poster.post_one(MagicMock(), "forge", entry(), {}, forum))
        assert "[101]" in forum.create_thread.call_args.kwargs["name"]

    def test_an_existing_thread_is_appended_to(self, forum, facts):
        thread = fake_thread()
        forum.guild.get_thread.return_value = thread
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"):
            ok, detail = run(poster.post_one(
                MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))
        assert ok and "posted 801" in detail
        forum.create_thread.assert_not_called()

    def test_a_rerun_of_the_same_war_EDITS_rather_than_appends(self, forum, facts):
        # ⚠️ MonChoon: "on a re-run for the same war I would edit the post. on a
        # new war I would append." Two embeds for one war in one thread is the
        # failure this prevents.
        msg = MagicMock(id=801)
        msg.edit = AsyncMock()
        thread = fake_thread()
        thread.send = AsyncMock()
        thread.fetch_message = AsyncMock(return_value=msg)
        forum.guild.get_thread.return_value = thread
        # ⚠️ The message id rides on the ENTRY, not the registry. The dashboard
        # keeps one outbox row per (member, kind, war) and a re-queue re-opens
        # that row keeping the id it recorded.
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"):
            ok, detail = run(poster.post_one(
                MagicMock(), "forge", entry(message_id="801"),
                {"101": {"thread_id": 700}}, forum))
        assert ok and "edited 801" in detail
        msg.edit.assert_awaited_once()
        thread.send.assert_not_called()

    def test_an_archived_thread_is_unarchived_first(self, forum, facts):
        # Forums archive quiet threads; we post at least once a war, so this is
        # routine rather than exceptional.
        thread = fake_thread(archived=True)
        thread.send = AsyncMock(return_value=MagicMock(id=802))
        forum.guild.get_thread.return_value = thread
        with patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"):
            ok, _ = run(poster.post_one(
                MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))
        assert ok
        thread.edit.assert_awaited_once_with(archived=False)

    def test_an_archived_thread_we_cannot_unarchive_fails_loudly(self, forum, facts):
        thread = fake_thread(archived=True)
        thread.edit = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "no Manage Threads"))
        forum.guild.get_thread.return_value = thread
        with patch.object(poster.api, "report", return_value=facts):
            with pytest.raises(poster.ThreadGone, match="Manage Threads"):
                run(poster.post_one(
                    MagicMock(), "forge", entry(), {"101": {"thread_id": 700}}, forum))


class TestNeverInventingAReport:
    def test_no_facts_means_no_post(self, forum):
        # ⚠️ The dashboard refuses a live or unknown war. Posting anyway would
        # put a scoreless report into a permanent record, which cannot be
        # unposted. MonChoon: "it should just fail loudly."
        with patch.object(poster.api, "report", return_value=None), \
             patch.object(poster.api, "mark_posted") as posted:
            ok, detail = run(poster.post_one(MagicMock(), "forge", entry(), {}, forum))
        assert not ok and "no report available" in detail
        posted.assert_not_called()
        forum.create_thread.assert_not_called()

    def test_an_entry_with_no_war_id_is_refused(self, forum):
        ok, detail = run(poster.post_one(
            MagicMock(), "forge", entry(payload={}), {}, forum))
        assert not ok and "no war_id" in detail


class TestPacing:
    def test_posts_are_spaced_out(self, forum, facts):
        # ⚠️ A roster-wide run is ~94 of these. A burst earns a global 429 that
        # penalises every other feature this bot runs.
        pending = [entry(id=i, member_id=str(100 + i)) for i in range(1, 4)]
        with patch.object(poster, "_resolve_forum", AsyncMock(return_value=forum)), \
             patch.object(poster.api, "claim", return_value={
                 "pending": pending, "threads": {}}), \
             patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"), \
             patch.object(poster.asyncio, "sleep", AsyncMock()) as slept:
            counts = run(poster.drain_once(MagicMock(), "forge"))
        assert counts["posted"] == 3
        # Between posts, not after the last one.
        assert slept.await_count == 2
        assert slept.await_args[0][0] == poster.POST_DELAY_SECONDS

    def test_one_tick_is_capped(self, forum, facts):
        pending = [entry(id=i, member_id=str(100 + i))
                   for i in range(poster.MAX_PER_TICK + 10)]
        with patch.object(poster, "_resolve_forum", AsyncMock(return_value=forum)), \
             patch.object(poster.api, "claim", return_value={
                 "pending": pending, "threads": {}}), \
             patch.object(poster.api, "report", return_value=facts), \
             patch.object(poster.api, "mark_posted"), \
             patch.object(poster.asyncio, "sleep", AsyncMock()):
            counts = run(poster.drain_once(MagicMock(), "forge"))
        assert counts["claimed"] == poster.MAX_PER_TICK
