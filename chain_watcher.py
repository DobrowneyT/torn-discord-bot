"""
The poll loop: draw the board, decide what to say (#785).

⚠️ **All Discord I/O goes through an injected `sender`.** Not for purity — so
the decisions below can be tested with a faked clock. "Did this ping fire twice"
and "did it stay quiet after the chain ended" are exactly the questions that are
impossible to answer against a live gateway and trivial against a fake, and they
are the questions that decide whether a channel mutes the bot.
"""

import logging
from typing import Dict, List, Optional

import chain_api
import chain_eta
import chain_formatter
import chain_identity
import chain_ledger
import chain_settings
import chain_tenants

log = logging.getLogger("chain_watcher")

HOUR_MS = 3600_000

#: How long before an hour the LAST call for an empty slot goes out.
#:
#: ⚠️ Two announcements per gap, total: once when it enters the horizon and once
#: here. A six-hour horizon re-checked every five minutes would otherwise be 72
#: identical messages about the same empty 3am slot, and a channel that learns
#: to mute the bot is worse than no bot — leadership believes coverage is being
#: watched.
LAST_CALL_HOURS = 2


class Sender:
    """What the watcher needs from Discord. Implemented for real in chain_bot."""

    async def board(self, channel_id: int, embeds, message_id: Optional[int]) -> Optional[int]:
        raise NotImplementedError

    async def say(self, channel_id: int, content: str) -> None:
        raise NotImplementedError


def _enrich(watchers: List[Dict]) -> List[Dict]:
    """Attach the Discord link and a computed arrival band to each watcher."""
    out = []
    for w in chain_identity.decorate(watchers):
        travel = w.get("travel")
        if travel:
            band = chain_eta.arrival_band(travel)
            w = {**w, "travel": {**travel, "arrival": band} if band else travel}
        out.append(w)
    return out


def _event_is_over(payload: Dict) -> bool:
    """
    ⚠️ Nothing is sent for an event that has ended.

    The dashboard already goes quiet — #782 serves a null event once
    `actually_ended_at` is set — but this is checked here too, because a payload
    can be a few minutes stale and an empty-slot ping for a finished chain wakes
    somebody at 3am for nothing. That single message is how a channel decides
    the bot is not worth reading.
    """
    event = payload.get("event")
    return not event or bool(event.get("actually_ended_at"))


class ChainWatcher:
    def __init__(self, sender: Sender):
        self.sender = sender
        self._board_messages: Dict[str, Optional[int]] = {}
        #: ⚠️ The last payload that actually arrived, per faction. A failed poll
        #: must leave the previous board standing rather than blanking it: an
        #: empty board reads as "nobody is signed up", which is the one message
        #: that must never be wrong.
        self._last_good: Dict[str, Dict] = {}

    async def tick_all(self, now_ms: int) -> None:
        for tenant in chain_tenants.all_tenants():
            try:
                await self.tick(tenant, now_ms)
            except Exception:                              # noqa: BLE001
                # ⚠️ Broad on purpose, and per tenant. One faction's dashboard
                # returning something unexpected must not stop the other four
                # being polled — the failure would be silent and total.
                log.exception("chain tick failed for %s", tenant.slug)

    async def tick(self, tenant, now_ms: int) -> None:
        slug = tenant.slug
        payload = await chain_api.fetch(tenant)
        stale = payload is None
        if stale:
            payload = self._last_good.get(slug)
            if payload is None:
                return
        else:
            self._last_good[slug] = payload

        if _event_is_over(payload):
            # Clear the ledger so the next event starts clean rather than
            # inheriting suppressions keyed on hours that will never repeat.
            chain_ledger.forget(slug)
            return

        await self._draw_board(tenant, payload, now_ms, stale=stale)

        # ⚠️ Only ever ping from a FRESH payload. Acting on a stale one would
        # mention somebody about a slot they may have dropped minutes ago, and
        # the correction never arrives because the poll is still failing.
        if not stale:
            await self._shift_pings(tenant, payload, now_ms)
            await self._gap_pings(tenant, payload, now_ms)

    async def _draw_board(self, tenant, payload: Dict, now_ms: int, *, stale: bool) -> None:
        slug = tenant.slug
        hours = [
            {**h, "watchers": _enrich(h.get("watchers", []))}
            for h in payload.get("hours", [])
        ]
        embeds = chain_formatter.build_board(
            {**payload, "hours": hours}, now_ms=now_ms,
            hours_shown=chain_settings.get(slug, "board_hours_shown"),
            stale=stale)
        message_id = await self.sender.board(
            tenant.board_channel_id, embeds, self._board_messages.get(slug))
        if message_id:
            self._board_messages[slug] = message_id

    async def _shift_pings(self, tenant, payload: Dict, now_ms: int) -> None:
        slug = tenant.slug
        lead_ms = chain_settings.get(slug, "shift_lead_minutes") * 60_000
        flight_ms = chain_settings.get(slug, "flight_lead_minutes") * 60_000
        mention = chain_settings.get(slug, "mention_members")

        for hour in payload.get("hours", []):
            hour_start = hour["hour_start"]
            if hour_start <= now_ms:
                continue
            until = hour_start - now_ms
            watchers = _enrich(hour.get("watchers", []))
            for i, watcher in enumerate(watchers):
                partner = next((o for j, o in enumerate(watchers) if j != i), None)
                shift = {
                    **watcher, "hour_start": hour_start, "bonus": hour.get("bonus"),
                    "partner": partner, "chain": payload.get("chain"),
                }
                if not mention:
                    shift.pop("discord_id", None)

                # ⚠️ The travel warning fires EARLY and SEPARATELY. Five
                # minutes' notice is useless to somebody over the Atlantic —
                # the entire point is that it arrives while they or a leader
                # can still do something about it.
                if until <= flight_ms and chain_eta.may_miss(watcher.get("travel"), hour_start):
                    await self._send_once(
                        tenant, chain_ledger.shift_key(watcher["member_id"], hour_start, "flight"),
                        chain_formatter.build_shift_ping(shift))
                    continue

                if until <= lead_ms:
                    await self._send_once(
                        tenant, chain_ledger.shift_key(watcher["member_id"], hour_start),
                        chain_formatter.build_shift_ping(
                            {**shift, "travel": None},
                            lead_in_minutes=chain_settings.get(slug, "shift_lead_minutes")))

    async def _gap_pings(self, tenant, payload: Dict, now_ms: int) -> None:
        slug = tenant.slug
        # ⚠️ The horizon comes from the payload, never from a constant here, so
        # the channel and the dashboard page cannot disagree about what "soon"
        # means (#782).
        horizon_ms = int(payload.get("gap_horizon_hours") or 6) * HOUR_MS

        # ⚠️ Collected, then sent as ONE message. One post per hour was four in
        # a row the first time this ran live, because every hour inside the
        # horizon entered it at once — and four posts is how a channel learns
        # to mute the bot.
        gaps: List[Dict] = []
        keys: List[str] = []

        for hour in payload.get("hours", []):
            hour_start = hour["hour_start"]
            until = hour_start - now_ms
            if until <= 0 or until > horizon_ms:
                continue
            open_slots = hour.get("open_slots")
            if open_slots is None:
                open_slots = max(0, int(hour.get("slots_per_hour", 2))
                                 - len(hour.get("watchers", [])))
            if open_slots <= 0:
                continue
            stage = "last-call" if until <= LAST_CALL_HOURS * HOUR_MS else "first"
            # ⚠️ A gap announced on entry and still empty gets ONE more message,
            # then silence. Re-announcing every cycle is what trains a channel
            # to ignore the bot.
            if stage == "last-call" and chain_ledger.already_sent(
                    slug, chain_ledger.gap_key(hour_start, "last-call")):
                continue
            key = chain_ledger.gap_key(hour_start, stage)
            if chain_ledger.already_sent(slug, key):
                continue
            gaps.append({"hour": hour, "open_slots": open_slots, "stage": stage})
            keys.append(key)

        if not gaps:
            return
        content = chain_formatter.build_gap_ping(gaps, last_call_hours=LAST_CALL_HOURS)
        if content is None:
            return
        await self.sender.say(tenant.pings_to, content)
        # ⚠️ Marked only AFTER the send. Marking first would silently swallow
        # every gap in a tick whose message failed to deliver, and those hours
        # would then never be announced at all.
        for key in keys:
            chain_ledger.mark_sent(slug, key)

    async def _send_once(self, tenant, key: str, content: Optional[str]) -> None:
        if content is None or chain_ledger.already_sent(tenant.slug, key):
            return
        await self.sender.say(tenant.pings_to, content)
        chain_ledger.mark_sent(tenant.slug, key)
