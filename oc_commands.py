"""
`/oc thread` — where a faction's OC board lives (#1019).

⚠️ Gated with `choon_auth.may_manage`, like `/rw config` and `/chain set`: it
decides where the faction's OC status is published.
"""

import logging
from typing import Optional, Union

import discord
from discord import app_commands

import choon_auth
import oc_settings
from war_overview_commands import resolve, slug_choices

log = logging.getLogger("oc_commands")


def register(tree: app_commands.CommandTree, *, on_change=None,
             guild: Optional[discord.Object] = None) -> None:
    oc = app_commands.Group(name="oc", description="The OC board")

    @oc.command(name="thread", description="Where this faction's OC board posts (leadership)")
    @app_commands.describe(
        channel="Channel or thread for the board. Omit to see the current one.",
        faction="Which faction (optional when only one is configured)")
    async def thread_cmd(interaction: discord.Interaction,
                         channel: Optional[Union[discord.TextChannel, discord.Thread]] = None,
                         faction: Optional[str] = None) -> None:
        slug, err = resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        allowed, refusal = choon_auth.may_manage(interaction, slug)
        if not allowed:
            await interaction.response.send_message(refusal, ephemeral=True)
            return
        oc_settings.adopt_legacy()
        if channel is None:
            current = oc_settings.get(slug, "oc_thread_id")
            await interaction.response.send_message(
                f"`{slug}`'s OC board is in <#{current}>." if current
                else f"`{slug}` has no OC board. Set one with `/oc thread channel:`.",
                ephemeral=True)
            return
        ok, message = oc_settings.set_value(slug, "oc_thread_id", channel.id)
        if ok:
            message = f"`{slug}`'s OC board will post in {channel.mention} within a minute."
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change is not None:
            try:
                await on_change()
            except Exception:                                  # noqa: BLE001
                log.exception("redraw after /oc thread failed")

    @thread_cmd.autocomplete("faction")
    async def faction_autocomplete(interaction: discord.Interaction, current: str):
        return slug_choices(current, interaction)

    tree.add_command(oc, guild=guild)
