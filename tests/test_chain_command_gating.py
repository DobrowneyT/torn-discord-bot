"""
That the gates are actually WIRED to the handlers (#825).

⚠️ `test_choon_auth.py` proves the decision function is right. This file proves
each command asks it — a different failure, and the more likely one: a correct
`may_manage` that nothing calls looks exactly like a delivered feature.
"""

import asyncio

import discord
from discord import app_commands
import pytest

import chain_commands
import chain_tenants
import choon_auth

FORGE_GUILD, TNL_GUILD = 111, 222
FORGE_ROLE, TNL_ROLE = 900, 901
ADMIN = 5150


class FakeRole:
    def __init__(self, rid): self.id = rid


class FakeResponse:
    def __init__(self, sent): self.sent = sent; self.deferred = False
    async def send_message(self, content=None, **kw):
        self.sent.append(content if content is not None else kw)
    async def defer(self, **kw): self.deferred = True
    def is_done(self): return self.deferred or bool(self.sent)


class FakeFollowup:
    def __init__(self, sent): self.sent = sent
    async def send(self, content=None, **kw):
        self.sent.append(content if content is not None else kw)


class FakeInteraction:
    def __init__(self, uid=1, guild_id=FORGE_GUILD, roles=()):
        self.guild_id = guild_id
        self.user = type("M", (), {"id": uid, "roles": [FakeRole(r) for r in roles]})()
        self.sent = []
        self.response = FakeResponse(self.sent)
        self.followup = FakeFollowup(self.sent)
        self.channel = None


@pytest.fixture(autouse=True)
def two_factions(tmp_path, monkeypatch):
    import state
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"), raising=False)
    monkeypatch.setenv(choon_auth.ADMIN_ENV, str(ADMIN))
    chain_tenants._save([
        chain_tenants.Tenant("forge", "https://forge.x", FORGE_GUILD, 1, 0, [FORGE_ROLE]).to_dict(),
        chain_tenants.Tenant("tnl", "https://tnl.x", TNL_GUILD, 2, 0, [TNL_ROLE]).to_dict(),
    ])
    yield


def leaves():
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
    chain_commands.register(tree)
    out = {}
    for cmd in tree.get_commands():
        for sub in getattr(cmd, "commands", []):
            out[f"{cmd.name} {sub.name}"] = sub
            for leaf in getattr(sub, "commands", []):
                out[f"{cmd.name} {sub.name} {leaf.name}"] = leaf
    return out


def run(cmd, interaction, **kwargs):
    """
    Invoke a handler and return everything it replied with.

    ⚠️ Exceptions past the gate are swallowed deliberately. These tests are
    about **whether the handler asked for permission**, and a handler allowed
    through then goes on to talk to Discord and a tenant's dashboard, neither of
    which exists here. Swallowing keeps the assertion on the thing under test
    instead of on how far a stubbed handler happens to get. A REFUSAL, by
    contrast, must return cleanly — asserted separately below.
    """
    try:
        asyncio.run(cmd.callback(interaction, **kwargs))
    except Exception as exc:                     # noqa: BLE001 — see the note
        interaction.crashed = exc
    return interaction.sent


def body(sent):
    """Flatten a reply, whether it came back as text or as an embed."""
    out = []
    for m in sent:
        if isinstance(m, dict) and "embed" in m:
            e = m["embed"]
            out.append(f"{e.title or ''} {e.description or ''}")
        else:
            out.append(str(m))
    return " ".join(out)


WRITE_COMMANDS = [
    ("chain stop", {}),
    ("chain tidy", {"hours": 24}),
    ("chain set", {"key": "slots_per_hour", "value": "2"}),
    ("chain reset", {"key": "slots_per_hour"}),
]


@pytest.mark.parametrize("name,extra", WRITE_COMMANDS)
def test_a_councillor_cannot_write_to_another_faction(name, extra):
    # ⚠️ The headline, per command. One handler forgetting to ask is the whole
    # exposure, so every write is checked rather than a representative one.
    who = FakeInteraction(uid=2, guild_id=FORGE_GUILD, roles=(FORGE_ROLE,))
    sent = run(leaves()[name], who, faction="tnl", **extra)
    assert sent and "different Discord server" in body(sent), name
    # ⚠️ A refusal must return cleanly, not raise on the way out.
    assert not hasattr(who, "crashed"), name


@pytest.mark.parametrize("name,extra", WRITE_COMMANDS)
def test_the_same_councillor_can_write_to_their_own(name, extra):
    who = FakeInteraction(uid=2, guild_id=FORGE_GUILD, roles=(FORGE_ROLE,))
    sent = run(leaves()[name], who, faction="forge", **extra)
    assert "different Discord server" not in body(sent), name
    assert "needs one of these roles" not in body(sent), name


def test_a_member_with_no_roles_cannot_write_to_their_own_faction():
    who = FakeInteraction(uid=3, guild_id=FORGE_GUILD, roles=())
    sent = run(leaves()["chain stop"], who, faction="forge")
    assert str(FORGE_ROLE) in body(sent)


def test_a_bot_admin_writes_to_any_faction():
    who = FakeInteraction(uid=ADMIN, guild_id=FORGE_GUILD)
    sent = run(leaves()["chain stop"], who, faction="tnl")
    assert "different Discord server" not in body(sent)


class TestReadsAreLooser:
    def test_a_member_with_no_roles_may_read_their_own_faction(self):
        who = FakeInteraction(uid=3, guild_id=FORGE_GUILD, roles=())
        sent = run(leaves()["chain settings"], who, faction="forge")
        assert "needs one of these roles" not in body(sent)

    def test_but_not_another_guilds_faction(self):
        who = FakeInteraction(uid=3, guild_id=FORGE_GUILD, roles=())
        sent = run(leaves()["chain settings"], who, faction="tnl")
        assert "different Discord server" in body(sent)

    def test_tenant_list_shows_only_what_the_caller_may_see(self):
        # ⚠️ The list carries every faction's dashboard URL.
        who = FakeInteraction(uid=3, guild_id=FORGE_GUILD, roles=())
        shown = body(run(leaves()["chain tenant list"], who))
        assert "forge" in shown and "tnl" not in shown


class TestTenantAdministration:
    def test_creating_a_new_faction_is_admin_only(self):
        # ⚠️ Nobody can hold a manager role for a faction that does not exist,
        # so a manager gate on creation would be a gate on nothing.
        who = FakeInteraction(uid=2, guild_id=FORGE_GUILD, roles=(FORGE_ROLE,))
        ch = type("C", (), {"id": 77})()
        sent = run(leaves()["chain tenant add"], who,
                   slug="brand-new", base_url="https://new.x", board_channel=ch)
        assert "bot-admin control" in body(sent)
        assert chain_tenants.get("brand-new") is None

    def test_a_councillor_may_still_edit_their_own_factions_entry(self):
        who = FakeInteraction(uid=2, guild_id=FORGE_GUILD, roles=(FORGE_ROLE,))
        ch = type("C", (), {"id": 88})()
        run(leaves()["chain tenant add"], who,
            slug="forge", base_url="https://forge.x", board_channel=ch)
        assert chain_tenants.get("forge").board_channel_id == 88

    def test_a_councillor_may_not_remove_another_faction(self):
        who = FakeInteraction(uid=2, guild_id=FORGE_GUILD, roles=(FORGE_ROLE,))
        run(leaves()["chain tenant remove"], who, slug="tnl")
        assert chain_tenants.get("tnl") is not None

    def test_granting_a_role_authority_is_admin_only(self):
        # ⚠️ Otherwise the gate is self-amending: any manager could hand their
        # faction's authority to anybody, including themselves for another.
        who = FakeInteraction(uid=2, guild_id=FORGE_GUILD, roles=(FORGE_ROLE,))
        role = type("R", (), {"id": 999})()
        choice = app_commands.Choice(name="allow this role", value="add")
        sent = run(leaves()["chain tenant role"], who,
                   slug="forge", action=choice, role=role)
        assert "bot-admin control" in body(sent)
        assert chain_tenants.get("forge").manager_role_ids == [FORGE_ROLE]

    def test_an_admin_can_grant_and_revoke(self):
        admin = FakeInteraction(uid=ADMIN, guild_id=FORGE_GUILD)
        role = type("R", (), {"id": 999})()
        run(leaves()["chain tenant role"], admin, slug="forge",
            action=app_commands.Choice(name="a", value="add"), role=role)
        assert 999 in chain_tenants.get("forge").manager_role_ids
        admin2 = FakeInteraction(uid=ADMIN, guild_id=FORGE_GUILD)
        run(leaves()["chain tenant role"], admin2, slug="forge",
            action=app_commands.Choice(name="r", value="remove"), role=role)
        assert 999 not in chain_tenants.get("forge").manager_role_ids

    def test_clearing_every_role_says_what_that_means(self):
        # ⚠️ "Roles cleared" reads as "anyone can do this now", which is the
        # opposite of what happens.
        admin = FakeInteraction(uid=ADMIN, guild_id=FORGE_GUILD)
        role = type("R", (), {"id": FORGE_ROLE})()
        sent = run(leaves()["chain tenant role"], admin, slug="forge",
                   action=app_commands.Choice(name="r", value="remove"), role=role)
        assert "only a bot admin" in body(sent)


class TestWhyThereIsNoCoarsePreFilter:
    """
    #825 proposed also setting `default_member_permissions` on the write
    commands so they would not clutter the picker for ordinary members. It
    cannot be done while they live under `/chain`, and the reason is pinned here
    because the failure is silent.
    """

    def test_discord_only_carries_the_field_on_a_top_level_command(self):
        # ⚠️ discord.py ACCEPTS `default_permissions` on a subcommand and stores
        # it on the object — but the serialised payload for that subcommand has
        # no `default_member_permissions` key, so it is never sent and never
        # applies. Somebody adding it would see no error, no test failure, and
        # no effect.
        group = app_commands.Group(name="g", description="d")

        @group.command(name="sub", description="d")
        @app_commands.default_permissions(manage_guild=True)
        async def sub(i: discord.Interaction): ...

        assert sub.default_permissions is not None, "discord.py stored it"
        tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
        payload = group.to_dict(tree)
        assert "default_member_permissions" in payload
        assert "default_member_permissions" not in payload["options"][0], (
            "if this ever starts appearing, a per-subcommand pre-filter became "
            "possible and #825's belt-and-braces step can be revisited")

    def test_so_chain_is_not_gated_at_the_group_level_either(self):
        # ⚠️ The only place the field WOULD apply is `/chain` as a whole, which
        # would hide the read commands from ordinary members. Reads are meant to
        # be open to any member of a faction's own guild (decided 2026-09-30),
        # so gating the group would break that to buy tidiness.
        tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
        chain_commands.register(tree)
        chain = next(c for c in tree.get_commands() if c.name == "chain")
        assert chain.default_permissions is None
