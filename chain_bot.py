"""
The live Chain Watch bot (#785).

Thin on purpose: every decision lives in `chain_watcher`, which is testable with
a faked clock. This file is the part that cannot be — the gateway, the message
objects, the loop.

⚠️ **Separate from `bot.py`.** The OC watcher has its own key, its own cadence
and its own channel, and has been working for months. Folding Chain Watch into
it would couple two features that have no reason to move together and would put
the working one at risk for the sake of the new one.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

import discord
from discord import app_commands

import chain_commands
import chain_link_sync
import chain_settings
import chain_tenants
import chain_watcher

log = logging.getLogger("chain_bot")


class DiscordSender(chain_watcher.Sender):
    """The real thing. Everything here is I/O; nothing here decides anything."""

    def __init__(self, client: discord.Client):
        self.client = client

    async def board(self, channel_id: int, embeds, message_id: Optional[int]):
        channel = self.client.get_channel(channel_id)
        if channel is None:
            log.warning("board channel %s not visible", channel_id)
            return None
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embeds=embeds)
                return message_id
            except discord.NotFound:
                # ⚠️ Somebody deleted it. Post a fresh one rather than going
                # dark — a board that vanishes is indistinguishable from a bot
                # that died.
                log.info("board message %s is gone — posting a new one", message_id)
            except discord.HTTPException as e:
                # ⚠️ Keep the id. A transient edit failure must not make the
                # next tick post a SECOND standing board.
                log.warning("could not edit board %s: %s", message_id, e)
                return message_id
        sent = await channel.send(embeds=embeds)
        return sent.id

    async def say(self, channel_id: int, content: str) -> None:
        channel = self.client.get_channel(channel_id)
        if channel is None:
            log.warning("ping channel %s not visible", channel_id)
            return
        try:
            await channel.send(content)
        except discord.HTTPException as e:
            # ⚠️ Swallowed deliberately. A ping that cannot be delivered must
            # not raise into the tick and stop the other factions' boards.
            log.warning("could not send to %s: %s", channel_id, e)


def guild_ids_from_env() -> List[int]:
    """
    ⚠️ Guild-scoped command sync, and it matters more than it looks.

    A GLOBAL sync (no guild) is propagated by Discord over up to an hour. During
    that hour the commands simply are not there — no error, nothing in the log,
    just a bot that appears broken to whoever is watching. Scoping the sync to
    named guilds is effectively instant, which is what anybody setting this up
    or demoing it to their leaders actually needs.

    Set CHAIN_GUILD_IDS to a comma-separated list while testing. Leave it unset
    for a global sync once the command set has settled.
    """
    raw = os.environ.get("CHAIN_GUILD_IDS") or ""
    out = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            out.append(int(part))
    return out


class ChainBot(discord.Client):
    def __init__(self, lead_role_id: int = 0, guild_ids: Optional[List[int]] = None):
        intents = discord.Intents.default()
        # ⚠️ The members intent is required for identity matching (#784) — it is
        # what populates `guild.members`. Without it enabled in the developer
        # portal the guild looks empty and nobody is ever auto-linked, with no
        # error to explain why.
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.lead_role_id = lead_role_id
        self.guild_ids = guild_ids or []
        self.watcher = chain_watcher.ChainWatcher(DiscordSender(self))
        self._loop_task: Optional[asyncio.Task] = None

    async def setup_hook(self) -> None:
        chain_commands.register(self.tree, lead_role_id=self.lead_role_id,
                                on_change=self.refresh_now)
        chain_link_sync.attach(self)
        if self.guild_ids:
            # ⚠️ copy_global_to, then sync per guild. Registering the commands
            # only against one guild would make a second faction's server
            # silently command-less.
            for gid in self.guild_ids:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            log.info("commands synced to %d guild(s) — instant", len(self.guild_ids))
        else:
            await self.tree.sync()
            log.info("commands synced globally — Discord may take up to an hour "
                     "to show them; set CHAIN_GUILD_IDS to make this instant")

    async def on_ready(self) -> None:
        log.info("chain bot ready as %s, %d guild(s)", self.user, len(self.guilds))
        # ⚠️ Match identities before the first board is drawn, so the first
        # shift ping of a restart is a mention rather than a bold name.
        await chain_link_sync.sync_all(self)
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run())

    async def refresh_now(self) -> None:
        """Re-draw immediately after a setting changes, rather than waiting out
        the interval — a control that takes five minutes to visibly apply feels
        broken while somebody is watching."""
        await self.watcher.tick_all(int(time.time() * 1000))

    async def _run(self) -> None:
        while not self.is_closed():
            try:
                await self.watcher.tick_all(int(time.time() * 1000))
            except Exception:                              # noqa: BLE001
                # ⚠️ The loop must never die. An unhandled error here stops
                # every faction's board silently and forever, and the first
                # sign is a missed shift.
                log.exception("chain tick failed")
            await asyncio.sleep(self._interval())

    def _interval(self) -> int:
        """
        ⚠️ The SHORTEST configured refresh across factions, so a faction that
        wants a fast board gets one. Each tenant is still only polled once per
        tick; the cost of a shorter interval is HTTP calls, not messages.
        """
        tenants = chain_tenants.all_tenants()
        if not tenants:
            return 300
        return min(chain_settings.get(t.slug, "board_refresh_seconds") for t in tenants)


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN must be set in .env")
    lead_role_id = int(os.environ.get("CHAIN_LEAD_ROLE_ID") or 0)
    ChainBot(lead_role_id=lead_role_id,
             guild_ids=guild_ids_from_env()).run(token, log_handler=None)


if __name__ == "__main__":
    main()
