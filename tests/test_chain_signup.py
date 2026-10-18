"""
Signing up from the Chain Watch board (#1008, #1009).

⚠️ What must never happen: a sign-up for somebody we only GUESSED the identity
of, a request sent without an id, or "failed" said about a call that may have
worked. The views are real discord.py classes — a fake would only prove the
fake's shape (see choonbot memory: `add_listener`).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import chain_api
import chain_signup as cs

HOUR = 3_600_000
NOW = 1_800_000_000_000 - (1_800_000_000_000 % HOUR)


def user(nick=None, display=None, glob=None, name="someone"):
    return SimpleNamespace(nick=nick, display_name=display, global_name=glob, name=name)


def payload(hours=None, members=("3099056", "111111")):
    return {
        "event": {"id": 1},
        "members": [{"member_id": m, "name": m} for m in members],
        "hours": hours if hours is not None else [
            {"hour_start": NOW - HOUR, "open_slots": 2, "slots_per_hour": 2, "watchers": []},
            {"hour_start": NOW, "open_slots": 1, "slots_per_hour": 2, "watchers": [{"member_id": "111111"}]},
            {"hour_start": NOW + HOUR, "open_slots": 0, "slots_per_hour": 2, "watchers": []},
            {"hour_start": NOW + 2 * HOUR, "open_slots": 1, "slots_per_hour": 2, "bonus": True,
             "watchers": [{"member_id": "3099056"}]},
            {"hour_start": NOW + 3 * HOUR, "open_slots": 2, "slots_per_hour": 2, "watchers": []},
        ],
    }


class TestIdentity:
    def test_reads_the_id_torn_verification_writes(self):
        assert cs.member_id_from(user(nick="Fluhffy [3099056] (TNL)")) == "3099056"

    def test_server_nickname_wins(self):
        assert cs.member_id_from(user(nick="A [1111111]", display="B [2222222]")) == "1111111"

    def test_falls_back_to_other_names(self):
        assert cs.member_id_from(user(glob="Goosey (873341)")) == "873341"

    def test_no_id_is_none_never_a_name_match(self):
        assert cs.member_id_from(user(nick="Fluhffy", display="Fluhffy")) is None


class TestMenus:
    def test_sign_up_offers_open_unfinished_hours_not_already_yours(self):
        opts = cs.sign_up_options(payload(), "3099056", NOW + 60_000)
        assert [o[0] for o in opts] == [NOW, NOW + 3 * HOUR]
        assert "happening now" in opts[0][2]

    def test_sign_up_menu_is_capped_at_discords_limit(self):
        hours = [{"hour_start": NOW + i * HOUR, "open_slots": 1, "slots_per_hour": 2, "watchers": []}
                 for i in range(40)]
        assert len(cs.sign_up_options(payload(hours), "3099056", NOW)) == cs.MAX_OPTIONS

    def test_drop_offers_only_your_hours_that_have_not_started(self):
        p = payload()
        p["hours"][1]["watchers"].append({"member_id": "3099056"})  # in progress: not droppable
        assert [o[0] for o in cs.drop_options(p, "3099056", NOW + 60_000)] == [NOW + 2 * HOUR]


class TestMessages:
    def test_success(self):
        assert cs.result_message("sign_up", {"ok": True}, member_id="1", slug="tnl", hour_ms=NOW).startswith("✅")

    def test_not_member_names_the_id_and_faction(self):
        msg = cs.result_message("sign_up", {"ok": False, "code": "not_member"},
                                member_id="3099056", slug="tnl", hour_ms=NOW)
        assert "3099056" in msg and "TNL" in msg

    def test_unreachable_never_claims_failure(self):
        msg = cs.result_message("sign_up", {"ok": False, "code": chain_api.UNREACHABLE},
                                member_id="1", slug="tnl", hour_ms=NOW)
        assert "can't confirm" in msg

    def test_unknown_code_is_treated_as_unconfirmed(self):
        msg = cs.result_message("sign_up", {"ok": False, "code": "brand_new"},
                                member_id="1", slug="tnl", hour_ms=NOW)
        assert "can't confirm" in msg

    def test_every_dashboard_code_has_a_message(self):
        for code in ("not_member", "no_event", "outside_event", "not_on_hour", "hour_finished",
                     "already_signed_up", "hour_full", "not_signed_up", "hour_started"):
            assert code in cs.MESSAGES


class TestViews:
    def test_buttons_are_persistent_with_fixed_ids(self):
        async def build():
            return cs.BoardButtons()
        view = asyncio.run(build())
        assert view.is_persistent()
        assert {c.custom_id for c in view.children} == {cs.SIGN_UP_ID, cs.DROP_ID}

    def test_picker_is_a_real_select(self):
        async def build():
            return cs.HourPicker("sign_up", "tnl", "1", [(NOW, "Mon · 00:00 TCT", "1 of 2")], None)
        view = asyncio.run(build())
        select = view.children[0]
        assert isinstance(select, discord.ui.Select)
        assert select.options[0].value == str(NOW)


def interaction(nick="Fluhffy [3099056] (TNL)", message_id=555):
    response = SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock())
    return SimpleNamespace(
        user=user(nick=nick), message=SimpleNamespace(id=message_id),
        response=response, edit_original_response=AsyncMock())


def run(coro):
    return asyncio.run(coro)


class TestOpenMenu:
    def test_no_id_never_reaches_the_dashboard(self):
        i = interaction(nick="Fluhffy")
        with patch.object(cs, "slug_for_board", return_value="tnl"), \
             patch.object(cs.chain_api, "fetch", AsyncMock()) as fetch:
            run(cs.open_menu(i, "sign_up", None))
        fetch.assert_not_awaited()
        assert "re-verify" in i.response.send_message.await_args.args[0].lower()

    def test_someone_from_another_faction_is_told_so(self):
        i = interaction(nick="Stranger [4242424]")
        with patch.object(cs, "slug_for_board", return_value="tnl"), \
             patch.object(cs.chain_tenants, "get", return_value=object()), \
             patch.object(cs.chain_api, "fetch", AsyncMock(return_value=payload())):
            run(cs.open_menu(i, "sign_up", None))
        text = i.response.send_message.await_args.args[0]
        assert "4242424" in text and "TNL" in text

    def test_a_member_gets_a_private_menu(self):
        i = interaction()
        with patch.object(cs, "slug_for_board", return_value="tnl"), \
             patch.object(cs.chain_tenants, "get", return_value=object()), \
             patch.object(cs.chain_api, "fetch", AsyncMock(return_value=payload())), \
             patch.object(cs.time, "time", return_value=(NOW + 60_000) / 1000):
            run(cs.open_menu(i, "sign_up", None))
        kwargs = i.response.send_message.await_args.kwargs
        assert kwargs["ephemeral"] is True
        assert isinstance(kwargs["view"], cs.HourPicker)

    def test_a_stale_board_message_says_so(self):
        i = interaction()
        with patch.object(cs, "slug_for_board", return_value=None):
            run(cs.open_menu(i, "sign_up", None))
        assert "isn't active" in i.response.send_message.await_args.args[0]


class TestPicking:
    def _pick(self, result, kind="sign_up"):
        on_change = AsyncMock()
        i = interaction()

        async def go():
            view = cs.HourPicker(kind, "tnl", "3099056", [(NOW, "x", "y")], on_change)
            view._select._values = [str(NOW)]
            with patch.object(cs.chain_tenants, "get", return_value=object()), \
                 patch.object(cs.chain_api, "post", AsyncMock(return_value=result)) as post:
                await view._picked(i)
            return post
        post = asyncio.run(go())
        return i, post, on_change

    def test_shows_progress_then_the_answer_and_redraws(self):
        i, post, on_change = self._pick({"ok": True})
        assert "⏳" in i.response.edit_message.await_args.kwargs["content"]
        assert i.response.edit_message.await_args.kwargs["view"] is None
        assert post.await_args.args[1] == "/signups"
        assert post.await_args.args[2] == {"member_id": "3099056", "hour_start": NOW}
        assert i.edit_original_response.await_args.kwargs["content"].startswith("✅")
        on_change.assert_awaited_once_with("tnl")

    def test_a_refusal_is_relayed_and_nothing_redraws(self):
        i, _, on_change = self._pick({"ok": False, "code": "hour_full"})
        assert "last place" in i.edit_original_response.await_args.kwargs["content"]
        on_change.assert_not_awaited()

    def test_drop_uses_the_drop_route(self):
        _, post, _ = self._pick({"ok": True}, kind="drop")
        assert post.await_args.args[1] == "/signups/drop"
