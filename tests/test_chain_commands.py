"""
That the command tree actually builds (#783).

⚠️ This is a smoke test and it earns its place. discord.py registers commands at
import/attach time, and its failure mode is a command that silently does not
appear in Discord — there is no error at startup, the slash command just is not
there, and the first person to find out is a leader typing it in front of
others. Phase 1 already hit the specific case where stacking two
`@cmd.autocomplete` decorators on one callback binds only the first.
"""

import discord
from discord import app_commands

import chain_commands
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
