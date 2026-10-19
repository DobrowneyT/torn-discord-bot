"""
`/oc thread` — where a faction's OC board lives (#1019).

⚠️ Gated with `choon_auth.may_manage`, like `/rw config` and `/chain set`: it
decides where the faction's OC status is published.
"""

import logging
from typing import Optional, Union

import discord
from discord import app_commands

import chain_api
import chain_tenants
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

    async def _managed(interaction, faction):
        slug, err = resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return None
        allowed, refusal = choon_auth.may_manage(interaction, slug)
        if not allowed:
            await interaction.response.send_message(refusal, ephemeral=True)
            return None
        return slug

    @oc.command(name="test", description="Post a harmless test suggestion to the action feed (leadership)")
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def test_cmd(interaction: discord.Interaction, faction: Optional[str] = None) -> None:
        slug = await _managed(interaction, faction)
        if slug is None:
            return
        by = getattr(interaction.user, "display_name", None) or str(interaction.user)
        r = await chain_api.oc_post(chain_tenants.get(slug), "/suggestions/test", {"by": by})
        if not r.get("ok"):
            await interaction.response.send_message(
                "The dashboard didn't take it — is it on 3.242.0 or later?", ephemeral=True)
            return
        await interaction.response.send_message(
            f"A test suggestion will appear in `{slug}`'s OC thread within a minute.", ephemeral=True)
        if on_change is not None:
            try:
                await on_change()
            except Exception:                                  # noqa: BLE001
                log.exception("redraw after /oc test failed")

    @oc.command(name="stats", description="How the action feed's suggestions have gone (leadership)")
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def stats_cmd(interaction: discord.Interaction, faction: Optional[str] = None) -> None:
        slug = await _managed(interaction, faction)
        if slug is None:
            return
        data = await chain_api.oc_get(chain_tenants.get(slug), "/suggestions/stats")
        stats = (data or {}).get("stats")
        if stats is None:
            await interaction.response.send_message("I couldn't reach the dashboard.", ephemeral=True)
            return
        if not stats:
            await interaction.response.send_message(f"`{slug}` hasn't had any suggestions yet.", ephemeral=True)
            return
        lines = []
        for kind, c in sorted(stats.items()):
            parts = [f"{c.get(k, 0)} {k}" for k in ("open", "snoozed", "done", "resolved", "expired") if c.get(k)]
            lines.append(f"**{kind}** — {c.get('posted', 0)} posted · " + (" · ".join(parts) or "none"))
        await interaction.response.send_message(
            embed=discord.Embed(title=f"Action feed — {slug}", description="\n".join(lines)[:4000], color=0x3498DB),
            ephemeral=True)

    @test_cmd.autocomplete("faction")
    async def test_faction_autocomplete(interaction: discord.Interaction, current: str):
        return slug_choices(current, interaction)

    @stats_cmd.autocomplete("faction")
    async def stats_faction_autocomplete(interaction: discord.Interaction, current: str):
        return slug_choices(current, interaction)

    @thread_cmd.autocomplete("faction")
    async def faction_autocomplete(interaction: discord.Interaction, current: str):
        return slug_choices(current, interaction)

    tree.add_command(oc, guild=guild)
