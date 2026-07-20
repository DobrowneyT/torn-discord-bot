"""
OC Watcher Discord bot.

Long-lived process:
  1. On startup, find or create the bot's single message in DISCORD_CHANNEL_ID
     (id is persisted in state.json so we can edit-in-place across restarts).
  2. Every POLL_INTERVAL_SECONDS:
       - fetch /v2/faction/crimes?cat=available + /v2/faction/members
       - enrich, build alerts, render embed
       - edit the persisted message
  3. On any API or unexpected error: log it and skip the edit so the last
     good payload stays visible to the channel.

Run:
    cd discord
    source .venv/bin/activate
    python bot.py
"""

import asyncio
import logging
import os
import time

import discord
from discord.ext import tasks
from dotenv import load_dotenv

import alerts as alerts_mod
import enrich
import formatter as formatter_mod
import state as state_mod
from config import POLL_INTERVAL_SECONDS
from item_cache import ItemNameCache
from state import CprOverrideStore
from torn_api import TornAPI, TornAPIError
from views import ManageOverridesView, open_override_menu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oc_watcher")


class OCWatcher(discord.Client):
    def __init__(self, channel_id: int, api_key: str):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.channel_id = channel_id
        self.api = TornAPI(api_key)
        self.item_cache = ItemNameCache()
        self.override_store = CprOverrideStore()
        self.message: discord.Message | None = None
        self.last_alerts: dict | None = None
        self._message_lock = asyncio.Lock()
        self.view = ManageOverridesView(
            store=self.override_store,
            alerts_provider=lambda: self.last_alerts,
        )

    async def setup_hook(self) -> None:
        # Register the persistent view so button clicks route to its callback
        # even on messages sent in a previous bot lifetime.
        self.add_view(self.view)
        self.poll_loop.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        try:
            await self._ensure_message()
        except discord.HTTPException as e:
            log.error("Failed to attach to channel %s: %s", self.channel_id, e)

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
                    alerts_provider=lambda: self.last_alerts,
                )

    async def _ensure_message(self) -> None:
        async with self._message_lock:
            if self.message is not None:
                return

            channel = self.get_channel(self.channel_id) or await self.fetch_channel(self.channel_id)

            existing_id = state_mod.get_message_id()
            if existing_id:
                try:
                    self.message = await channel.fetch_message(existing_id)
                    log.info("Reusing existing message %s in #%s", existing_id, channel)
                    return
                except discord.NotFound:
                    log.warning("Stored message id %s not found — will send a new one", existing_id)

            placeholder_embeds = formatter_mod.build_embeds(
                {"crimes": [], "generated_at": ""}, now_ts=int(time.time())
            )
            placeholder_embeds[0].description = "Starting up… first poll in progress."
            self.message = await channel.send(embeds=placeholder_embeds, view=self.view)
            state_mod.set_message_id(self.message.id)
            log.info("Sent new message %s in #%s", self.message.id, channel)

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def poll_loop(self) -> None:
        try:
            await self._tick()
        except TornAPIError as e:
            log.error("Torn API error: %s — keeping last published message", e)
        except discord.HTTPException as e:
            log.error("Discord HTTP error: %s — will retry next tick", e)
        except Exception:  # noqa: BLE001
            log.exception("Unexpected error — keeping last published message")

    @poll_loop.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()

    async def _tick(self) -> None:
        await self._ensure_message()
        if self.message is None:
            log.warning("No message to edit — skipping tick")
            return

        now_ts = int(time.time())
        loop = asyncio.get_running_loop()

        # Run the synchronous Torn API calls in the default thread pool so the
        # discord event loop isn't blocked while we wait on HTTP.
        crimes_resp, members_resp = await loop.run_in_executor(None, self._fetch_api)

        raw_crimes = crimes_resp.get("crimes", [])
        member_lookup = enrich.build_member_lookup(members_resp)
        status_map = enrich.build_status_map(members_resp)

        item_ids = enrich.collect_item_ids(raw_crimes)
        item_names = await loop.run_in_executor(
            None, self.item_cache.get_or_fetch, item_ids, self.api
        )

        # Drop any stale overrides for crimes that are no longer active before
        # the alerts dict is built (so freshly-pruned overrides don't suppress).
        active_crime_ids = {c["id"] for c in raw_crimes if "id" in c}
        self.override_store.prune(active_crime_ids)

        enriched = enrich.enrich_crimes(raw_crimes, member_lookup, item_names)
        alerts_dict = alerts_mod.build_alerts(
            enriched, status_map, now_ts,
            cpr_overrides=self.override_store.keys(),
        )
        # Cache for the manage-overrides button to read on click.
        self.last_alerts = alerts_dict

        # If anything in the message is "red" severity, keep the thread alive
        # for the next archive window. Lower-severity ticks let the thread
        # auto-archive — that's intentional, to keep the channel quiet.
        await self._ensure_thread_active(alerts_dict)

        # Always edit, even when alert content is unchanged, so the "Last
        # updated" footer acts as a watchdog you can read at a glance.
        embeds = formatter_mod.build_embeds(alerts_dict, now_ts=now_ts)
        await self.message.edit(embeds=embeds, view=self.view)
        log.info(
            "edited message %s — crimes=%d alerts=%d members=%d unavailable=%d overrides=%d",
            self.message.id, len(raw_crimes), len(alerts_dict["crimes"]),
            len(member_lookup), len(status_map), len(self.override_store.list()),
        )

    async def _ensure_thread_active(self, alerts_dict: dict) -> None:
        """Keep the thread alive only when *new* red-severity alerts appear.

        We diff the current red-alert key set against the set persisted in
        ``state.json`` (``notified_red_alerts``). Strategy C runs only for
        keys that are red NOW but were not previously notified, so a
        steady-state list of red alerts produces exactly one notification
        — not one per minute. When red alerts clear we sync the persisted
        set so the same alert can re-fire later if it returns.

        Strategies A and B (PATCHing the channel directly) are kept as
        commented blocks for future testing — neither produces a Discord
        notification but neither has actually unarchived for us yet.
        """
        if self.message is None:
            return

        current_keys = formatter_mod.red_alert_keys(alerts_dict)
        notified_keys = state_mod.get_notified_red_alerts()
        new_keys = current_keys - notified_keys
        cleared_keys = notified_keys - current_keys

        if not new_keys:
            # Either no red alerts, or every red alert is already known.
            if cleared_keys:
                state_mod.set_notified_red_alerts(current_keys)
                log.info(
                    "[thread] no NEW red alerts; %d previously-notified cleared (now %d known)",
                    len(cleared_keys), len(current_keys),
                )
            else:
                log.debug(
                    "[thread] no NEW red alerts (%d known, %d cleared)",
                    len(current_keys), len(cleared_keys),
                )
            return

        log.info(
            "[thread] %d NEW red alert(s) detected (was %d, now %d) — running Strategy C",
            len(new_keys), len(notified_keys), len(current_keys),
        )
        for k in sorted(new_keys):
            log.info("[thread]   new red: crime=%s user=%s position=%s type=%s", *k)

        cached_channel = self.message.channel
        cached_archived = getattr(cached_channel, "archived", None)

        try:
            fresh = await self.fetch_channel(self.channel_id)
        except discord.NotFound:
            log.error("[thread] fetch_channel: not found")
            return
        except discord.HTTPException as e:
            log.warning("[thread] fetch_channel failed (%s); falling back to cached", e)
            fresh = cached_channel

        if not isinstance(fresh, discord.Thread):
            # Not a thread — still record we've "notified" for these keys so
            # we don't spin trying every tick.
            state_mod.set_notified_red_alerts(current_keys)
            return

        log.info(
            "[thread] thread state: cached_archived=%s fresh_archived=%s archive_timestamp=%s",
            cached_archived, fresh.archived, getattr(fresh, "archive_timestamp", None),
        )

        # Strategies A/B/C — toggle to test each in isolation.
        # Currently: A = COMMENTED, B = COMMENTED, C = ACTIVE.

        # ===== STRATEGY A: thread.edit(archived=False) =====
        # discord.py's high-level helper. PATCHes /channels/{id} with
        # {"archived": false} via the wrapped Thread object.
        # if await self._try_unarchive(
        #     "A · thread.edit(archived=False)",
        #     lambda: fresh.edit(archived=False, reason="OC Watcher: keep alive"),
        # ):
        #     return
        # ===== END STRATEGY A =====

        # ===== STRATEGY B: http.edit_channel(archived=False) =====
        # Same PATCH endpoint as A, but goes around the cached Thread
        # wrapper by calling discord.py's HTTPClient directly. Useful if
        # A is short-circuiting because of stale wrapper state.
        # if await self._try_unarchive(
        #     "B · http.edit_channel(archived=False)",
        #     lambda: self.http.edit_channel(
        #         self.channel_id, archived=False, reason="OC Watcher raw"
        #     ),
        # ):
        #     return
        # ===== END STRATEGY B =====

        # ===== STRATEGY C: send-then-delete =====
        # Exploits Discord's "new message auto-unarchives" rule: send a
        # single dot, then immediately delete it. Visible for ~100ms in
        # clients but reliably wakes the thread.
        async def _send_and_delete():
            tmp = await fresh.send(content="·")
            await tmp.delete()
        if await self._try_unarchive("C · send-then-delete", _send_and_delete):
            # Persist current keys only on success so a failed send is
            # retried on the next tick instead of being silently swallowed.
            state_mod.set_notified_red_alerts(current_keys)
            return
        # ===== END STRATEGY C =====

        log.error("[thread] all uncommented strategies failed — will retry next tick")

    async def _try_unarchive(self, name: str, factory) -> bool:
        log.info("[thread] strategy %s — attempting", name)
        try:
            result = factory()
            if asyncio.iscoroutine(result):
                await result
            log.info("[thread] strategy %s — ✓ SUCCESS", name)
            return True
        except discord.Forbidden as e:
            log.warning("[thread] strategy %s — ✗ Forbidden: %s", name, e)
        except discord.HTTPException as e:
            log.warning(
                "[thread] strategy %s — ✗ HTTP status=%s code=%s text=%r",
                name, e.status, e.code, getattr(e, "text", str(e)),
            )
        except Exception as e:  # noqa: BLE001
            log.exception("[thread] strategy %s — ✗ Unexpected: %s", name, e)
        return False

    def _fetch_api(self):
        crimes = self.api.get_faction_crimes(
            category="available",
            filters="ready_at",
            sort="ASC",
            comment="OC_Watcher_Bot",
        )
        members = self.api.get_faction_members(comment="OC_Watcher_Bot")
        return crimes, members


def main() -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_raw = os.environ.get("DISCORD_CHANNEL_ID")
    api_key = os.environ.get("TORN_API_KEY") or os.environ.get("VITE_TORN_API_KEY")
    if not token or not channel_raw or not api_key:
        raise SystemExit(
            "DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, and TORN_API_KEY must all be set in discord/.env"
        )

    bot = OCWatcher(channel_id=int(channel_raw), api_key=api_key)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
