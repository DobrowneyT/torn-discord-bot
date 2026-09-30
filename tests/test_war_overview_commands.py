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


def test_faction_war_and_type_are_required_and_the_rest_are_not():
    params = {p.name: p for p in cmd().parameters}
    # ⚠️ `faction` is required. It used to default when exactly one tenant was
    # configured, which read as "this command belongs to that faction" — and
    # silently picks a side the moment a second one is added.
    for required in ("faction", "war", "type"):
        assert params[required].required, f"{required} should be required"
    for optional in ("member", "bins", "warring_only", "summary_only"):
        assert not params[optional].required, f"{optional} should be optional"


def test_the_parameters_are_ordered_so_each_picker_can_narrow_the_next():
    # ⚠️ ORDER IS THE INTERFACE, and it is load-bearing rather than cosmetic:
    # an autocomplete can only read arguments that come BEFORE it. `faction`
    # first is what makes the war list that faction's wars; `war` before
    # `member` is what makes the member list that war's participants. Reorder
    # these and the pickers keep working but stop narrowing, which nobody
    # notices until they are scrolling 400 names.
    names = [p.name for p in cmd().parameters]
    assert names.index("faction") < names.index("war") < names.index("member")
    assert names.index("type") < names.index("member")
    # Discord's own rule: every required option precedes every optional one.
    required = [p.required for p in cmd().parameters]
    assert required == sorted(required, reverse=True), names


def test_the_bin_size_has_autocomplete_and_defaults_to_auto():
    params = {p.name: p for p in cmd().parameters}
    assert params["bins"].autocomplete
    # ⚠️ `None`, not `"auto"` — the endpoint reads an absent `bin` as auto, so
    # the default must be the value that sends nothing.
    assert params["bins"].default in (None, discord.utils.MISSING)


class TestValidateBins:
    def test_nothing_means_auto_and_sends_no_argument(self):
        assert woc.validate_bins(None) == (None, None)
        assert woc.validate_bins("auto") == (None, None)

    def test_a_width_becomes_whole_seconds_as_a_string(self):
        # ⚠️ A string: it goes into a query string, and `5m` must reach the
        # dashboard as `300` because that is the contract the endpoint tests pin.
        assert woc.validate_bins("5m") == ("300", None)
        assert woc.validate_bins("3600") == ("3600", None)

    def test_a_typo_is_refused_before_the_fetch(self):
        # ⚠️ Refused HERE, not after a round trip. On a slow dashboard that is
        # several seconds of a deferred interaction to learn about a typo.
        value, err = woc.validate_bins("banana")
        assert value is None and err


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


class TestReadAuthorization:
    """
    `/rw-overview` shipped with NO gate (#825). Any member of the server could
    pull any configured faction's full member-by-member war history.
    """

    class _Role:
        def __init__(self, rid): self.id = rid

    def _interaction(self, uid=1, guild_id=111, roles=()):
        class R:
            def __init__(s): s.sent = []
            async def send_message(s, content=None, **kw): s.sent.append(content or kw)
        class I:
            pass
        i = I()
        i.guild_id = guild_id
        i.user = type("M", (), {"id": uid,
                                "roles": [TestReadAuthorization._Role(r) for r in roles]})()
        i.response = R()
        return i

    def _two_factions(self, tmp_path, monkeypatch):
        import state
        monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"), raising=False)
        chain_tenants._save([
            chain_tenants.Tenant("forge", "https://forge.x", 111, 1).to_dict(),
            chain_tenants.Tenant("tnl", "https://tnl.x", 222, 2).to_dict(),
        ])

    def test_a_member_cannot_read_another_guilds_faction(self, tmp_path, monkeypatch):
        import asyncio
        import choon_auth
        self._two_factions(tmp_path, monkeypatch)
        monkeypatch.delenv(choon_auth.ADMIN_ENV, raising=False)
        who = self._interaction(guild_id=111)
        cmd = cmd_for()
        asyncio.run(cmd.callback(who, faction="tnl", war="1",
                                 type=app_commands.Choice(name="f", value="faction")))
        assert "different Discord server" in str(who.response.sent[0])

    def test_the_faction_picker_hides_what_cannot_be_read(self, tmp_path, monkeypatch):
        import asyncio
        import choon_auth
        self._two_factions(tmp_path, monkeypatch)
        monkeypatch.delenv(choon_auth.ADMIN_ENV, raising=False)
        who = self._interaction(guild_id=111)
        # ⚠️ Otherwise the picker hands every member the full list of configured
        # factions, and they learn their permissions by being refused.
        cb = cmd_for()._params["faction"].autocomplete
        assert [c.value for c in asyncio.run(cb(who, ""))] == ["forge"]


def cmd_for():
    return next(c for c in build().get_commands() if c.name == "rw-overview")
