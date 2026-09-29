"""
`/rw-overview` — post a war's summary into Discord (#811).

⚠️ **Nothing is counted here.** Every figure comes from the dashboard's
`/api/internal/war-overview`, which runs the same summary the page does and is
held level with it by a parity test on that side. This module resolves the
command's arguments, fetches, and renders.

⚠️ **The command TREE is shared; nothing else is.** discord.py allows exactly
one `CommandTree` per client, so `/rw-overview` necessarily registers onto the
same tree as `/chain`. That is a library constraint, not a coupling: no Chain
Watch module, table, setting or token is touched, and War Overview is enabled by
its own `WAR_OVERVIEW_TOKEN_*` independently of whether Chain Watch is on.
"""

import logging
import os
from typing import List, Optional

import discord
from discord import app_commands

import chain_tenants
import war_overview_api as api
import war_overview_format as fmt

log = logging.getLogger("war_overview_commands")


def enabled() -> bool:
    """
    ⚠️ Gated on a token being present, like Chain Watch. Registering a command
    that can only ever answer "not configured" is worse than not having it:
    somebody finds it, tries it, and files a bug against a feature nobody turned
    on.
    """
    return any(k.startswith("WAR_OVERVIEW_TOKEN_") and v
               for k, v in os.environ.items())


def slug_choices(current: str) -> List[app_commands.Choice]:
    cur = (current or "").lower()
    return [app_commands.Choice(name=t.slug, value=t.slug)
            for t in chain_tenants.all_tenants() if cur in t.slug.lower()][:25]


def resolve(faction: Optional[str]):
    """
    Which tenant. Returns (slug, error-message).

    ⚠️ Defaults only when exactly ONE tenant exists — the same rule `/chain`
    follows. With five configured, guessing means somebody reads another
    faction's war and neither of them finds out.
    """
    tenants = chain_tenants.all_tenants()
    if faction:
        if chain_tenants.get(faction) is None:
            known = ", ".join(f"`{t.slug}`" for t in tenants) or "none configured"
            return None, f"No tenant called `{faction}`. Known: {known}."
        return faction, None
    if len(tenants) == 1:
        return tenants[0].slug, None
    if not tenants:
        return None, "No factions are configured yet — add one with `/chain tenant add`."
    known = ", ".join(f"`{t.slug}`" for t in tenants)
    return None, f"Several factions are configured — say which: {known}."


def validate(mode: str, member: Optional[str]):
    """
    The member/mode combination. Returns an error string, or None.

    ⚠️ **Discord cannot make one parameter required only for one value of
    another.** So `member` is declared optional and the combination is checked
    here — and the message must name the parameter to add, because "invalid
    arguments" sends people back to guessing.
    """
    if mode == "member" and not member:
        return ("`type:member` needs a member as well — re-run it with "
                "`member:` and pick a name from the list.")
    if mode == "faction" and member:
        return ("`member:` only applies to `type:member`. Either drop it, or "
                "switch to `type:member`.")
    return None


def register(tree: app_commands.CommandTree, *, guild: Optional[discord.Object] = None) -> None:
    """Attach `/rw-overview` to an EXISTING tree — see the note at the top."""

    @tree.command(name="rw-overview",
                  description="Post a ranked war's summary — faction-wide or for one member")
    @app_commands.describe(
        war="Which war",
        type="Faction-wide, or one member",
        faction="Which faction (optional when only one is configured)",
        member="Which member (required for type:member)",
        warring_only="Count only the two warring factions (default: yes)",
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="the whole faction", value="faction"),
        app_commands.Choice(name="one member", value="member"),
    ])
    async def rw_overview(interaction: discord.Interaction,
                          war: str,
                          type: app_commands.Choice[str],
                          faction: Optional[str] = None,
                          member: Optional[str] = None,
                          warring_only: bool = True) -> None:
        slug, err = resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        mode = type.value
        bad = validate(mode, member)
        if bad:
            await interaction.response.send_message(bad, ephemeral=True)
            return

        # ⚠️ Deferred: this pulls a whole war's attacks, which is well past the
        # three seconds Discord allows before the interaction is dead.
        await interaction.response.defer()

        payload = api.fetch(slug, war=war, mode=mode, member=member,
                            warring_only="1" if warring_only else None)
        if payload is None:
            await interaction.followup.send(
                f"Could not reach `{slug}`'s dashboard, or it rejected our token. "
                f"The reason is in the bot's log.", ephemeral=True)
            return
        if payload.get("error"):
            await interaction.followup.send(
                f"`{slug}` does not have that war: {payload['error']}.", ephemeral=True)
            return

        card = fmt.build_overview(payload)
        embed = discord.Embed(title=card["title"], description=card["description"],
                              colour=card["colour"])
        embed.set_footer(text=card["footer"])
        await interaction.followup.send(embed=embed)

    @rw_overview.autocomplete("faction")
    async def _faction_ac(interaction: discord.Interaction, current: str):
        return slug_choices(current)

    @rw_overview.autocomplete("war")
    async def _war_ac(interaction: discord.Interaction, current: str):
        # ⚠️ One fetch per keystroke is why the picker payload is its own cheap
        # shape on the dashboard — asking for a war id returns the whole war.
        slug, err = resolve(interaction.namespace.faction)
        if err:
            return []
        payload = api.fetch(slug)
        if payload is None:
            return []
        return [app_commands.Choice(name=label, value=value)
                for label, value in fmt.war_choices(payload, current)]

    @rw_overview.autocomplete("member")
    async def _member_ac(interaction: discord.Interaction, current: str):
        slug, err = resolve(interaction.namespace.faction)
        if err or not interaction.namespace.war:
            return []
        payload = api.fetch(slug, war=interaction.namespace.war)
        if payload is None:
            return []
        return [app_commands.Choice(name=label, value=value)
                for label, value in fmt.member_choices(payload, current)]

    if guild is not None:
        pass  # the host syncs; nothing guild-specific here
