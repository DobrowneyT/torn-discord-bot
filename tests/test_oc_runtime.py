"""
The OC boards, read from each faction's dashboard (#1019).

⚠️ What must not regress: the alert rules themselves (unchanged), one faction's
outage blanking another's board, and an override being pruned because a
dashboard did not answer.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import chain_tenants
import oc_runtime
import oc_settings
import state
import formatter as formatter_mod

NOW = 1_800_000_000


def slot(label, user_id, cpr, required, item=None):
    return {"position": label.split(" #")[0], "position_info": {"label": label}, "position_label": label,
            "checkpoint_pass_rate": cpr, "required_cpr": required,
            "user": {"id": user_id, "name": f"m{user_id}"} if user_id else None,
            "item_requirement": item}


def payload(**over):
    p = {
        "server_now": NOW * 1000, "crimes_fetched_at": NOW * 1000, "members_fetched_at": NOW * 1000,
        "crimes": [{
            "id": 9001, "name": "Blast from the Past", "status": "Planning", "ready_at": NOW + 3600,
            "slots": [
                slot("Bomber", 1, 70, 75),          # heavy role below its bar → flagged
                slot("Picklock #2", 2, 48, 50),     # light role, its own low bar → flagged by 2
                slot("Hacker", 3, 40, None),        # no published minimum → not judged
                slot("Muscle", 4, 80, 75, {"id": 1294, "name": "Glitter Bomb", "is_available": False,
                                           "armoury_available": 2}),
            ],
        }],
        "members": [{"id": 4, "name": "m4", "status": {"state": "Okay"}}],
        "findings": [],
    }
    p.update(over)
    return p


class TestBuild:
    def test_seats_are_judged_against_their_own_published_minimum(self):
        a = oc_runtime.build(payload(), NOW, set())
        low = {e["user_id"]: e["required"] for e in a["crimes"][0]["low_cpr"]}
        assert low == {1: 75, 2: 50}           # 3 has no minimum, 4 clears it

    def test_missing_item_says_the_armoury_has_one(self):
        a = oc_runtime.build(payload(), NOW, set())
        body = formatter_mod.build_embeds(a, now_ts=NOW)[0].description
        assert "Glitter Bomb (armoury has 2)" in body

    def test_overrides_still_suppress(self):
        a = oc_runtime.build(payload(), NOW, {(9001, 1, "Bomber")})
        assert [e["user_id"] for e in a["crimes"][0]["low_cpr"]] == [2]

    def test_old_data_is_said_out_loud(self):
        stale = payload(members_fetched_at=(NOW - 30 * 60) * 1000, crimes_fetched_at=None)
        a = oc_runtime.build(stale, NOW, set())
        assert "crimes yet" in a["notice"] and "30 min old" in a["notice"]
        assert a["notice"] in formatter_mod.build_embeds(a, now_ts=NOW)[0].description
        assert "notice" not in oc_runtime.build(payload(), NOW, set())


def board(fetch_results):
    """An OcBoards whose dashboards answer `fetch_results[slug]`, drawing into fakes."""
    client = MagicMock()
    boards = oc_runtime.OcBoards(client)
    sent = {}

    async def fake_message(slug, thread_id):
        msg = sent.setdefault(slug, MagicMock())
        msg.edit = AsyncMock()
        msg.channel = MagicMock(spec=discord.TextChannel)
        return msg
    boards._message = fake_message

    async def fake_fetch(tenant):
        r = fetch_results[tenant.slug]
        return r() if callable(r) else r
    return boards, sent, fake_fetch


def tenants(*slugs):
    for i, s in enumerate(slugs):
        chain_tenants.add(s, f"https://{s}.monchoon.me", 1, 10 + i)
        oc_settings.set_value(s, "oc_thread_id", 500 + i)


class TestTickAll:
    def test_every_configured_faction_gets_its_own_board(self):
        tenants("forge", "tnl")
        boards, sent, fetch = board({"forge": payload(), "tnl": payload()})
        with patch.object(oc_runtime.chain_api, "fetch_oc", fetch), \
             patch.object(oc_runtime.time, "time", return_value=NOW):
            asyncio.run(boards.tick_all())
        assert set(sent) == {"forge", "tnl"}
        embeds = sent["tnl"].edit.await_args.kwargs["embeds"]
        assert embeds[0].title.endswith("· TNL")
        assert sent["tnl"].edit.await_args.kwargs["view"] is boards.view

    def test_a_faction_without_a_thread_posts_nothing(self):
        chain_tenants.add("mayhem", "https://mayhem.monchoon.me", 1, 10)
        boards, sent, fetch = board({"mayhem": payload()})
        with patch.object(oc_runtime.chain_api, "fetch_oc", fetch):
            asyncio.run(boards.tick_all())
        assert sent == {}

    def test_one_dashboard_down_keeps_the_others_and_prunes_nothing(self):
        tenants("forge", "tnl")
        boards, sent, fetch = board({"forge": payload(), "tnl": None})
        boards.store.add(7777, 1, "Bomber")
        with patch.object(oc_runtime.chain_api, "fetch_oc", fetch), \
             patch.object(oc_runtime.time, "time", return_value=NOW):
            asyncio.run(boards.tick_all())
        assert set(sent) == {"forge"}
        assert boards.store.has(7777, 1, "Bomber"), "a partial picture must not prune overrides"

    def test_finished_crimes_lose_their_overrides_when_every_board_answered(self):
        tenants("forge")
        boards, _, fetch = board({"forge": payload()})
        boards.store.add(7777, 1, "Bomber")
        boards.store.add(9001, 1, "Bomber")
        with patch.object(oc_runtime.chain_api, "fetch_oc", fetch), \
             patch.object(oc_runtime.time, "time", return_value=NOW):
            asyncio.run(boards.tick_all())
        assert not boards.store.has(7777, 1, "Bomber")
        assert boards.store.has(9001, 1, "Bomber")

    def test_the_override_menu_sees_every_faction(self):
        tenants("forge", "tnl")
        boards, _, fetch = board({"forge": payload(), "tnl": payload()})
        with patch.object(oc_runtime.chain_api, "fetch_oc", fetch), \
             patch.object(oc_runtime.time, "time", return_value=NOW):
            asyncio.run(boards.tick_all())
        assert len(boards.all_alerts()["crimes"]) == 2


class TestLegacyAdoption:
    def test_the_old_single_board_moves_onto_forge_with_its_message(self, monkeypatch):
        chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "4242")
        st = state.load_state()
        st["message_id"] = 999
        st["notified_red_alerts"] = [{"crime_id": 1, "user_id": 2, "position": "Muscle", "alert_type": "unavailable"}]
        state.save_state(st)
        assert oc_settings.adopt_legacy() == "forge"
        assert oc_settings.get("forge", "oc_thread_id") == 4242
        assert oc_settings.board_message("forge") == 999
        assert oc_runtime._notified("forge") == {(1, 2, "Muscle", "unavailable")}
        # Once only: the settings are the truth after that.
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "5555")
        assert oc_settings.adopt_legacy() is None
        assert oc_settings.get("forge", "oc_thread_id") == 4242

    def test_nothing_is_adopted_onto_a_faction_the_bot_does_not_serve(self, monkeypatch):
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "4242")
        assert oc_settings.adopt_legacy() is None

    def test_changing_the_thread_starts_a_new_board_message(self):
        chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
        oc_settings.set_board_message("forge", 999)
        oc_settings.set_value("forge", "oc_thread_id", 777)
        assert oc_settings.board_message("forge") is None
