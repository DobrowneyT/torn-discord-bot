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
                     "chain tenant list", "chain tenant add", "chain tenant remove"):
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
