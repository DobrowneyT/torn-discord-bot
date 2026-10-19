"""
OC Watcher Discord bot — and the host for Chain Watch, member reports and
War Overview, which all ride on this one client.

The OC boards (#1019): one per faction, each in that faction's OC thread
(`/oc thread`), refreshed every minute from the faction's dashboard
(`/api/internal/oc-watch`). The bot no longer calls Torn for OCs at all — no
key of its own, no copied threshold table. See `oc_runtime`.

Run:
    source .venv/bin/activate
    python bot.py
"""

import logging
import os

import discord
from discord.ext import tasks
from dotenv import load_dotenv

import chain_runtime
import member_threads_runtime
import oc_runtime
from state import CprOverrideStore
from views import open_override_menu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oc_watcher")


class OCWatcher(discord.Client):
    """
    ⚠️ `channel_id` and `api_key` are accepted for compatibility and ignored:
    the boards are per faction now, and the old `DISCORD_CHANNEL_ID` thread is
    carried onto its faction by `oc_settings.adopt_legacy`.
    """

    def __init__(self, channel_id: int = 0, api_key: str = "", *, chain: bool = False):
        intents = discord.Intents.default()
        if chain:
            # ⚠️ Required for Chain Watch's identity matching (#784) — it is what
            # populates `guild.members`. Without it ALSO enabled in the developer
            # portal, the guild looks empty and nobody is ever auto-linked, with
            # no error to explain why.
            intents.members = True
        super().__init__(intents=intents)
        self.override_store = CprOverrideStore()
        self.oc = oc_runtime.OcBoards(self, self.override_store)
        # The one persistent override button, shared by every faction's board.
        self.view = self.oc.view
        # ⚠️ Chain Watch rides on THIS client rather than a second process.
        # Two processes on one token open two gateway connections and Discord
        # routes each interaction to only one of them — the override button
        # above would silently stop working roughly half the time, with nothing
        # logged. Opt-in: None unless a CHAIN_WATCH_TOKEN_* is configured.
        self.chain = chain_runtime.ChainRuntime(self) if chain else None
        # ⚠️ #943 — the member-report drain, on this same client for the same
        # reason. Opt-in on a forum being configured.
        self.member_reports = member_threads_runtime.MemberThreadsRuntime(self)

    async def setup_hook(self) -> None:
        # Register the persistent view so button clicks route to its callback
        # even on messages sent in a previous bot lifetime.
        self.add_view(self.view)
        self.poll_loop.start()
        if self.chain:
            # ⚠️ Guarded, and it must never take the OC boards down with it.
            try:
                await self.chain.setup()
            except Exception:                              # noqa: BLE001
                log.exception("Chain Watch failed to start — OC boards continue")
                self.chain = None

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        if self.chain:
            try:
                await self.chain.start()
            except Exception:                              # noqa: BLE001
                log.exception("Chain Watch failed to start — OC boards continue")
        # ⚠️ Separately guarded. A member-report misconfiguration must not stop
        # Chain Watch, and neither may stop the OC boards.
        if member_threads_runtime.enabled():
            try:
                await self.member_reports.start()
                log.info("Member reports enabled (a member_thread_forum_id is set)")
            except Exception:                              # noqa: BLE001
                log.exception("Member reports failed to start — everything else continues")

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        data = getattr(interaction, "data", None) or {}
        log.info(
            "Interaction received: type=%s user=%s channel=%s message=%s data=%s",
            getattr(interaction, "type", "?"),
            getattr(interaction.user, "id", "?"),
            getattr(interaction.channel, "id", "?"),
            getattr(interaction.message, "id", "?"),
            data,
        )
        if data.get("custom_id") == "oc_watcher_manage_overrides_v1":
            if not interaction.response.is_done():
                await open_override_menu(
                    interaction,
                    store=self.override_store,
                    alerts_provider=self.oc.all_alerts,
                )

    async def refresh_oc(self) -> None:
        """Redraw the OC boards now — after `/oc thread`, not a minute later."""
        await self.oc.tick_all()

    @tasks.loop(seconds=oc_runtime.OC_POLL_SECONDS)
    async def poll_loop(self) -> None:
        try:
            await self.oc.tick_all()
        except discord.HTTPException as e:
            log.error("Discord HTTP error: %s — will retry next tick", e)
        except Exception:  # noqa: BLE001
            log.exception("Unexpected error — keeping the last boards")

    @poll_loop.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN must be set in .env")

    chain = chain_runtime.chain_enabled()
    if chain:
        log.info("Chain Watch enabled (a CHAIN_WATCH_TOKEN_* is set)")
    bot = OCWatcher(chain=chain)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
