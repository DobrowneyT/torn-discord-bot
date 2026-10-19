"""
The OC action feed (#1020).

⚠️ What must never happen: a second post for the same suggestion, a ping on a
redraw, a button that stops routing after a restart, or a non-manager closing
a suggestion.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import chain_identity
import chain_tenants
import oc_feed


def sug(**over):
    s = {"id": 12, "key": "swap:1", "kind": "swap", "status": "open", "version": 1,
         "title": "Swap two seats", "body": "Alice is better at Hacker.",
         "steps": ["Bob → free seat", "Alice → Hacker", "Bob → Picklock"],
         "value": {"success_pp": 6.1, "ev": 38_000_000}, "crime_id": "9001",
         "message_id": None, "channel_id": None, "acted_by": None, "close_reason": None,
         "expires_at": None, "snoozed_until": None}
    s.update(over)
    return s


class TestRender:
    def test_open_shows_the_moves_and_the_worth(self):
        e = oc_feed.render(sug())
        assert e.title.startswith("⚖️ Better seating — Swap two seats")
        assert "**2.** Alice → Hacker" in e.description
        assert "+6.1% success" in e.description and "+$38M" in e.description
        assert e.footer.text == "Suggestion #12 · swap"

    def test_closed_is_struck_through_and_says_why(self):
        for status, words in (("done", "Marked done by MonChoon"), ("resolved", "Seen happening"),
                              ("expired", "No longer possible")):
            e = oc_feed.render(sug(status=status, acted_by="MonChoon", close_reason="No longer possible"))
            assert e.title.startswith("~~") and words in e.description
            assert "Alice → Hacker" not in e.description

    def test_snoozed_says_until_when(self):
        e = oc_feed.render(sug(status="snoozed", acted_by="MonChoon", snoozed_until=1_800_000_000_000))
        assert "Snoozed by MonChoon until <t:1800000000:t>" in e.description


class TestButtons:
    def test_only_an_open_suggestion_has_buttons_with_stable_ids(self):
        async def build():
            return oc_feed.view_for("forge", sug()), oc_feed.view_for("forge", sug(status="snoozed"))
        view, none = asyncio.run(build())
        assert none is None
        assert [c.custom_id for c in view.children] == ["oc_sugg:done:forge:12", "oc_sugg:snooze:forge:12"]
        assert view.is_persistent()

    def test_the_id_template_reads_back(self):
        m = oc_feed.SuggestionButton.__discord_ui_compiled_template__.fullmatch("oc_sugg:snooze:tnl:345")
        assert m and m["action"] == "snooze" and m["slug"] == "tnl" and m["id"] == "345"


class TestPings:
    def test_only_linked_members_are_mentioned(self):
        chain_identity.link("2250591", 111, name="MonChoon")
        assert oc_feed.ping_content(["2250591", "999"]) == "<@111>"
        assert oc_feed.ping_content([]) is None


def channel_with(message=None, missing=False):
    ch = MagicMock()
    ch.id = 42
    sent = MagicMock(id=777)
    ch.send = AsyncMock(return_value=sent)
    if missing:
        ch.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    else:
        ch.fetch_message = AsyncMock(return_value=message)
    return ch


def run_sync(payload, ch):
    tenant = SimpleNamespace(slug="forge")
    post = AsyncMock(return_value={"ok": True})
    with patch.object(oc_feed.chain_api, "oc_post", post):
        counts = asyncio.run(oc_feed.sync(tenant, payload, ch))
    return counts, post


class TestSync:
    def test_a_new_suggestion_is_posted_once_with_the_ping_and_recorded(self):
        chain_identity.link("2250591", 111, name="MonChoon")
        ch = channel_with()
        counts, post = run_sync({"findings": [sug()], "ping_member_ids": ["2250591"]}, ch)
        assert counts["posted"] == 1
        kwargs = ch.send.await_args.kwargs
        assert kwargs["content"] == "<@111>"
        assert kwargs["view"] is not None
        assert post.await_args.args[1:] == ("/suggestions/12/rendered", {"version": 1, "message_id": "777", "channel_id": "42"})

    def test_a_change_edits_the_same_message_and_never_pings(self):
        msg = MagicMock()
        msg.edit = AsyncMock()
        ch = channel_with(msg)
        counts, post = run_sync({"findings": [sug(status="snoozed", acted_by="x", version=3, message_id="777")],
                                 "ping_member_ids": ["2250591"]}, ch)
        assert counts["edited"] == 1
        ch.send.assert_not_awaited()
        assert "content" not in msg.edit.await_args.kwargs
        assert msg.edit.await_args.kwargs["view"] is None
        assert post.await_args.args[2] == {"version": 3}

    def test_a_closed_suggestion_s_message_is_deleted(self):
        for status in ("done", "resolved", "expired"):
            msg = MagicMock()
            msg.delete = AsyncMock()
            msg.edit = AsyncMock()
            ch = channel_with(msg)
            counts, post = run_sync({"findings": [sug(status=status, version=4, message_id="777")]}, ch)
            msg.delete.assert_awaited_once()
            msg.edit.assert_not_awaited()
            assert counts["deleted"] == 1
            assert post.await_args.args[2] == {"version": 4, "deleted": True}

    def test_an_already_deleted_message_is_fine(self):
        ch = channel_with(missing=True)
        counts, post = run_sync({"findings": [sug(status="expired", message_id="777")]}, ch)
        assert counts["deleted"] == 1 and post.await_args.args[2]["deleted"] is True

    def test_closed_before_it_was_ever_posted_is_only_acknowledged(self):
        ch = channel_with()
        counts, post = run_sync({"findings": [sug(status="expired")]}, ch)
        ch.send.assert_not_awaited()
        assert post.await_args.args[2] == {"version": 1, "deleted": True}

    def test_an_open_one_whose_message_was_deleted_is_posted_again(self):
        ch = channel_with(missing=True)
        counts, _ = run_sync({"findings": [sug(message_id="555")]}, ch)
        assert counts["posted"] == 1

    def test_a_discord_failure_leaves_it_for_next_tick(self):
        ch = channel_with()
        ch.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "boom"))
        counts, post = run_sync({"findings": [sug()]}, ch)
        post.assert_not_awaited()     # not marked rendered, so the dashboard re-sends it


class TestPressing:
    def _press(self, allowed=True, result=None):
        chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)

        async def go():
            btn = oc_feed.SuggestionButton("done", "forge", 12)
            interaction = MagicMock()
            interaction.user = SimpleNamespace(id=1, display_name="MonChoon")
            interaction.response.send_message = AsyncMock()
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            interaction.message.edit = AsyncMock()
            interaction.message.delete = AsyncMock()
            post = AsyncMock(side_effect=[result or {"ok": True, "suggestion": sug(status="done", acted_by="MonChoon", version=2)},
                                          {"ok": True}])
            with patch.object(oc_feed.choon_auth, "may_manage", return_value=(allowed, "Managers only.")), \
                 patch.object(oc_feed.chain_api, "oc_post", post):
                await btn.callback(interaction)
            return interaction, post
        return asyncio.run(go())

    def test_a_manager_marks_it_done_and_the_message_is_removed_at_once(self):
        interaction, post = self._press()
        assert post.await_args_list[0].args[1:] == ("/suggestions/12/act", {"action": "done", "by": "MonChoon"})
        interaction.message.delete.assert_awaited_once()
        interaction.message.edit.assert_not_awaited()
        assert post.await_args_list[1].args[1:] == ("/suggestions/12/rendered", {"version": 2, "deleted": True})
        assert "removed from the thread" in interaction.followup.send.await_args.args[0]

    def test_a_non_manager_is_refused_and_nothing_is_sent(self):
        interaction, post = self._press(allowed=False)
        interaction.response.send_message.assert_awaited_once()
        post.assert_not_awaited()

    def test_already_closed_says_so_and_does_not_redraw(self):
        interaction, _ = self._press(result={"ok": False, "error": "closed"})
        interaction.followup.send.assert_awaited_once()
        interaction.message.edit.assert_not_awaited()
