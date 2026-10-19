"""
The OC boards — one per faction, read from that faction's dashboard (#1019).

The board used to call Torn itself with a key of its own, for one faction, and
judge CPR against a table copied from an old config. Now each faction's
dashboard serves its crimes, roster and published minimums
(`/api/internal/oc-watch`), and this only renders them.

⚠️ **The alert rules are unchanged** (`alerts.build_alerts`), fed the
dashboard's payload, which keeps Torn's shape for exactly that reason. What
changed is the source of the data and of each seat's minimum.

⚠️ **One faction failing must not blank another's board, or wipe overrides.**
Each tick is per faction; a faction whose dashboard did not answer keeps its
last board. Overrides for finished crimes are pruned only when every board
answered, because pruning against a partial picture deletes a live override.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import discord

import alerts as alerts_mod
import chain_api
import chain_tenants
import enrich
import formatter as formatter_mod
import oc_settings
import state
from state import CprOverrideStore
from views import ManageOverridesView

log = logging.getLogger("oc_runtime")

#: How often the boards refresh. ⚠️ No slower than the dashboard's
#: `crime-occupancy` poll (a minute), which is where the data comes from.
OC_POLL_SECONDS = 60

#: Older than this and the board says so — status (hospital, travel) comes from
#: the dashboard's roster sample, which a quiet-season cadence can slow down.
STALE_MEMBERS_MS = 15 * 60_000
STALE_CRIMES_MS = 5 * 60_000


def stale_notice(payload: Dict, now_ms: int) -> Optional[str]:
    """A line for the board when the dashboard's data is old, else None."""
    notes = []
    c = payload.get("crimes_fetched_at")
    m = payload.get("members_fetched_at")
    if c is None:
        notes.append("the dashboard has not read the crimes yet")
    elif now_ms - c > STALE_CRIMES_MS:
        notes.append(f"crimes are {int((now_ms - c) / 60_000)} min old")
    if m is None:
        notes.append("member status is unknown")
    elif now_ms - m > STALE_MEMBERS_MS:
        notes.append(f"member status is {int((now_ms - m) / 60_000)} min old")
    return ("⚠️ " + "; ".join(notes) + ".") if notes else None


def build(payload: Dict, now_ts: int, overrides) -> Dict:
    """The dashboard payload → the alerts dict the formatter has always drawn."""
    members = {"members": payload.get("members") or []}
    crimes = payload.get("crimes") or []
    enriched = enrich.enrich_crimes(crimes, enrich.build_member_lookup(members), {})
    out = alerts_mod.build_alerts(
        enriched, enrich.build_status_map(members), now_ts, cpr_overrides=overrides)
    notice = stale_notice(payload, now_ts * 1000)
    if notice:
        out["notice"] = notice
    return out


def _notified(slug: str) -> Set:
    raw = (state.load_state().get(oc_settings.NOTIFIED_KEY) or {}).get(slug) or []
    out = set()
    for a in raw:
        try:
            out.add((int(a["crime_id"]), int(a["user_id"]), a["position"], a["alert_type"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _set_notified(slug: str, keys: Set) -> None:
    st = state.load_state()
    st.setdefault(oc_settings.NOTIFIED_KEY, {})[slug] = [
        {"crime_id": c, "user_id": u, "position": p, "alert_type": t} for (c, u, p, t) in sorted(keys)]
    state.save_state(st)


class OcBoards:
    def __init__(self, client: discord.Client, store: Optional[CprOverrideStore] = None):
        self.client = client
        self.store = store or CprOverrideStore()
        self.messages: Dict[str, discord.Message] = {}
        self.alerts: Dict[str, Dict] = {}
        # ⚠️ One persistent view for every board. The override menu lists
        # every faction's flagged seats — crime ids are unique across Torn, so
        # an override can never land on the wrong faction's crime.
        self.view = ManageOverridesView(store=self.store, alerts_provider=self.all_alerts)

    def all_alerts(self) -> Dict:
        crimes: List[Dict] = []
        for a in self.alerts.values():
            crimes.extend(a.get("crimes", []))
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "crimes": crimes}

    async def tick_all(self) -> None:
        oc_settings.adopt_legacy()
        now_ts = int(time.time())
        active: Set[int] = set()
        answered = 0
        complete = True
        for tenant in chain_tenants.all_tenants():
            thread_id = oc_settings.get(tenant.slug, "oc_thread_id")
            if not thread_id:
                continue
            try:
                ids = await self.tick(tenant, int(thread_id), now_ts)
            except Exception:                                  # noqa: BLE001
                # ⚠️ Never let one faction stop the others.
                log.exception("OC board for %s failed this tick", tenant.slug)
                ids = None
            if ids is None:
                complete = False
            else:
                answered += 1
                active |= ids
        if complete and answered:
            self.store.prune(active)

    async def tick(self, tenant, thread_id: int, now_ts: int) -> Optional[Set[int]]:
        """One faction. Returns its live crime ids, or None when nothing was drawn."""
        payload = await chain_api.fetch_oc(tenant)
        if payload is None:
            # Keep the last board up; chain_api has logged why.
            return None
        alerts = build(payload, now_ts, self.store.keys())
        self.alerts[tenant.slug] = alerts

        message = await self._message(tenant.slug, thread_id)
        if message is None:
            return None
        await self._wake_for_new_red(tenant.slug, message, alerts)

        embeds = formatter_mod.build_embeds(alerts, now_ts=now_ts)
        embeds[0].title = f"{formatter_mod.EMBED_TITLE} · {tenant.slug.upper()}"
        await message.edit(embeds=embeds, view=self.view)
        return {c["id"] for c in payload.get("crimes") or [] if "id" in c}

    async def _message(self, slug: str, thread_id: int) -> Optional[discord.Message]:
        cached = self.messages.get(slug)
        if cached is not None and getattr(cached.channel, "id", None) == thread_id:
            return cached
        try:
            channel = self.client.get_channel(thread_id) or await self.client.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden) as e:
            log.error("%s: OC thread %s is unreachable (%s) — set it again with /oc thread", slug, thread_id, e)
            return None
        known = oc_settings.board_message(slug)
        if known:
            try:
                message = await channel.fetch_message(known)
                self.messages[slug] = message
                return message
            except discord.NotFound:
                log.warning("%s: board message %s is gone — posting a new one", slug, known)
        placeholder = formatter_mod.build_embeds({"crimes": [], "generated_at": ""}, now_ts=int(time.time()))
        placeholder[0].description = "Starting up… first poll in progress."
        message = await channel.send(embeds=placeholder, view=self.view)
        oc_settings.set_board_message(slug, message.id)
        self.messages[slug] = message
        return message

    async def _wake_for_new_red(self, slug: str, message: discord.Message, alerts: Dict) -> None:
        """
        Re-open an archived thread when a NEW red alert appears — once per alert.

        ⚠️ Send-then-delete: the only strategy that reliably woke the thread
        (the edits were tried and kept commented out in the old bot.py).
        """
        current = formatter_mod.red_alert_keys(alerts)
        notified = _notified(slug)
        if not (current - notified):
            if notified - current:
                _set_notified(slug, current)
            return
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            _set_notified(slug, current)
            return
        try:
            tmp = await channel.send(content="·")
            await tmp.delete()
            _set_notified(slug, current)
        except discord.HTTPException as e:
            log.warning("%s: could not wake the OC thread: %s — will retry", slug, e)
