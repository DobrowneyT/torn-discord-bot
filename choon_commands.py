"""
`/choon` — the bot's own controls, above any one feature (#826).

⚠️ **Tenant management moved out of `/chain` because it was never Chain
Watch's.** `chain_tenants` is already War Overview's address book — its own
docstring calls it bot-level infrastructure — and the command name was the last
thing claiming it belonged to one feature. A third feature using it would have
made that read as plainly wrong.

⚠️ **Discord allows exactly two levels of nesting**, so `/choon faction add` is
at the limit and there is no room for a bot-name prefix on top of a domain root.
That is why the roots are per DOMAIN (`/chain`, `/rw`) rather than one root per
bot, and why only the bot-wide things live under `/choon`, where a prefix
actually earns its keep against the other bots in the server.

⚠️ **Separate roots are what make Discord's own permissions usable.** Overrides
attach to a command id, and subcommands have no ids of their own — so `/chain`,
`/rw` and `/choon` can each be given different roles in Integrations, where
`/chain tenant` could never be separated from `/chain refresh`. The in-bot gate
in `choon_auth` is still the boundary; this only decides what Discord can
usefully express on top of it.
"""

import logging
from typing import List, Optional

import discord
from discord import app_commands

import chain_settings
import chain_tenants
import choon_auth
from choon_auth import refuse as _refuse, require_manage as _may_manage

log = logging.getLogger("choon_commands")


def register(tree: app_commands.CommandTree, *,
             guild: Optional[discord.Object] = None, on_change=None) -> None:
    """
    Attach `/choon` to an EXISTING tree.

    ⚠️ One `CommandTree` per discord.py client, so `/choon`, `/chain` and `/rw`
    necessarily share one. A second tree makes Discord route interactions to
    only one of them, and the symptom is a command that silently does nothing.
    """
    choon = app_commands.Group(name="choon", description="Choonbot's own controls")

    faction = app_commands.Group(name="faction",
                                 description="Which factions this bot serves",
                                 parent=choon)

    @faction.command(name="list", description="Factions configured, and whether each can be polled")
    async def faction_list(interaction: discord.Interaction) -> None:
        # ⚠️ Narrowed to what the caller may read (#825). The full list of
        # configured factions, with each one's dashboard URL, is not something
        # every member of every server should get for free.
        visible = set(choon_auth.readable_slugs(interaction))
        tenants = [t for t in chain_tenants.all_tenants() if t.slug in visible]
        if not tenants:
            await interaction.response.send_message(
                "No factions you can see here. Add one with `/choon faction add`.",
                ephemeral=True)
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
            embed=discord.Embed(title="Factions this bot serves",
                                description="\n\n".join(lines)[:4000], color=0x3498DB),
            ephemeral=True)

    @faction.command(name="add", description="Add or update a faction")
    @app_commands.describe(
        slug="The faction's dashboard slug, e.g. forge",
        base_url="Its dashboard URL, e.g. https://forge.monchoon.me",
        board_channel="Where the standing board lives",
        ping_channel="Where shift pings go (defaults to the board channel)",
    )
    async def faction_add(interaction: discord.Interaction, slug: str, base_url: str,
                         board_channel: discord.TextChannel,
                         ping_channel: Optional[discord.TextChannel] = None) -> None:
        # ⚠️ No token parameter, deliberately. Slash command arguments are
        # visible to the client and land in logs; a tenant bearer pasted into a
        # channel is a leaked credential for that faction. It comes from the
        # environment — see chain_tenants.TOKEN_ENV.
        # ⚠️ Creating a faction is ADMIN-ONLY, and editing one takes that
        # faction's own manager check. Nobody can hold a manager role for a
        # faction that does not exist yet, so a "manager" gate on creation would
        # be a gate on nothing — anyone could add a tenant pointed at any
        # dashboard and then manage it.
        wanted = (slug or "").strip().lower()
        if chain_tenants.get(wanted) is None:
            if not choon_auth.is_admin(interaction.user.id):
                await _refuse(interaction,
                              "Adding a new faction is a bot-admin control.")
                return
        elif not await _may_manage(interaction, wanted):
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

    @faction.command(name="remove", description="Stop watching a faction")
    @app_commands.describe(slug="Which faction")
    async def faction_remove(interaction: discord.Interaction, slug: str) -> None:
        if not await _may_manage(interaction, (slug or "").strip().lower()):
            return
        ok, message = chain_tenants.remove(slug)
        await interaction.response.send_message(message, ephemeral=True)
        if ok and on_change:
            await on_change()

    @faction.command(name="role",
                    description="Which Discord roles may change a faction's settings")
    @app_commands.describe(slug="Which faction", action="Add or remove the role",
                           role="The role")
    @app_commands.choices(action=[
        app_commands.Choice(name="allow this role", value="add"),
        app_commands.Choice(name="stop allowing this role", value="remove"),
    ])
    async def faction_role(interaction: discord.Interaction, slug: str,
                          action: app_commands.Choice[str],
                          role: discord.Role) -> None:
        """
        ⚠️ **Bot-admin only, and that is the point.** Granting a role authority
        over a faction is itself a privilege-granting act; letting a faction's
        own managers extend the list would make the gate self-amending, so any
        manager could hand it to anybody.

        ⚠️ The bot-admin list stays in the environment and has NO command — see
        `choon_auth.admin_ids`. An admin list editable by a command is only as
        strong as the gate on that command, which is circular.
        """
        if not choon_auth.is_admin(interaction.user.id):
            await _refuse(interaction, "Setting a faction's roles is a bot-admin control.")
            return
        wanted = (slug or "").strip().lower()
        row = chain_tenants.get(wanted)
        if row is None:
            await _refuse(interaction, f"No faction called `{wanted}`.")
            return
        current = set(row.manager_role_ids)
        if action.value == "add":
            current.add(role.id)
        else:
            current.discard(role.id)
        ok, result = chain_tenants.set_manager_roles(wanted, sorted(current))
        if not ok:
            await _refuse(interaction, str(result))
            return
        if result:
            names = ", ".join(f"<@&{r}>" for r in result)
            body = f"`{wanted}` can now be changed by: {names}."
        else:
            # ⚠️ Says what the empty case MEANS. "Roles cleared" reads as
            # "anyone can now do this", which is the opposite of what happens.
            body = (f"`{wanted}` has no manager roles, so only a bot admin can "
                    f"change it.")
        await interaction.response.send_message(body, ephemeral=True)

    def _slug_choices(current: str, allowed=None) -> List[app_commands.Choice]:
        cur = (current or "").lower()
        return [app_commands.Choice(name=t.slug, value=t.slug)
                for t in chain_tenants.all_tenants()
                if cur in t.slug.lower() and (allowed is None or t.slug in allowed)][:25]

    async def _readable_ac(interaction: discord.Interaction, current: str):
        return _slug_choices(current, choon_auth.readable_slugs(interaction))

    async def _managed_ac(interaction: discord.Interaction, current: str):
        return _slug_choices(current, choon_auth.manageable_slugs(interaction))

    # ⚠️ `faction add` gets NO picker: its `slug` names a faction that does not
    # exist yet, so completing it from the ones that do is at best noise and at
    # worst an invitation to overwrite a live tenant. It is the same exception
    # that lived in chain_commands, carried over with the commands.
    faction_remove.autocomplete("slug")(_managed_ac)
    faction_role.autocomplete("slug")(_readable_ac)

    @choon.command(name="help", description="What this bot does, and how to drive it")
    async def choon_help(interaction: discord.Interaction) -> None:
        """⚠️ Deliberately open to everyone. A help command that refuses is how
        somebody concludes the bot is broken rather than that it is not for
        them."""
        embed = discord.Embed(
            title="Choonbot",
            description=(
                "**`/chain`** — the Chain Watch board, its settings and its pings.\n"
                "**`/rw overview`** — a ranked war's summary, faction-wide or per member.\n"
                "**`/choon faction`** — which factions this bot serves.\n\n"
                "Each faction's settings can only be changed by that faction's own "
                "manager roles, so holding one for one faction does not reach another. "
                "`/choon faction list` shows the ones you can see."),
            color=0x3498DB)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    tree.add_command(choon, guild=guild)
