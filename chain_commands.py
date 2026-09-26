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

import time

import chain_api
import chain_identity
import chain_posts
import chain_link_sync
import chain_settings
import chain_tenants

log = logging.getLogger("chain_commands")


def running_elsewhere(slug: str, here: int) -> Optional[int]:
    """
    The channel this faction is already posting in, if it is not `here`.

    ⚠️ Pure, so it can be tested. The guard it serves was written inside the
    command handler first, where a mutation removing it changed nothing any
    test could see — the whole check was invisible to the suite.
    """
    running = chain_settings.get(slug, "board_channel_id")
    if running and int(running) != int(here):
        return int(running)
    return None


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
             lead_role_id: int = 0, on_change=None, sender=None) -> None:
    """
    Attach the /chain command group.

    `on_change` is called after any successful write so the running bot can
    re-draw immediately rather than waiting out its refresh interval — a setting
    that takes five minutes to visibly apply feels broken while somebody is
    watching.
    """
    async def _sync(slug: str, guild: Optional[discord.Guild]):
        """
        Pull the roster and re-run the match. Returns None when unreachable.

        ⚠️ Delegates to chain_link_sync so there is ONE matcher. A second one
        living in the command handler would drift from the one that runs on
        startup, and the drift would only show up as a ping that did not arrive.
        """
        tenant = chain_tenants.get(slug)
        if tenant is None:
            return None
        return await chain_link_sync.sync_tenant(tree.client, tenant)

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
        # ⚠️ Shout about the dry-run switch. Left off, shift pings name people
        # instead of mentioning them and nobody is notified at all — and the
        # BOARD still shows blue mentions, so it looks like pinging works. That
        # combination has read as a bug twice; it should not be something you
        # have to remember you turned off.
        if not values.get("mention_members"):
            embed.description = (
                "⚠️ **`mention_members` is OFF** — shift pings name people "
                "instead of mentioning them, so nobody is notified. The board "
                "still shows mentions, but a mention in an embed never "
                "notified anybody anyway.\n"
                "Turn it on with `/chain set key:mention_members value:on`.\n\n"
            ) + (embed.description or "")
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

    # ── identity (#784) ──────────────────────────────────────────────────────
    #
    # ⚠️ These are the ESCAPE HATCH, not the primary path. The auto-match runs
    # on startup and on member-join; `/chain link` exists for the handful it
    # cannot resolve. If somebody finds themselves running it a hundred times,
    # the auto-match is broken and that is the bug to fix.

    @chain.command(name="link", description="Tell the bot which Torn member a Discord user is")
    @app_commands.describe(user="The Discord user", torn_id="Their Torn player id")
    async def link_cmd(interaction: discord.Interaction, user: discord.User,
                       torn_id: str) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        torn_id = (torn_id or "").strip()
        if not torn_id.isdigit():
            await interaction.response.send_message(
                f"`{torn_id}` is not a Torn player id.", ephemeral=True)
            return
        # ⚠️ Clear any other Torn member already pointing at this Discord user
        # before linking. One account is one person, and a silently doubled link
        # pings somebody for shifts that are not theirs.
        freed = [m for m in chain_identity.unlink_discord(user.id) if m != torn_id]
        chain_identity.link(torn_id, user.id, name=user.display_name)
        note = f" (was linked to `{', '.join(freed)}`)" if freed else ""
        await interaction.response.send_message(
            f"{user.mention} is Torn `{torn_id}`{note}. "
            "This is a manual link — the auto-match will not overwrite it.",
            ephemeral=True)

    @chain.command(name="unlink", description="Forget who a Discord user is")
    @app_commands.describe(user="The Discord user")
    async def unlink_cmd(interaction: discord.Interaction, user: discord.User) -> None:
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        freed = chain_identity.unlink_discord(user.id)
        await interaction.response.send_message(
            f"Unlinked {user.mention} from `{', '.join(freed)}`." if freed
            else f"{user.mention} was not linked to anybody.", ephemeral=True)

    @chain.command(name="link-status", description="Who is linked, and who still needs doing")
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def link_status_cmd(interaction: discord.Interaction,
                              faction: Optional[str] = None) -> None:
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        summary = await _sync(slug, interaction.guild)
        if summary is None:
            await interaction.response.send_message(
                f"Could not reach `{slug}`'s dashboard, so the roster is unknown.",
                ephemeral=True)
            return
        lines = [f"**{summary['linked']} of {summary['total']} linked.**"]
        # ⚠️ Ambiguous and simply-missing are listed SEPARATELY. They need
        # different actions — "pick which one" versus "this person is not on
        # Discord" — and collapsing them hides the easy fix in the long list.
        if summary["ambiguous"]:
            lines.append("\n**Ambiguous — say which with `/chain link`:**")
            for a in summary["ambiguous"]:
                who = ", ".join(f"<@{c}>" for c in a["candidates"])
                lines.append(f"· {a['name']} `[{a['member_id']}]` → {who}")
        if summary["unlinked"]:
            lines.append("\n**No match — link by hand, or they are not on Discord:**")
            lines.append(", ".join(f"{u['name']} `[{u['member_id']}]`"
                                   for u in summary["unlinked"])[:1500])
        await interaction.response.send_message(
            embed=discord.Embed(title=f"Chain Watch links — {slug}",
                                description="\n".join(lines)[:4000], color=0x3498DB),
            ephemeral=True)

    @chain.command(name="link-sync", description="Re-run the automatic matching now")
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def link_sync_cmd(interaction: discord.Interaction,
                            faction: Optional[str] = None) -> None:
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        summary = await _sync(slug, interaction.guild)
        if summary is None:
            await interaction.followup.send(
                f"Could not reach `{slug}`'s dashboard.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Matched **{summary['linked']} of {summary['total']}**. "
            f"{len(summary['ambiguous'])} ambiguous, {len(summary['unlinked'])} unmatched — "
            "`/chain link-status` lists them.", ephemeral=True)

    @link_status_cmd.autocomplete("faction")
    async def link_status_faction_autocomplete(interaction: discord.Interaction, current: str):
        return _slug_choices(current)

    @link_sync_cmd.autocomplete("faction")
    async def link_sync_faction_autocomplete(interaction: discord.Interaction, current: str):
        return _slug_choices(current)

    @chain.command(name="channel",
                   description="Post here — run it in the channel or thread you want")
    @app_commands.describe(
        which="Which message goes here",
        faction="Which faction (optional when only one is configured)",
        move="Move it even though it is already posting somewhere else")
    @app_commands.choices(which=[
        app_commands.Choice(name="the board", value="board"),
        app_commands.Choice(name="pings", value="pings"),
        app_commands.Choice(name="both", value="both"),
    ])
    async def channel_cmd(interaction: discord.Interaction,
                          which: app_commands.Choice[str],
                          faction: Optional[str] = None,
                          move: bool = False) -> None:
        """
        Point a faction's output at wherever this was typed.

        ⚠️ **This exists because `/chain tenant add` cannot accept a thread.**
        Its options are typed `discord.TextChannel`, and Discord rejects a
        thread against that type before the command ever runs — so a thread
        could only be configured by copying its id into `/chain set`, which
        nobody discovers. Reading `interaction.channel_id` works for a channel
        and a thread alike.
        """
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        here = interaction.channel_id
        if not here:
            await interaction.response.send_message(
                "Could not tell which channel this is.", ephemeral=True)
            return

        # ⚠️ Already running somewhere else? Say so rather than quietly moving
        # it. A board silently relocating leaves the old one frozen in a
        # channel people are still watching, and nothing tells them it stopped
        # being true. `move: true` is the deliberate way through.
        running = running_elsewhere(slug, here)
        if running and not move:
            guild = interaction.guild_id
            link = f"https://discord.com/channels/{guild}/{running}"
            await interaction.response.send_message(
                f"⚠️ `{slug}` is **already posting** in <#{running}>.\n"
                f"{link}\n\n"
                f"• Move it here: `/chain channel which:{which.value} "
                f"faction:{slug} move:True`\n"
                f"• Stop it there first: `/chain stop faction:{slug}`",
                ephemeral=True)
            return

        keys = {"board": ["board_channel_id"], "pings": ["ping_channel_id"],
                "both": ["board_channel_id", "ping_channel_id"]}[which.value]
        for key in keys:
            chain_settings.set_value(slug, key, str(here))
        # ⚠️ The tenant record holds the channels too, and the settings are what
        # the poller reads — writing only one of them leaves the two disagreeing
        # about where the board lives, which shows up as a board in the old
        # place that never updates.
        tenant = chain_tenants.get(slug)
        if tenant is not None:
            chain_tenants.add(
                tenant.slug, tenant.base_url, tenant.guild_id,
                here if "board_channel_id" in keys else tenant.board_channel_id,
                here if "ping_channel_id" in keys else tenant.ping_channel_id)

        note = ""
        if isinstance(interaction.channel, discord.Thread):
            # ⚠️ Threads auto-archive. The board is EDITED rather than re-posted,
            # and an archived thread refuses writes — so a board left in a quiet
            # thread silently stops updating. The bot un-archives before writing
            # (it holds Manage Threads), but say so rather than let it surprise.
            note = ("\n⚠️ This is a thread, so it will auto-archive when quiet. "
                    "The bot re-opens it before posting, but a thread with a short "
                    "archive time is a board that goes stale between chains.")
        await interaction.response.send_message(
            f"`{slug}` will post **{which.name}** here.{note}", ephemeral=True)
        if on_change:
            await on_change()

    @chain.command(name="stop", description="Stop a faction posting, and clear its messages")
    @app_commands.describe(faction="Which faction (optional when only one is configured)")
    async def stop_cmd(interaction: discord.Interaction,
                       faction: Optional[str] = None) -> None:
        """
        ⚠️ Takes the messages DOWN as well as stopping. A board left behind is
        frozen at whatever it last said, in a channel people still read, with
        nothing to indicate it has stopped being true — which is worse than no
        board at all.

        ⚠️ Flight warnings survive, as everywhere else: they record why a slot
        went uncovered, and that outlives the board.
        """
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        removed = 0
        board_id = chain_posts.board_message(slug)
        board_channel = chain_settings.get(slug, "board_channel_id")
        if board_id and board_channel:
            await sender.delete(board_channel, board_id)
            chain_posts.set_board_message(slug, None)
            removed += 1
        for post in chain_posts.forget_all(slug, keep_kinds=("flight",)):
            await sender.delete(post["channel_id"], post["message_id"])
            removed += 1

        for key in ("board_channel_id", "ping_channel_id"):
            chain_settings.set_value(slug, key, "0")

        await interaction.followup.send(
            f"Stopped `{slug}` and removed **{removed}** message(s). "
            f"Flight warnings were kept.\n"
            f"Start it again with `/chain channel which:both faction:{slug}` "
            f"in the channel or thread you want.",
            ephemeral=True)

    @chain.command(name="tidy",
                   description="Delete the bot's own old Chain Watch messages")
    @app_commands.describe(
        hours="Delete this bot's messages older than this many hours",
        faction="Which faction (optional when only one is configured)")
    async def tidy_cmd(interaction: discord.Interaction, hours: int = 24,
                       faction: Optional[str] = None) -> None:
        """
        One-off clean-up for messages the bot no longer tracks.

        ⚠️ **This exists because tracking started partway through.** Pings sent
        before the clean-up feature shipped were never recorded, so nothing will
        ever remove them — they are orphaned in the channel forever. Automatic
        clean-up cannot reach them; only a deliberate sweep can.

        ⚠️ **Operator-triggered on purpose.** A bot that decides by itself to
        bulk-delete channel history is one nobody can trust. The cutoff is
        yours, and it reports what it removed.
        """
        if not _is_lead(interaction, lead_role_id):
            await interaction.response.send_message(
                "That is a leadership control.", ephemeral=True)
            return
        slug, err = _resolve(faction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if hours < 1:
            # ⚠️ A floor, not a clamp to zero. `hours=0` would delete the ping
            # sent moments ago that somebody is still reading.
            await interaction.response.send_message(
                "Give it at least 1 hour — anything less would delete pings "
                "people are still reading.", ephemeral=True)
            return

        tenant = chain_tenants.get(slug)
        await interaction.response.defer(ephemeral=True)
        cutoff = int(time.time() * 1000) - hours * 3_600_000

        # ⚠️ The board is excluded explicitly. It lives in the same channel as
        # the pings unless the operator split them, and it is old BY DESIGN —
        # edited in place, never re-posted. Nothing else here would spare it.
        keep = {mid for mid in (chain_posts.board_message(slug),) if mid}
        # And anything still tracked: the automatic clean-up owns those, and
        # they may be about hours that have not happened yet.
        keep |= {p["message_id"] for p in chain_posts.posts_for(slug)}

        channels = {tenant.pings_to, tenant.board_channel_id}
        removed = 0
        for channel_id in channels:
            removed += await sender.purge_own(
                channel_id, before_ms=cutoff, keep_ids=keep)

        await interaction.followup.send(
            f"Removed **{removed}** of this bot's messages older than {hours}h "
            f"in `{slug}`. The board and anything still tracked were left alone."
            if removed else
            f"Nothing to remove in `{slug}` older than {hours}h.",
            ephemeral=True)

    @chain.command(name="help", description="What this bot does, and how to drive it")
    async def help_cmd(interaction: discord.Interaction) -> None:
        """
        ⚠️ Worth its place because none of the setup is guessable: which command
        takes a thread, which value comes from the environment and never from a
        command, and why the board is one message rather than many. Every line
        below is a thing somebody has had to be told.
        """
        tenants = chain_tenants.all_tenants()
        known = ", ".join(f"`{t.slug}`" for t in tenants) or "*none yet*"
        example = tenants[0].slug if tenants else "forge"

        setup = (
            f"**Setting up a faction**\n"
            f"1. On the dashboard box: `node db/chain-watch-token.mjs {example}`\n"
            f"2. Put it in the bot's environment as `CHAIN_WATCH_TOKEN_"
            f"{example.upper().replace('-', '_')}` and restart. "
            f"⚠️ Never through a command — arguments are visible client-side "
            f"and land in logs.\n"
            f"3. `/chain tenant add slug:{example} base_url:https://{example}"
            f".monchoon.me board_channel:#chain`\n"
        )
        running = (
            f"**Where it posts**\n"
            f"`/chain channel which:both faction:{example}` — run it **in** the "
            f"channel or thread you want. ⚠️ This is the only way to use a "
            f"thread: `/chain tenant add` cannot accept one.\n"
            f"`/chain stop faction:{example}` — stop and take the messages down.\n"
            f"`/chain tidy hours:24` — remove old messages the bot no longer "
            f"tracks.\n"
        )
        messages = (
            "**What it posts** — three messages, each one of a kind\n"
            "• **the board** — edited in place, stamped with its age\n"
            "• **needs cover** — one standing message, gone when every slot fills\n"
            "• **shift ping** — recycled each hour\n"
            "Flight warnings are kept deliberately: they record *why* a slot "
            "went uncovered.\n"
        )
        people = (
            f"**People**\n"
            f"`/chain link-status` — who is matched to a Discord account\n"
            f"`/chain link user:@them torn_id:123456` — fix one by hand\n"
            f"⚠️ Matching is automatic; `/chain link` is the escape hatch for "
            f"the few it cannot resolve.\n"
        )
        tuning = (
            "**Tuning** — `/chain settings` lists everything, `/chain set` "
            "changes it, live.\n"
            "⚠️ Bonus hours, watchers per hour, payout and the gap horizon are "
            "**dashboard** settings — the bot renders them.\n"
        )
        embed = discord.Embed(
            title="Chain Watch — how it works",
            description="\n".join([setup, running, messages, people, tuning]),
            color=0x3498DB,
        )
        embed.set_footer(text=f"Factions configured: {known}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @chain.command(name="refresh", description="Re-draw the board now")
    async def refresh_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Re-drawing the board.", ephemeral=True)
        if on_change:
            await on_change()

    tree.add_command(chain, guild=guild)
