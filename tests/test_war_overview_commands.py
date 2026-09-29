"""
`/rw-overview` (#811) — that it attaches, and that its argument rules hold.

⚠️ discord.py registers commands at attach time and its failure mode is a
command that silently does not appear: no error at startup, the slash command
just is not there, and the first person to find out is a leader typing it in
front of others.
"""

import discord
from discord import app_commands

import chain_tenants
import war_overview_commands as woc
import war_overview_api as api


def build():
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
    woc.register(tree)
    return tree


def cmd():
    return next(c for c in build().get_commands() if c.name == "rw-overview")


def test_the_command_is_attached():
    assert "rw-overview" in [c.name for c in build().get_commands()]


def test_war_and_type_are_required_and_the_rest_are_not():
    params = {p.name: p for p in cmd().parameters}
    assert params["war"].required and params["type"].required
    for optional in ("faction", "member", "warring_only"):
        assert not params[optional].required


def test_every_parameter_worth_completing_has_autocomplete():
    # ⚠️ The rule #803 exists for: never make somebody type an id from memory.
    params = {p.name: p for p in cmd().parameters}
    for k in ("faction", "war", "member"):
        assert params[k].autocomplete, f"{k} makes you guess"


def test_summary_only_exists_and_defaults_to_off():
    # ⚠️ Off by default: the chart is the reason to run this in a channel
    # rather than reading the page. The flag is for when somebody wants the
    # numbers without a picture.
    assert {p.name: p for p in cmd().parameters}["summary_only"].default is False


def test_warring_only_defaults_to_true():
    # ⚠️ The common case. A post-mortem asks about the war, and the unfiltered
    # numbers sweep in every chain-filler hit made during the window.
    assert {p.name: p for p in cmd().parameters}["warring_only"].default is True


class TestValidate:
    def test_member_mode_without_a_member_names_the_parameter(self):
        # ⚠️ Discord cannot make one parameter required only for one value of
        # another, so this is checked in the handler — and the message has to
        # say what to add, or people go back to guessing.
        msg = woc.validate("member", None)
        assert msg and "member:" in msg

    def test_faction_mode_with_a_member_explains_rather_than_ignoring(self):
        # Silently dropping it would answer a different question from the one
        # asked, which is worse than refusing.
        msg = woc.validate("faction", "123")
        assert msg and "type:member" in msg

    def test_the_two_valid_combinations_pass(self):
        assert woc.validate("faction", None) is None
        assert woc.validate("member", "123") is None


class TestResolve:
    def setup_method(self):
        for t in list(chain_tenants.all_tenants()):
            chain_tenants.remove(t.slug)

    def test_defaults_only_when_there_is_exactly_one(self):
        assert woc.resolve(None)[0] is None          # none configured
        chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
        assert woc.resolve(None)[0] == "forge"
        chain_tenants.add("tnl", "https://tnl.monchoon.me", 2, 20)
        # ⚠️ With several, guessing means somebody reads another faction's war
        # and neither of them finds out.
        slug, err = woc.resolve(None)
        assert slug is None and "say which" in err

    def test_an_unknown_slug_lists_the_known_ones(self):
        chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
        slug, err = woc.resolve("nope")
        assert slug is None and "forge" in err


class TestEnablement:
    def test_gated_on_its_own_token_not_on_chain_watch(self, monkeypatch):
        # ⚠️ Independent of Chain Watch. Sharing the gate would mean turning one
        # feature on silently turns on the other.
        monkeypatch.delenv("WAR_OVERVIEW_TOKEN_FORGE", raising=False)
        monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "x" * 64)
        assert woc.enabled() is False
        monkeypatch.setenv("WAR_OVERVIEW_TOKEN_FORGE", "y" * 64)
        assert woc.enabled() is True


class TestApiTokens:
    def test_the_token_env_var_is_its_own(self, monkeypatch):
        # ⚠️ Not a reuse of CHAIN_WATCH_TOKEN_*: a leak of the board credential
        # must not also surrender a war's full attack and revive history.
        assert api.token_env("forge") == "WAR_OVERVIEW_TOKEN_FORGE"
        assert api.token_env("next-level") == "WAR_OVERVIEW_TOKEN_NEXT_LEVEL"
        monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "x" * 64)
        monkeypatch.delenv("WAR_OVERVIEW_TOKEN_FORGE", raising=False)
        assert api.token_for("forge") is None

    def test_an_unknown_slug_fetches_nothing(self, monkeypatch):
        for t in list(chain_tenants.all_tenants()):
            chain_tenants.remove(t.slug)
        monkeypatch.setenv("WAR_OVERVIEW_TOKEN_GHOST", "z" * 64)
        assert api.fetch("ghost") is None
