"""#1024 — /oc digest, /oc report, /oc feed. On request only."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands

import chain_tenants
import oc_commands
import oc_reports

DIGEST = {
    "members": {"current": 98, "seated": 90, "idle": 8},
    "levels": [{"difficulty": 8, "crimes": 4, "open": 2, "seated": 20, "idle_qualified": 5},
               {"difficulty": 7, "crimes": 6, "open": 3, "seated": 30, "idle_qualified": 1}],
    "start_next": [{"difficulty": 8, "short_by": 3}], "scope": None,
}
REPORT = {
    "days": 7, "finished": 12, "successful": 10, "failed": 2, "analysed": 11, "average_efficiency": 97.4,
    "could_have_seated_better": 3,
    "biggest_gaps": [{"name": "Blast from the Past", "difficulty": 7, "status": "Failure", "executed_at": 1_800_000_000_000,
                      "weighted_gain": 4.2, "moves": ["Ann: Muscle → Picklock #2", "Ben: Picklock #2 → Muscle"]}],
    "failures": [{"name": "Blast from the Past", "difficulty": 7, "executed_at": 1_800_000_000_000,
                  "weakest": {"label": "Muscle", "member_id": "1", "member_name": "Ann", "cpr": 50, "weight_pct": 35}}],
    "caveat": "Other seats are judged on today's readings.",
}


def test_digest_says_what_to_start_and_that_scope_is_missing():
    e = oc_reports.digest_embed("forge", DIGEST)
    assert "**90** of **98**" in e.description
    assert "Start another L8" in e.description and "3 more qualified members" in e.description
    assert "Scope isn't tracked" in e.description


def test_report_names_gaps_and_failures():
    e = oc_reports.report_embed("forge", REPORT)
    assert "**97.4%**" in e.description
    assert "Ann: Muscle → Picklock #2" in e.description
    assert "Muscle (35% weight) held by Ann at CPR 50" in e.description
    assert "today's readings" in e.description


def _tree():
    async def build():
        client = MagicMock()
        tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
        oc_commands.register(tree)
        return tree
    return asyncio.run(build())


def test_the_commands_register_against_real_discord_py():
    oc = _tree().get_command("oc")
    assert {c.name for c in oc.commands} >= {"thread", "test", "stats", "digest", "report", "feed"}


def test_feed_switches_a_kind_off_through_the_dashboard():
    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    feed = _tree().get_command("oc").get_command("feed")
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    post = AsyncMock(return_value={"kinds": {"swap": True, "too_low": True, "idle": False}})
    with patch.object(oc_commands.choon_auth, "may_manage", return_value=(True, None)), \
         patch.object(oc_commands.chain_api, "oc_post", post):
        asyncio.run(feed.callback(interaction, kind=app_commands.Choice(name="Not in an OC", value="idle"),
                                  state=app_commands.Choice(name="off", value="off"), faction="forge"))
    assert post.await_args.args[1:] == ("/feed-kinds", {"kind": "idle", "enabled": False})
    text = interaction.response.send_message.await_args.args[0]
    assert "⚪ Not in an OC — off" in text and "Turned off" in text


def test_a_non_manager_gets_nothing():
    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    digest = _tree().get_command("oc").get_command("digest")
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    with patch.object(oc_commands.choon_auth, "may_manage", return_value=(False, "Managers only.")), \
         patch.object(oc_commands.chain_api, "oc_get", AsyncMock()) as get:
        asyncio.run(digest.callback(interaction, faction="forge"))
    get.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0] == "Managers only."
