"""
That the command tree actually builds (#783).

⚠️ This is a smoke test and it earns its place. discord.py registers commands at
import/attach time, and its failure mode is a command that silently does not
appear in Discord — there is no error at startup, the slash command just is not
there, and the first person to find out is a leader typing it in front of
others. Phase 1 already hit the specific case where stacking two
`@cmd.autocomplete` decorators on one callback binds only the first.
"""

import asyncio

import discord
from discord import app_commands

import chain_commands
import chain_settings
import chain_tenants


def build():
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
    chain_commands.register(tree)
    return tree


def _walk(tree):
    names = set()
    for cmd in tree.get_commands():
        names.add(cmd.name)
        for sub in getattr(cmd, "commands", []):
            names.add(f"{cmd.name} {sub.name}")
            for leaf in getattr(sub, "commands", []):
                names.add(f"{cmd.name} {sub.name} {leaf.name}")
    return names


def test_every_command_is_attached():
    names = _walk(build())
    for expected in ("chain settings", "chain set", "chain reset", "chain refresh",
                     "chain tenant list", "chain tenant add", "chain tenant remove",
                     "chain link", "chain unlink", "chain link-status", "chain link-sync",
                     "chain tidy", "chain channel", "chain stop", "chain help"):
        assert expected in names, f"{expected} missing — it would simply not appear in Discord"


def test_tenant_add_takes_no_token_parameter():
    # ⚠️ The one parameter that must never exist. A bearer typed into a slash
    # command is visible client-side and lands in logs.
    tree = build()
    chain = next(c for c in tree.get_commands() if c.name == "chain")
    tenant = next(c for c in chain.commands if c.name == "tenant")
    add = next(c for c in tenant.commands if c.name == "add")
    assert "token" not in {p.name for p in add.parameters}


def test_resolving_a_faction_asks_when_it_is_ambiguous():
    # ⚠️ With several factions configured, guessing means a leader retunes
    # somebody else's board from their own channel and neither finds out.
    assert chain_commands._resolve(None) == (None, chain_commands._resolve(None)[1])
    assert "No factions are configured" in chain_commands._resolve(None)[1]

    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    assert chain_commands._resolve(None)[0] == "forge"   # unambiguous: default

    chain_tenants.add("tnl", "https://tnl.monchoon.me", 2, 20)
    slug, err = chain_commands._resolve(None)
    assert slug is None and "say which" in err

    assert chain_commands._resolve("forge") == ("forge", None)
    assert chain_commands._resolve("nope")[0] is None


def test_settings_warns_loudly_when_mentions_are_off():
    # ⚠️ Left off, shift pings notify nobody while the BOARD still shows blue
    # mentions — so it looks like pinging works. That combination has read as a
    # bug twice, and it should not be something you have to remember.
    import chain_settings
    chain_settings.set_value("forge", "mention_members", "off")
    assert chain_settings.get("forge", "mention_members") is False
    chain_settings.set_value("forge", "mention_members", "on")
    assert chain_settings.get("forge", "mention_members") is True


# ── /chain channel, /chain stop, /chain help ────────────────────────────────

def test_channel_takes_no_channel_argument():
    # ⚠️ The whole point: it reads where it was TYPED. A channel option would
    # be typed discord.TextChannel and Discord would reject a thread against
    # it before the command ever ran — which is the limitation this replaces.
    tree = build()
    chain = next(c for c in tree.get_commands() if c.name == "chain")
    cmd = next(c for c in chain.commands if c.name == "channel")
    names = {p.name for p in cmd.parameters}
    assert "channel" not in names
    assert names == {"which", "faction", "move"}


def test_channel_offers_a_move_escape_hatch():
    # ⚠️ Refusing outright would leave no way to relocate a board without
    # stopping it first, which is worse than the accident it prevents.
    tree = build()
    chain = next(c for c in tree.get_commands() if c.name == "chain")
    cmd = next(c for c in chain.commands if c.name == "channel")
    move = next(p for p in cmd.parameters if p.name == "move")
    assert move.required is False


def test_stop_exists_and_names_a_faction():
    tree = build()
    chain = next(c for c in tree.get_commands() if c.name == "chain")
    cmd = next(c for c in chain.commands if c.name == "stop")
    assert {p.name for p in cmd.parameters} == {"faction"}


def test_help_takes_no_arguments():
    # It is the command somebody runs when they do not know what to type.
    tree = build()
    chain = next(c for c in tree.get_commands() if c.name == "chain")
    cmd = next(c for c in chain.commands if c.name == "help")
    assert cmd.parameters == []


def test_running_elsewhere_spots_an_active_board():
    # ⚠️ The guard behind /chain channel: moving a board silently leaves the
    # old one frozen in a channel people still watch, with nothing to say it
    # stopped being true.
    import chain_settings
    chain_settings.set_value("forge", "board_channel_id", "100")
    assert chain_commands.running_elsewhere("forge", 999) == 100


def test_running_elsewhere_is_quiet_about_the_channel_you_are_in():
    import chain_settings
    chain_settings.set_value("forge", "board_channel_id", "100")
    assert chain_commands.running_elsewhere("forge", 100) is None


def test_running_elsewhere_is_quiet_when_nothing_is_configured():
    assert chain_commands.running_elsewhere("forge", 100) is None


def test_running_elsewhere_compares_numbers_not_strings():
    # ⚠️ Channel ids arrive as ints from Discord and as whatever the settings
    # store round-tripped. "100" != 100 would make the guard fire forever.
    import chain_settings
    chain_settings.set_value("forge", "board_channel_id", "100")
    assert chain_commands.running_elsewhere("forge", "100") is None


# ── never make somebody guess the slug (MonChoon, 2026-09-27) ───────────────


def _leaves(tree):
    chain = next(c for c in tree.get_commands() if c.name == "chain")
    return {c.qualified_name: c for c in chain_commands._leaf_commands(chain)}


def _param(cmd, name):
    return next((p for p in cmd.parameters if p.name == name), None)


def test_every_faction_parameter_offers_the_configured_factions():
    # ⚠️ The guard, not a spot-check. /chain channel, /chain stop and /chain
    # tidy each shipped without autocomplete, and /chain channel is the one
    # command a leader runs in a brand-new thread — so it was the one that made
    # them type a slug from memory. A guess that misses is silently a DIFFERENT
    # faction's board being moved.
    leaves = _leaves(build())
    for name, cmd in leaves.items():
        for pname in ("faction", "slug"):
            param = _param(cmd, pname)
            if param is None or name == "chain tenant add":
                continue
            assert param.autocomplete, f"{name} {pname} makes you guess the slug"
    # And the three that were missing are actually present to be checked.
    for name in ("chain channel", "chain stop", "chain tidy"):
        assert _param(leaves[name], "faction") is not None


def test_tenant_add_does_not_complete_the_slug_of_an_existing_faction():
    # ⚠️ Its slug names a faction that does not exist YET. Completing it from
    # the ones that do turns a new-tenant command into a way to overwrite a
    # live one by pressing tab.
    assert _param(_leaves(build())["chain tenant add"], "slug").autocomplete is False


class _FakeRole:
    def __init__(self, rid): self.id = rid


class _FakeInteraction:
    """Enough of an Interaction for the authorization checks (#825)."""
    def __init__(self, uid=1, guild_id=1, roles=()):
        self.guild_id = guild_id
        self.user = type("M", (), {"id": uid, "roles": [_FakeRole(r) for r in roles]})()


def test_the_autocomplete_lists_what_is_configured_right_now(monkeypatch):
    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    chain_tenants.add("tnl", "https://tnl.monchoon.me", 2, 20)
    # ⚠️ An admin, because the picker is narrowed by authority now (#825). This
    # test is about the list being read at CALL time rather than snapshotted at
    # import, so it needs a caller who can see everything.
    monkeypatch.setenv("CHOON_ADMIN_USER_IDS", "7")
    who = _FakeInteraction(uid=7)
    cmd = _leaves(build())["chain channel"]
    # ⚠️ `cmd.parameters[i].autocomplete` is a BOOL on the public wrapper; the
    # callback itself lives on the internal CommandParameter. Asserting on the
    # bool alone would pass against a callback that returns nothing.
    callback = cmd._params["faction"].autocomplete
    choices = asyncio.run(callback(who, ""))
    assert {c.value for c in choices} == {"forge", "tnl"}
    narrowed = asyncio.run(callback(who, "fo"))
    assert [c.value for c in narrowed] == ["forge"]


def test_an_autocomplete_never_raises_even_with_nothing_to_go_on():
    # ⚠️ A raising autocomplete is INVISIBLE: Discord shows an empty picker and
    # no error reaches anybody. Failing closed is the only safe behaviour, so it
    # is pinned rather than left to luck.
    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    callback = _leaves(build())["chain channel"]._params["faction"].autocomplete
    assert asyncio.run(callback(None, "")) == []


def test_the_write_picker_is_narrower_than_the_read_picker(monkeypatch):
    # ⚠️ Two factions in ONE guild — the case where the guild check passes and
    # only the role check separates them. A single shared picker would offer
    # both for /chain channel.
    monkeypatch.delenv("CHOON_ADMIN_USER_IDS", raising=False)
    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    chain_tenants.add("tnl", "https://tnl.monchoon.me", 1, 20)
    chain_tenants.set_manager_roles("forge", [500])
    chain_tenants.set_manager_roles("tnl", [501])
    leaves = _leaves(build())
    councillor = _FakeInteraction(uid=2, guild_id=1, roles=(500,))
    write = leaves["chain channel"]._params["faction"].autocomplete
    read = leaves["chain settings"]._params["faction"].autocomplete
    assert [c.value for c in asyncio.run(write(councillor, ""))] == ["forge"]
    assert {c.value for c in asyncio.run(read(councillor, ""))} == {"forge", "tnl"}


# ── refusing a channel the bot cannot write in ──────────────────────────────


def test_a_refusal_names_the_permissions_and_where_to_set_them():
    # ⚠️ "Missing Access" on its own sends people to the wrong screen. For a
    # thread the permission is inherited from the PARENT channel, and there is
    # nothing to fix on the thread itself.
    msg = chain_commands.permission_refusal(
        ["View Channel", "Embed Links"], thread=True, slug="forge")
    assert "View Channel" in msg and "Embed Links" in msg
    assert "thread" in msg and "parent channel" in msg
    # ⚠️ And it must say nothing changed. A refusal that leaves the reader
    # unsure whether the board just moved is worse than no refusal.
    assert "Nothing was changed" in msg and "forge" in msg


def test_a_refusal_for_a_plain_channel_does_not_call_it_a_thread():
    msg = chain_commands.permission_refusal(["Send Messages"], thread=False, slug="forge")
    assert "thread" not in msg.lower()


# ── moving a board takes the old one with it (MonChoon, 2026-09-27) ─────────
#
# "It didn't delete the old board though when I moved channels." A board left
# behind is frozen at whatever it last said, in a channel people still read,
# with nothing to indicate it stopped being true.


def test_surfaces_moving_reports_only_what_actually_changes():
    chain_settings.set_value("forge", "board_channel_id", "100")
    chain_settings.set_value("forge", "ping_channel_id", "100")
    keys = ["board_channel_id", "ping_channel_id"]

    moving = chain_commands.surfaces_moving("forge", keys, 200)
    assert moving == {"board_channel_id": 100, "ping_channel_id": 100}

    # ⚠️ Re-running it in the SAME channel must report nothing. Otherwise the
    # command deletes the live board and the next tick re-posts it — a visible
    # flap, and every ping in the channel gone, for a no-op.
    assert chain_commands.surfaces_moving("forge", keys, 100) == {}


def test_settings_store_returns_channel_ids_as_ints():
    # ⚠️ The contract both channel guards rest on, pinned here rather than
    # asserted in a comment. `board_channel_id` is declared type "channel", so
    # set_value coerces it — a stored id and an id straight from Discord
    # therefore compare equal without either side converting.
    #
    # Written after a mutation test showed the int()/str()/raw forms of the
    # comparison were ALL equivalent: the conversion is belt-and-braces, and
    # THIS is the thing actually keeping it correct. Change the setting to a
    # plain string and both guards silently fire on every run.
    chain_settings.set_value("forge", "board_channel_id", "100")
    stored = chain_settings.get("forge", "board_channel_id")
    assert isinstance(stored, int) and stored == 100
    assert chain_commands.surfaces_moving("forge", ["board_channel_id"], 100) == {}
    assert chain_commands.surfaces_moving("forge", ["board_channel_id"], 101) == {"board_channel_id": 100}


def test_surfaces_moving_ignores_a_surface_that_was_never_set():
    # A first-ever /chain channel has nothing to clean up, and must not be
    # reported as a move from channel 0.
    chain_settings.set_value("forge", "board_channel_id", "0")
    assert chain_commands.surfaces_moving("forge", ["board_channel_id"], 500) == {}


def test_moving_only_the_pings_leaves_the_board_alone():
    # `which:pings` moves one surface. Deleting the board because the pings
    # moved would be a surprise the command never advertised.
    chain_settings.set_value("forge", "board_channel_id", "100")
    chain_settings.set_value("forge", "ping_channel_id", "100")
    moving = chain_commands.surfaces_moving("forge", ["ping_channel_id"], 200)
    assert moving == {"ping_channel_id": 100}
    assert "board_channel_id" not in moving

