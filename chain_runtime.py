"""
Chain Watch as an add-on to an existing bot (#785).

⚠️ **Why this exists rather than a second process.** Two processes sharing one
Discord token open two gateway connections, and Discord routes each interaction
to only ONE of them. The OC watcher's "Manage CPR overrides" button lives in its
process's view store; a click delivered to the other process finds no view and
silently does nothing. The bot looks alive, the button looks dead, and nothing is
logged. So Chain Watch attaches to whichever client already holds the token
instead of claiming it a second time.

Everything here is lifecycle. The decisions live in `chain_watcher`, which is
tested against a faked clock.
"""

import asyncio
import logging
import os
import time
from typing import List, Optional

import discord
from discord import app_commands

import chain_bot_sender
import chain_commands
import chain_notify
import chain_link_sync
import chain_settings
import chain_tenants
import war_overview_commands
import chain_watcher

log = logging.getLogger("chain_runtime")

DEFAULT_INTERVAL_SECONDS = 300


def guild_ids_from_env() -> List[int]:
    """
    ⚠️ Guild-scoped command sync, and it matters more than it looks.

    A GLOBAL sync (no guild) is propagated by Discord over up to an hour. During
    that hour the commands are simply not there — no error, nothing in the log,
    just a bot that appears broken to whoever is watching. Scoping to named
    guilds is effectively instant, which is what anybody setting this up or
    demoing it to their leaders actually needs.

    Junk is ignored rather than fatal: a stray comma or a pasted channel name
    must not stop a working bot from booting.
    """
    raw = os.environ.get("CHAIN_GUILD_IDS") or ""
    return [int(p) for p in raw.replace(" ", "").split(",") if p.isdigit()]


def chain_enabled() -> bool:
    """
    ⚠️ Opt-in, so adding this to a running bot changes nothing until asked.

    Presence of any CHAIN_WATCH_TOKEN_* is the signal: it is the one thing that
    cannot be supplied from Discord, so it is the honest gate.
    """
    return any(k.startswith("CHAIN_WATCH_TOKEN_") and v
               for k, v in os.environ.items())


class ChainRuntime:
    """
    Bolts Chain Watch onto a `discord.Client` that already exists.

    The host client is responsible for calling `setup()` from its `setup_hook`
    and `start()` from its `on_ready`.
    """

    def __init__(self, client: discord.Client, *, lead_role_id: int = 0,
                 guild_ids: Optional[List[int]] = None):
        self.client = client
        self.lead_role_id = lead_role_id
        self.guild_ids = guild_ids if guild_ids is not None else guild_ids_from_env()
        self.tree = app_commands.CommandTree(client)
        self.watcher = chain_watcher.ChainWatcher(
            chain_bot_sender.DiscordSender(client))
        self._task: Optional[asyncio.Task] = None
        # ⚠️ An optimisation on top of the poll loop, never a replacement. If
        # the socket is missing or a nudge is dropped, the board is at worst one
        # poll stale — where it was before this existed.
        self.notify = chain_notify.NotifyServer(self._redraw_one)

    def _adopt_legacy_lead_role(self) -> None:
        """
        Carry `CHAIN_LEAD_ROLE_ID` onto the sole tenant's manager roles (#825).

        ⚠️ **Only when there is exactly one tenant.** That variable is one
        faction's council role that happened to be serving as a global gate;
        with several tenants configured there is no way to tell whose it is, and
        guessing would hand one faction's council authority over another's board
        — the exact thing #825 removes. With several, it is logged and ignored,
        and the operator sets roles per faction with `/chain tenant role`.

        ⚠️ Never overwrites roles somebody chose — see
        `chain_tenants.adopt_legacy_lead_role`.
        """
        if not self.lead_role_id:
            return
        tenants = chain_tenants.all_tenants()
        if len(tenants) != 1:
            log.warning(
                "CHAIN_LEAD_ROLE_ID is set but %d tenants are configured — cannot tell "
                "which faction it belongs to. Authority is per faction now (#825); set "
                "each faction's roles with /chain tenant role.", len(tenants))
            return
        chain_tenants.adopt_legacy_lead_role(tenants[0].slug, self.lead_role_id)

    async def setup(self) -> None:
        self._adopt_legacy_lead_role()
        chain_commands.register(self.tree, lead_role_id=self.lead_role_id,
                                on_change=self.refresh_now,
                                sender=self.watcher.sender)

        # ⚠️ **War Overview shares this TREE and nothing else.** discord.py
        # allows exactly one CommandTree per client, so `/rw-overview` has to
        # register on the same one as `/chain`. That is a library constraint,
        # not a coupling: it touches no Chain Watch module, table, setting or
        # token, and it is gated on its OWN `WAR_OVERVIEW_TOKEN_*` so turning
        # one feature on never silently turns on the other.
        #
        # ⚠️ Registered here because this is where the tree lives. If Chain
        # Watch is ever switched off independently, `/rw-overview` needs a new
        # home — a tree of its own is NOT the answer, because a second tree on
        # one client makes Discord route interactions to only one of them.
        if war_overview_commands.enabled():
            war_overview_commands.register(self.tree)
            log.info("War Overview enabled (a WAR_OVERVIEW_TOKEN_* is set)")

        chain_link_sync.attach(self.client)
        if self.guild_ids:
            # ⚠️ copy_global_to then sync per guild. Registering against only one
            # guild leaves a second faction's server silently command-less.
            for gid in self.guild_ids:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            log.info("chain commands synced to %d guild(s) — instant", len(self.guild_ids))
        else:
            await self.tree.sync()
            log.info("chain commands synced globally — Discord may take up to an "
                     "hour to show them; set CHAIN_GUILD_IDS to make this instant")

    async def _redraw_one(self, slug: str) -> None:
        """Redraw one faction after its quiet period."""
        tenant = chain_tenants.get(slug)
        if tenant is None:
            # A nudge from a faction we do not serve. Normal on a shared box.
            return
        # ⚠️ Read the debounce per tenant each time rather than at construction,
        # so `/chain set board_debounce_seconds` applies without a restart —
        # the whole point of these being commands.
        self.notify.debounce_seconds = chain_settings.get(slug, "board_debounce_seconds")
        await self.watcher.tick(tenant, int(time.time() * 1000))

    async def start(self) -> None:
        # ⚠️ Match identities before the first board is drawn, so the first shift
        # ping after a restart is a mention rather than a bold name.
        await chain_link_sync.sync_all(self.client)
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        await self.notify.start()

    async def refresh_now(self) -> None:
        """Re-draw immediately after a setting changes rather than waiting out the
        interval — a control that takes five minutes to visibly apply feels
        broken while somebody is watching."""
        await self.watcher.tick_all(int(time.time() * 1000))

    async def _run(self) -> None:
        while not self.client.is_closed():
            try:
                await self.watcher.tick_all(int(time.time() * 1000))
            except Exception:                              # noqa: BLE001
                # ⚠️ The loop must never die. An unhandled error here stops every
                # faction's board silently and forever, and the first sign is a
                # missed shift. It must also never take the HOST bot down — the
                # OC watcher has been working for months and is not what changed.
                log.exception("chain tick failed")
            await asyncio.sleep(self.interval())

    def interval(self) -> int:
        """
        ⚠️ The SHORTEST configured refresh across factions, so a faction that
        wants a fast board gets one. Each tenant is still polled once per tick;
        a shorter interval costs HTTP calls, not messages.
        """
        tenants = chain_tenants.all_tenants()
        if not tenants:
            return DEFAULT_INTERVAL_SECONDS
        return min(chain_settings.get(t.slug, "board_refresh_seconds") for t in tenants)
