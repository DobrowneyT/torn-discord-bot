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
from typing import Optional, Tuple

import discord
from discord import app_commands

import chain_settings
import chain_tenants

log = logging.getLogger("chain_commands")


def _resolve(slug: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Which tenant a command applies to. Returns (slug, error-message).

    ⚠️ Defaults only when there is exactly ONE tenant. With five configured,
    guessing means a leader retunes another faction's board from their own
    channel and neither of them finds out — so the command asks instead.
    """
    tenants = chain_tenants.all_tenants()
    if slug:
        if chain_tenants.get(slug) is None:
            known = ", ".join(f"`{t.slug}`" for t in tenants) or "none configured"
            return None, f"No tenant called `{slug}`. Known: {known}."
        return slug, None
    if len(tenants) == 1:
        return tenants[0].slug, None
    if not tenants:
        return None, "No factions are configured yet — add one with `/chain tenant add`."
    known = ", ".join(f"`{t.slug}`" for t in tenants)
    return None, f"Several factions are configured — say which: {known}."


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
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def settings_cmd(interaction: discord.Interaction,
                           faction: Optional[str] = None) -> None:
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        values = chain_settings.all_settings(slug)
        lines = []
        for key, s in chain_settings.SETTINGS.items():
            shown = values[key]
            if s.kind == "channel":
                shown = f"<#{shown}>" if shown else "*unset*"
            lines.append(f"**`{key}`** — {shown}\n{s.help}")
        embed = discord.Embed(
            title=f"Chain Watch settings — {slug}",
            description="\n\n".join(lines)[:4000],
            color=0x3498DB,
        )
        # ⚠️ Say where the other half lives, or somebody will file a bug asking
        # why they cannot change the bonus hours from here.
        embed.set_footer(text="Bonus hours, watchers per hour, payout and the gap "
                              "horizon are dashboard settings — the bot renders them.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @chain.command(name="set", description="Change one setting, live")
    @app_commands.describe(key="Which setting", value="Its new value",
                           faction="Which faction (optional when only one is configured)")
    async def set_cmd(interaction: discord.Interaction, key: str, value: str,
                      faction: Optional[str] = None) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        ok, message = chain_settings.set_value(slug, key, value)
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change:
            await on_change()

    @chain.command(name="reset", description="Put one setting back to its default")
    @app_commands.describe(key="Which setting",
                           faction="Which faction (optional when only one is configured)")
    async def reset_cmd(interaction: discord.Interaction, key: str,
                        faction: Optional[str] = None) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        ok, message = chain_settings.reset(slug, key)
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

    # ── /chain tenant ────────────────────────────────────────────────────────
    tenant = app_commands.Group(name="tenant", description="Which factions this bot watches",
                                parent=chain)

    @tenant.command(name="list", description="Factions configured, and whether each can be polled")
    async def tenant_list(interaction: discord.Interaction) -> None:
        tenants = chain_tenants.all_tenants()
        if not tenants:
            await interaction.response.send_message(
                "No factions configured. Add one with `/chain tenant add`.", ephemeral=True)
            return
        lines = []
        for t in tenants:
            # ⚠️ Report the MISSING token here rather than only at poll time. A
            # tenant added without one simply never draws a board, which looks
            # like the bot being broken instead of a config that is incomplete.
            ok = "✅" if t.token() else f"⚠️ `{t.token_env}` not set"
            lines.append(f"**`{t.slug}`** — {t.base_url}\n"
                         f"board <#{t.board_channel_id}> · pings <#{t.pings_to}> · {ok}")
        await interaction.response.send_message(
            embed=discord.Embed(title="Chain Watch factions",
                                description="\n\n".join(lines)[:4000], color=0x3498DB),
            ephemeral=True)

    @tenant.command(name="add", description="Add or update a faction")
    @app_commands.describe(
        slug="The faction's dashboard slug, e.g. forge",
        base_url="Its dashboard URL, e.g. https://forge.monchoon.me",
        board_channel="Where the standing board lives",
        ping_channel="Where shift pings go (defaults to the board channel)",
    )
    async def tenant_add(interaction: discord.Interaction, slug: str, base_url: str,
                         board_channel: discord.TextChannel,
                         ping_channel: Optional[discord.TextChannel] = None) -> None:
        # ⚠️ No token parameter, deliberately. Slash command arguments are
        # visible to the client and land in logs; a tenant bearer pasted into a
        # channel is a leaked credential for that faction. It comes from the
        # environment — see chain_tenants.TOKEN_ENV.
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        # ⚠️ The guild is taken from WHERE THE COMMAND WAS RUN, not typed. A
        # mistyped guild id silently scopes the identity auto-match (#784) to
        # the wrong server, which links the wrong people to the wrong shifts.
        guild_id = interaction.guild_id or 0
        ok, message = chain_tenants.add(
            slug, base_url, guild_id, board_channel.id,
            ping_channel.id if ping_channel else 0)
        if ok and chain_settings.adopt_legacy(slug.strip().lower()):
            message += "\nCarried over the settings from before this bot was multi-faction."
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change:
            await on_change()

    @tenant.command(name="remove", description="Stop watching a faction")
    @app_commands.describe(slug="Which faction")
    async def tenant_remove(interaction: discord.Interaction, slug: str) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        ok, message = chain_tenants.remove(slug)
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change:
            await on_change()

    def _slug_choices(current: str):
        return [
            app_commands.Choice(name=t.slug, value=t.slug)
            for t in chain_tenants.all_tenants()
            if current.lower() in t.slug.lower()
        ][:25]

    # ⚠️ One callback per command, as above — `@cmd.autocomplete` returns the
    # command rather than the function, so a shared decorator binds only once.
    @settings_cmd.autocomplete("faction")
    async def settings_faction_autocomplete(interaction: discord.Interaction, current: str):
        return _slug_choices(current)

    @set_cmd.autocomplete("faction")
    async def set_faction_autocomplete(interaction: discord.Interaction, current: str):
        return _slug_choices(current)

    @reset_cmd.autocomplete("faction")
    async def reset_faction_autocomplete(interaction: discord.Interaction, current: str):
        return _slug_choices(current)

    @tenant_remove.autocomplete("slug")
    async def tenant_remove_autocomplete(interaction: discord.Interaction, current: str):
        return _slug_choices(current)

    @chain.command(name="refresh", description="Re-draw the board now")
    async def refresh_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Re-drawing the board.", ephemeral=True)
        if on_change:
            await on_change()

    tree.add_command(chain, guild=guild)
