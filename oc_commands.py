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
import oc_reports
import oc_settings
from war_overview_commands import resolve, slug_choices

log = logging.getLogger("oc_commands")


async def _faction_choices(interaction: discord.Interaction, current: str):
    return slug_choices(current, interaction)


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

    # ── On request (#1024): nothing below posts on a schedule ────────────────
    @oc.command(name="digest", description="OC supply right now: open seats and who is waiting (leadership)")
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def digest_cmd(interaction: discord.Interaction, faction: Optional[str] = None) -> None:
        slug = await _managed(interaction, faction)
        if slug is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        d = await chain_api.oc_get(chain_tenants.get(slug), "/digest")
        if d is None:
            await interaction.followup.send("I couldn't reach the dashboard.", ephemeral=True)
            return
        await interaction.followup.send(embed=oc_reports.digest_embed(slug, d), ephemeral=True)

    @oc.command(name="report", description="How well OC crews were seated, and why crimes failed (leadership)")
    @app_commands.describe(days="How far back (1–90, default 7)",
                           faction="Which faction (optional when only one is configured)")
    async def report_cmd(interaction: discord.Interaction, days: Optional[app_commands.Range[int, 1, 90]] = 7,
                         faction: Optional[str] = None) -> None:
        slug = await _managed(interaction, faction)
        if slug is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        r = await chain_api.oc_get(chain_tenants.get(slug), f"/report?days={int(days or 7)}")
        if r is None:
            await interaction.followup.send("I couldn't reach the dashboard.", ephemeral=True)
            return
        await interaction.followup.send(embed=oc_reports.report_embed(slug, r), ephemeral=True)

    @oc.command(name="feed", description="Turn action-feed suggestions on or off (leadership)")
    @app_commands.describe(kind="Which suggestions", state="On or off. Omit to see the current settings.",
                           faction="Which faction (optional when only one is configured)")
    @app_commands.choices(
        kind=[app_commands.Choice(name=n, value=v) for n, v in (
            ("All suggestions", "all"), ("Better seating (swaps)", "swap"),
            ("Joined too low", "too_low"), ("Not in an OC", "idle"))],
        state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    async def feed_cmd(interaction: discord.Interaction, kind: Optional[app_commands.Choice[str]] = None,
                       state: Optional[app_commands.Choice[str]] = None, faction: Optional[str] = None) -> None:
        slug = await _managed(interaction, faction)
        if slug is None:
            return
        tenant = chain_tenants.get(slug)
        if kind is not None and state is not None:
            r = await chain_api.oc_post(tenant, "/feed-kinds", {"kind": kind.value, "enabled": state.value == "on"})
            kinds = r.get("kinds") if r.get("code") is None else None
        else:
            kinds = ((await chain_api.oc_get(tenant, "/feed-kinds")) or {}).get("kinds")
        if kinds is None:
            await interaction.response.send_message("I couldn't reach the dashboard.", ephemeral=True)
            return
        label = {"swap": "Better seating", "too_low": "Joined too low", "idle": "Not in an OC"}
        lines = [f"{'🟢' if v else '⚪'} {label.get(k, k)} — {'on' if v else 'off'}" for k, v in kinds.items()]
        note = ("\n\n_Switching a kind off closes its open suggestions as \"Turned off\" within a minute._"
                if kind is not None and state is not None else "")
        await interaction.response.send_message(f"**Action feed — `{slug}`**\n" + "\n".join(lines) + note, ephemeral=True)

    for _cmd in (digest_cmd, report_cmd, feed_cmd):
        _cmd.autocomplete("faction")(_faction_choices)

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
