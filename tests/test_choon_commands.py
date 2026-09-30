"""
`/choon` — the bot's own controls (#826).

⚠️ The renames are a delete-and-create from Discord's side, and the failure mode
of a command move is the same as a command that never attached: it silently is
not there, and the first person to find out is a leader typing it.
"""

import asyncio

import discord
from discord import app_commands
import pytest

import chain_commands
import chain_tenants
import choon_auth
import choon_commands
import war_overview_commands


def tree():
    t = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
    chain_commands.register(t)
    choon_commands.register(t)
    war_overview_commands.register(t)
    return t


def paths(t):
    out = set()
    for c in t.get_commands():
        out.add(c.name)
        for s in getattr(c, "commands", []):
            out.add(f"{c.name} {s.name}")
            for leaf in getattr(s, "commands", []):
                out.add(f"{c.name} {s.name} {leaf.name}")
    return out


class TestTheNewShape:
    def test_the_three_roots(self):
        assert {"chain", "choon", "rw"} <= {c.name for c in tree().get_commands()}

    def test_faction_management_lives_under_choon(self):
        p = paths(tree())
        for leaf in ("list", "add", "remove", "role"):
            assert f"choon faction {leaf}" in p, leaf

    def test_and_no_longer_under_chain(self):
        # ⚠️ Gone, not aliased. Two paths to one handler means two things to
        # keep gated, and the old one is the one somebody forgets.
        assert not any(x.startswith("chain tenant") for x in paths(tree()))

    def test_rw_overview_is_a_subcommand_now(self):
        p = paths(tree())
        assert "rw overview" in p
        assert "rw-overview" not in p

    def test_chain_keeps_everything_else(self):
        p = paths(tree())
        for leaf in ("settings", "set", "reset", "link", "unlink", "link-status",
                     "link-sync", "channel", "stop", "tidy", "help", "refresh"):
            assert f"chain {leaf}" in p, leaf

    def test_nothing_exceeds_discords_two_levels_of_nesting(self):
        # ⚠️ `/choon faction add` is AT the limit. A fourth level is not a
        # deeper menu — Discord rejects the command outright.
        for c in tree().get_commands():
            for s in getattr(c, "commands", []):
                for leaf in getattr(s, "commands", []):
                    assert not getattr(leaf, "commands", []), (
                        f"{c.name} {s.name} {leaf.name} has children")


class TestWhySeparateRoots:
    def test_each_root_is_its_own_top_level_command(self):
        # ⚠️ THE point of #826. Discord permission overrides attach to a command
        # ID and subcommands have none, so this is what lets `/rw` be opened to
        # members while `/chain` stays with leadership. Under one root it could
        # never be expressed.
        names = {c.name for c in tree().get_commands()}
        assert {"chain", "rw", "choon"} <= names

    def test_none_of_them_ships_a_default_permission(self):
        # ⚠️ Left to the operator in Integrations rather than decided here.
        # Baking one in would hide commands from people without anybody having
        # chosen that, and `/choon help` in particular must stay reachable.
        for c in tree().get_commands():
            assert c.default_permissions is None, c.name


class TestHelp:
    def test_help_is_open_to_everyone(self, tmp_path, monkeypatch):
        # ⚠️ A help command that refuses is how somebody concludes the bot is
        # broken rather than that it is not for them.
        import state
        monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "s.json"), raising=False)
        monkeypatch.delenv(choon_auth.ADMIN_ENV, raising=False)
        cmd = next(s for s in next(c for c in tree().get_commands()
                                   if c.name == "choon").commands if s.name == "help")
        sent = []

        class I:
            guild_id = 999
            user = type("M", (), {"id": 1, "roles": []})()
            response = type("R", (), {"send_message": staticmethod(
                lambda *a, **k: _collect(sent, a, k))})()

        asyncio.run(cmd.callback(I()))
        assert sent, "help refused a member"

    def test_it_names_all_three_roots(self, tmp_path, monkeypatch):
        import state
        monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "s.json"), raising=False)
        cmd = next(s for s in next(c for c in tree().get_commands()
                                   if c.name == "choon").commands if s.name == "help")
        sent = []

        class I:
            guild_id = 999
            user = type("M", (), {"id": 1, "roles": []})()
            response = type("R", (), {"send_message": staticmethod(
                lambda *a, **k: _collect(sent, a, k))})()

        asyncio.run(cmd.callback(I()))
        text = sent[0]["embed"].description
        for root in ("/chain", "/rw overview", "/choon faction"):
            assert root in text, root


async def _collect(sink, args, kwargs):
    sink.append(kwargs or {"content": args[0] if args else None})
