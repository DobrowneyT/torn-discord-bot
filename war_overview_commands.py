"""
`/rw overview` — post a war's summary into Discord (#811).

⚠️ **Nothing is counted here.** Every figure comes from the dashboard's
`/api/internal/war-overview`, which runs the same summary the page does and is
held level with it by a parity test on that side. This module resolves the
command's arguments, fetches, and renders.

⚠️ **The command TREE is shared; nothing else is.** discord.py allows exactly
one `CommandTree` per client, so `/rw overview` necessarily registers onto the
same tree as `/chain`. That is a library constraint, not a coupling: no Chain
Watch module, table, setting or token is touched, and War Overview is enabled by
its own `WAR_OVERVIEW_TOKEN_*` independently of whether Chain Watch is on.
"""

import asyncio
import io
import logging
import os
from typing import List, Optional

import discord
from discord import app_commands

import chain_tenants
import choon_auth
import war_overview_api as api
import war_overview_bins as binsize
import war_overview_chart as chart
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


def slug_choices(current: str, interaction=None) -> List[app_commands.Choice]:
    """
    ⚠️ Narrowed to the factions this caller may read (#825). Offering the rest
    teaches people their permissions by refusing them, and hands every member
    the full list of configured factions for free.
    """
    cur = (current or "").lower()
    allowed = None if interaction is None else set(choon_auth.readable_slugs(interaction))
    return [app_commands.Choice(name=t.slug, value=t.slug)
            for t in chain_tenants.all_tenants()
            if cur in t.slug.lower() and (allowed is None or t.slug in allowed)][:25]


def resolve(faction: Optional[str]):
    """
    Which tenant. Returns (slug, error-message).

    ⚠️ The command itself now REQUIRES `faction`, so the `None` branch is only
    reached from autocomplete, where the argument may not be filled in yet. It
    still defaults only when exactly ONE tenant exists — the same rule `/chain`
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
        return None, "No factions are configured yet — add one with `/choon faction add`."
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


def validate_bins(raw: Optional[str]):
    """
    The typed bin size → `(value-for-the-endpoint, error)`.

    `None` for the value means auto, which the endpoint treats as absent.

    ⚠️ Parsed here so a typo is refused BEFORE the fetch. Sending `banana`
    onward costs a round trip to say the same thing, and on a slow dashboard
    that is several seconds of a deferred interaction to learn you made a typo.
    """
    seconds, err = binsize.parse(raw)
    if err:
        return None, err
    return (None if seconds is None else str(seconds)), None


def register(tree: app_commands.CommandTree, *, guild: Optional[discord.Object] = None) -> None:
    """Attach `/rw overview` to an EXISTING tree — see the note at the top."""

    # ⚠️ A GROUP, not a hyphenated top-level name (#826). `/rw` leaves room for
    # `/rw payout` and `/rw summary` without a third top-level command, and it
    # gives Discord a command id of its own so `/rw` can be opened to members in
    # Integrations while `/chain` stays with leadership — overrides attach to a
    # command id, and subcommands have none.
    rw = app_commands.Group(name="rw", description="Ranked war reporting")

    @rw.command(name="overview",
                description="Post a ranked war's summary — faction-wide or for one member")
    @app_commands.describe(
        faction="Which faction's dashboard to read",
        war="Which war",
        type="Faction-wide, or one member",
        member="Which member (required for type:member)",
        bins="Bar width for the chart — the page's Bin size (default: auto)",
        warring_only="Count only the two warring factions (default: yes)",
        summary_only="Skip the chart and post just the numbers",
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="the whole faction", value="faction"),
        app_commands.Choice(name="one member", value="member"),
    ])
    # ⚠️ ORDER IS THE INTERFACE. Discord walks the parameters in declaration
    # order, and each autocomplete can only see the ones already filled in — so
    # `faction` must come first for the war list to be that faction's wars, and
    # `war` before `member` for the member list to be that war's participants.
    # Declared optional-looking arguments still have to follow the required
    # ones, which is Discord's rule, not ours.
    async def rw_overview(interaction: discord.Interaction,
                          faction: str,
                          war: str,
                          type: app_commands.Choice[str],
                          member: Optional[str] = None,
                          bins: Optional[str] = None,
                          warring_only: bool = True,
                          summary_only: bool = False) -> None:
        slug, err = resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        # ⚠️ A READ gate, added with #825. This command shipped with none at all,
        # so any member of the server could pull any configured faction's full
        # member-by-member war history. Guild-scoped rather than role-scoped:
        # anybody in a faction's own server may read its wars, and a bot admin
        # may read every faction's.
        allowed, refusal = choon_auth.may_read(interaction, slug)
        if not allowed:
            await interaction.response.send_message(refusal, ephemeral=True)
            return
        mode = type.value
        bad = validate(mode, member)
        if bad:
            await interaction.response.send_message(bad, ephemeral=True)
            return
        bin_value, bad_bin = validate_bins(bins)
        if bad_bin:
            await interaction.response.send_message(bad_bin, ephemeral=True)
            return

        # ⚠️ Deferred: this pulls a whole war's attacks, which is well past the
        # three seconds Discord allows before the interaction is dead.
        await interaction.response.defer()

        # ⚠️ OFF THE EVENT LOOP. `api.fetch` is blocking `requests` with a 30s
        # timeout, and a coroutine that blocks stalls the WHOLE bot — gateway
        # heartbeats included, which Discord eventually treats as a disconnect
        # (#832). `chain_api.fetch` has done this since the start; this path did
        # not. The timeout is right: it pulls a whole war's attacks. Running it
        # on the loop is what was wrong.
        payload = await asyncio.to_thread(
                            api.fetch, slug, war=war, mode=mode, member=member,
                            warring_only="1" if warring_only else None,
                            # None means auto, which the endpoint reads as absent.
                            bin=bin_value,
                            # ⚠️ Only asked for when it will be drawn. The
                            # series are cheap but not free, and the
                            # summary-only path is the common one.
                            chart=None if summary_only else "1")
        if payload is None:
            await interaction.followup.send(
                f"Could not reach `{slug}`'s dashboard, or it rejected our token. "
                f"The reason is in the bot's log.", ephemeral=True)
            return
        if payload.get("error"):
            # ⚠️ The dashboard's wording, not ours. It refuses for more than one
            # reason now — an unknown war, or a bin width it cannot draw — and
            # reporting both as "does not have that war" sends somebody looking
            # in the wrong place.
            await interaction.followup.send(
                f"`{slug}`: {payload['error']}", ephemeral=True)
            return

        card = fmt.build_overview(payload)
        embed = discord.Embed(title=card["title"], description=card["description"],
                              colour=card["colour"])
        embed.set_footer(text=card["footer"])

        # ⚠️ A chart that fails to draw must still leave the NUMBERS postable.
        # `render` returns None rather than raising for exactly that reason —
        # losing the summary because the picture failed is the worse trade.
        # ⚠️ Threaded for the same reason, and this one is not I/O: matplotlib
        # is CPU-bound and takes about a second on a measured 70 KB render. It
        # pays that on EVERY successful non-summary call rather than only when
        # something is wrong, which makes it the most reliable staller of the
        # four.
        png = None if summary_only else await asyncio.to_thread(
            chart.render, payload.get("chart") or {}, title=card["title"])
        if png:
            # ⚠️ `attachment://` binds the embed to the file in the SAME
            # message. A bare URL would not render, and a second message would
            # separate the picture from the numbers it belongs to.
            name = "war-overview.png"
            embed.set_image(url=f"attachment://{name}")
            await interaction.followup.send(
                embed=embed, file=discord.File(io.BytesIO(png), filename=name))
            return
        await interaction.followup.send(embed=embed)

    @rw_overview.autocomplete("faction")
    async def _faction_ac(interaction: discord.Interaction, current: str):
        return slug_choices(current, interaction)

    @rw_overview.autocomplete("bins")
    async def _bins_ac(interaction: discord.Interaction, current: str):
        # ⚠️ No fetch: this list does not depend on the war. Every autocomplete
        # fires per keystroke, and this one must never be a reason the picker
        # feels slow.
        return [app_commands.Choice(name=name, value=value)
                for name, value in binsize.choices(current)]

    @rw_overview.autocomplete("war")
    async def _war_ac(interaction: discord.Interaction, current: str):
        # ⚠️ One fetch per keystroke is why the picker payload is its own cheap
        # shape on the dashboard — asking for a war id returns the whole war.
        slug, err = resolve(interaction.namespace.faction)
        # ⚠️ Gated too. An autocomplete that answers for a faction the command
        # would refuse leaks the war list — and the opponent names — anyway.
        if err or not choon_auth.may_read(interaction, slug)[0]:
            return []
        # ⚠️ Per KEYSTROKE. One person typing here against a degraded dashboard
        # stalls the bot repeatedly, and has no idea they are doing it.
        payload = await asyncio.to_thread(api.fetch, slug)
        if payload is None:
            return []
        return [app_commands.Choice(name=label, value=value)
                for label, value in fmt.war_choices(payload, current)]

    @rw_overview.autocomplete("member")
    async def _member_ac(interaction: discord.Interaction, current: str):
        slug, err = resolve(interaction.namespace.faction)
        if err or not interaction.namespace.war:
            return []
        # ⚠️ Same reasoning as the war picker: this one lists member names.
        if not choon_auth.may_read(interaction, slug)[0]:
            return []
        payload = await asyncio.to_thread(api.fetch, slug, war=interaction.namespace.war)
        if payload is None:
            return []
        return [app_commands.Choice(name=label, value=value)
                for label, value in fmt.member_choices(payload, current)]

    tree.add_command(rw, guild=guild)
