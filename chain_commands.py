"""
Slash commands for the Chain Watch surfaces.

⚠️ Every one of these changes behaviour **without a restart**. The board is
being tuned while leaders are looking at it, which is the whole point: a wording
or cadence argument settled in the channel should be applied in the channel.

⚠️ What is deliberately NOT here: anything about the event itself. Bonus hours,
watchers per hour, payout per slot and the gap horizon are dashboard controls.
Putting them here too would make two places authoritative for one number.
`/chain settings` says so, so nobody goes looking.
"""

import logging
from typing import Optional

import discord
from discord import app_commands

import chain_settings

log = logging.getLogger("chain_commands")


def _is_lead(interaction: discord.Interaction, lead_role_id: int) -> bool:
    """Leadership gate. ⚠️ Advisory only — see the note in register()."""
    if lead_role_id == 0:
        return True
    member = interaction.user
    return isinstance(member, discord.Member) and any(r.id == lead_role_id for r in member.roles)


def register(tree: app_commands.CommandTree, *, guild: Optional[discord.Object] = None,
             lead_role_id: int = 0, on_change=None) -> None:
    """
    Attach the /chain command group.

    `on_change` is called after any successful write so the running bot can
    re-draw immediately rather than waiting out its refresh interval — a setting
    that takes five minutes to visibly apply feels broken while somebody is
    watching.
    """
    chain = app_commands.Group(name="chain", description="Chain Watch board and pings")

    @chain.command(name="settings", description="Show every tunable and its current value")
    async def settings_cmd(interaction: discord.Interaction) -> None:
        values = chain_settings.all_settings()
        lines = []
        for key, s in chain_settings.SETTINGS.items():
            shown = values[key]
            if s.kind == "channel":
                shown = f"<#{shown}>" if shown else "*unset*"
            lines.append(f"**`{key}`** — {shown}\n{s.help}")
        embed = discord.Embed(
            title="Chain Watch settings",
            description="\n\n".join(lines)[:4000],
            color=0x3498DB,
        )
        # ⚠️ Say where the other half lives, or somebody will file a bug asking
        # why they cannot change the bonus hours from here.
        embed.set_footer(text="Bonus hours, watchers per hour, payout and the gap "
                              "horizon are dashboard settings — the bot renders them.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @chain.command(name="set", description="Change one setting, live")
    @app_commands.describe(key="Which setting", value="Its new value")
    async def set_cmd(interaction: discord.Interaction, key: str, value: str) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        ok, message = chain_settings.set_value(key, value)
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change:
            await on_change()

    @chain.command(name="reset", description="Put one setting back to its default")
    @app_commands.describe(key="Which setting")
    async def reset_cmd(interaction: discord.Interaction, key: str) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        ok, message = chain_settings.reset(key)
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change:
            await on_change()

    def _key_choices(current: str):
        # Typing the name of a setting that does not exist is the likeliest
        # mistake, so the names are offered rather than remembered.
        return [
            app_commands.Choice(name=k, value=k)
            for k in chain_settings.SETTINGS
            if current.lower() in k.lower()
        ][:25]

    # ⚠️ One autocomplete callback per command. `@cmd.autocomplete` returns the
    # command rather than the function, so stacking two of them on one callback
    # silently binds only the first.
    @set_cmd.autocomplete("key")
    async def set_key_autocomplete(interaction: discord.Interaction, current: str):
        return _key_choices(current)

    @reset_cmd.autocomplete("key")
    async def reset_key_autocomplete(interaction: discord.Interaction, current: str):
        return _key_choices(current)

    @chain.command(name="refresh", description="Re-draw the board now")
    async def refresh_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Re-drawing the board.", ephemeral=True)
        if on_change:
            await on_change()

    tree.add_command(chain, guild=guild)
