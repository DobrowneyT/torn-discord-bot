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
import chain_posts
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

    async def say(self, channel_id: int, content: str) -> Optional[int]:
        """Post a message. Returns its id so it can be revised or removed."""
        raise NotImplementedError

    async def edit(self, channel_id: int, message_id: int, content: str) -> bool:
        """Returns False when the message is gone, so the caller can forget it."""
        raise NotImplementedError

    async def delete(self, channel_id: int, message_id: int) -> None:
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
        # ⚠️ Only a cache in front of chain_posts, never the record. Holding it
        # here alone is why every restart posted a fresh board and left the old
        # one dead above it — and redeploys happen most while somebody is
        # tuning the thing and watching the channel.
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
            # ⚠️ And take the pings down. A chain that finished on day nine
            # leaves a channel full of "03:00 needs 2 slots" about hours that
            # will never happen, which is the last thing anybody reads before
            # muting the bot.
            # ⚠️ Flight warnings survive the sweep, for the same reason they
            # survive the tidy-up: payout review happens AFTER the chain ends,
            # and this is where "they were over the Atlantic" is written down.
            for post in chain_posts.forget_all(slug, keep_kinds=("flight",)):
                await self.sender.delete(post["channel_id"], post["message_id"])
            return

        # ⚠️ **The board is drawn in its own guard, and a failure here must not
        # reach the pings.** It did once, live: a KeyError while formatting the
        # projection raised out of _draw_board, so the whole tick aborted before
        # a single ping was sent — and the symptom was somebody not being told
        # their shift had started. A stale board is cosmetic; a missed shift
        # ping is a slot nobody covers.
        try:
            await self._draw_board(tenant, payload, now_ms, stale=stale)
        except Exception:                                  # noqa: BLE001
            log.exception("board draw failed for %s — pings continue", tenant.slug)

        # ⚠️ Only ever ping from a FRESH payload. Acting on a stale one would
        # mention somebody about a slot they may have dropped minutes ago, and
        # the correction never arrives because the poll is still failing.
        if not stale:
            # ⚠️ Each of these is guarded too, and in priority order. A shift
            # ping is the one somebody is waiting on; a gap ping and a tidy-up
            # are not worth letting a bug in one suppress the other two.
            for step in (self._shift_pings, self._gap_pings, self._tidy_posts):
                try:
                    await step(tenant, payload, now_ms)
                except Exception:                          # noqa: BLE001
                    log.exception("%s failed for %s — continuing",
                                  step.__name__, tenant.slug)

    async def _draw_board(self, tenant, payload: Dict, now_ms: int, *, stale: bool) -> None:
        slug = tenant.slug
        hours = [
            {**h, "watchers": _enrich(h.get("watchers", []))}
            for h in payload.get("hours", [])
        ]
        embeds = chain_formatter.build_board(
            {**payload, "hours": hours}, now_ms=now_ms,
            hours_shown=chain_settings.get(slug, "board_hours_shown"),
            compact=chain_settings.get(slug, "board_compact"),
            stale=stale)
        known = self._board_messages.get(slug)
        if known is None:
            known = chain_posts.board_message(slug)
        message_id = await self.sender.board(tenant.board_channel_id, embeds, known)
        if message_id and message_id != known:
            chain_posts.set_board_message(slug, message_id)
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
            if not mention:
                # The dry run: real messages, nobody's phone buzzing.
                watchers = [{k: v for k, v in w.items() if k != "discord_id"}
                            for w in watchers]

            # ⚠️ The travel warning fires EARLY and per person. Five minutes'
            # notice is useless to somebody over the Atlantic, and the
            # instruction — drop the slot — is theirs alone.
            flying = set()
            for watcher in watchers:
                if until > flight_ms:
                    continue
                if not chain_eta.may_miss(watcher.get("travel"), hour_start):
                    continue
                flying.add(watcher["member_id"])
                await self._send_once(
                    tenant,
                    chain_ledger.shift_key(watcher["member_id"], hour_start, "flight"),
                    chain_formatter.build_travel_warning(
                        {**watcher, "hour_start": hour_start}),
                    # ⚠️ Its own kind, so the tidy-up can leave it alone. It is
                    # the record of WHY a slot went uncovered, and payout
                    # review happens after the chain has ended.
                    hour_ms=hour_start, kind="flight")

            if until > lead_ms:
                continue

            # ⚠️ ONE message for everybody still due on this hour. Two
            # near-identical posts seconds apart, each naming the other person,
            # is how a channel learns to skip the bot.
            due = [w for w in watchers
                   if w["member_id"] not in flying
                   and not chain_ledger.already_sent(
                       slug, chain_ledger.shift_key(w["member_id"], hour_start))]
            if not due:
                continue
            content = chain_formatter.build_shift_ping(
                due,
                # ⚠️ The hour's FULL watcher list, not just the ones being
                # addressed. Without it a member who signed up after their
                # partner was already pinged is told they are alone.
                on_hour=watchers,
                hour_start=hour_start, bonus=hour.get("bonus"),
                chain=payload.get("chain"),
                lead_in_minutes=chain_settings.get(slug, "shift_lead_minutes"))
            if content is None:
                continue
            message_id = await self.sender.say(tenant.pings_to, content)
            # ⚠️ Marked only after the send, and for everybody named — a failed
            # message must leave them all to be pinged next tick rather than
            # silently marking some of them done.
            for watcher in due:
                chain_ledger.mark_sent(
                    slug, chain_ledger.shift_key(watcher["member_id"], hour_start))
            if message_id:
                chain_posts.record(slug, kind="shift", message_id=message_id,
                                   channel_id=tenant.pings_to, hours=[hour_start])

    async def _gap_pings(self, tenant, payload: Dict, now_ms: int) -> None:
        """
        ONE standing "needs cover" message per faction, reconciled every tick.

        ⚠️ **One message, not one per batch.** Posting a fresh message each time
        a new hour entered the horizon meant roughly one post an hour — about
        288 over a twelve-day chain, each still true, none of them removable
        while any hour in it was still in the future. The channel filled with
        overlapping claims and a reader could not tell which was current.

        ⚠️ **It is RE-POSTED, not merely edited, when there is something new to
        say.** An edit notifies nobody and does not move the message, so a gap
        that appeared while everyone was asleep would sit silently in the
        backlog. A new hour, or an hour crossing into last-call, earns a fresh
        post; anything else — slots filling, hours passing — is a silent edit.
        """
        slug = tenant.slug
        # ⚠️ The horizon comes from the payload, never a constant here, so the
        # channel and the dashboard page cannot disagree about "soon" (#782).
        horizon_ms = int(payload.get("gap_horizon_hours") or 6) * HOUR_MS

        gaps: List[Dict] = []
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
            gaps.append({
                "hour": hour, "open_slots": open_slots,
                "stage": "last-call" if until <= LAST_CALL_HOURS * HOUR_MS else "first",
            })

        existing = next(iter(chain_posts.posts_for(slug, "gap")), None)

        if not gaps:
            # ⚠️ Everything covered: the message has nothing left to ask for.
            if existing:
                await self.sender.delete(existing["channel_id"], existing["message_id"])
                chain_posts.forget(slug, existing["message_id"])
            return

        content = chain_formatter.build_gap_ping(gaps, last_call_hours=LAST_CALL_HOURS)
        if content is None:
            return
        hours_now = [g["hour"]["hour_start"] for g in gaps]
        stages_now = {str(g["hour"]["hour_start"]): g["stage"] for g in gaps}

        if existing is None:
            message_id = await self.sender.say(tenant.pings_to, content)
            if message_id:
                chain_posts.record(slug, kind="gap", message_id=message_id,
                                   channel_id=tenant.pings_to, hours=hours_now,
                                   content=content, stages=stages_now)
            return

        was_hours = {int(h) for h in existing.get("hours", [])}
        was_stages = existing.get("stages", {})
        appeared = [h for h in hours_now if h not in was_hours]
        # ⚠️ Compared against the RECORDED stage, not recomputed from the clock.
        # Recomputing would call every last-call hour "new" on every tick and
        # re-post forever.
        urgent = [h for h in hours_now
                  if stages_now[str(h)] == "last-call"
                  and was_stages.get(str(h)) != "last-call"]

        if appeared or urgent:
            await self.sender.delete(existing["channel_id"], existing["message_id"])
            chain_posts.forget(slug, existing["message_id"])
            message_id = await self.sender.say(tenant.pings_to, content)
            if message_id:
                chain_posts.record(slug, kind="gap", message_id=message_id,
                                   channel_id=tenant.pings_to, hours=hours_now,
                                   content=content, stages=stages_now)
            return

        # ⚠️ Only when the text actually changed. Editing every tick is a
        # Discord call per five minutes, for the length of the event, to
        # produce identical words.
        if content != existing.get("content"):
            alive = await self.sender.edit(
                existing["channel_id"], existing["message_id"], content)
            if alive:
                chain_posts.update(slug, existing["message_id"], content,
                                   hours=hours_now, stages=stages_now)
            else:
                chain_posts.forget(slug, existing["message_id"])

    async def _send_once(self, tenant, key: str, content: Optional[str],
                         hour_ms: Optional[int] = None, kind: str = "shift") -> None:
        if content is None or chain_ledger.already_sent(tenant.slug, key):
            return
        message_id = await self.sender.say(tenant.pings_to, content)
        chain_ledger.mark_sent(tenant.slug, key)
        if message_id and hour_ms is not None:
            chain_posts.record(tenant.slug, kind=kind, message_id=message_id,
                               channel_id=tenant.pings_to, hours=[hour_ms])

    async def _tidy_posts(self, tenant, payload: Dict, now_ms: int) -> None:
        """
        Remove shift pings whose hour is over.

        ⚠️ Gap posts are NOT handled here. `_gap_pings` owns the single standing
        "needs cover" message and reconciles it every tick; touching it here too
        would mean two writers for one message, and they would fight.

        ⚠️ Flight warnings are kept. They record WHY a slot went uncovered, and
        a lead asking "why did nobody hit at 04:00" is asking during payout
        review — after the chain has finished, which is exactly when a sweep
        would otherwise have deleted the evidence.
        """
        slug = tenant.slug
        # ⚠️ Minutes, and 0 is meaningful: remove the ping the moment its hour
        # ends. The old hours-based setting overloaded 0 to mean "never", so
        # the most likely wish was the one thing it could not express.
        grace_ms = chain_settings.get(slug, "ping_cleanup_minutes") * 60_000

        for post in chain_posts.posts_for(slug):
            if post.get("kind") in ("gap", "flight"):
                continue
            if all(h + HOUR_MS + grace_ms <= now_ms for h in post["hours"]):
                await self.sender.delete(post["channel_id"], post["message_id"])
                chain_posts.forget(slug, post["message_id"])

    async def _send_once(self, tenant, key: str, content: Optional[str],
                         hour_ms: Optional[int] = None, kind: str = "shift") -> None:
        if content is None or chain_ledger.already_sent(tenant.slug, key):
            return
        message_id = await self.sender.say(tenant.pings_to, content)
        chain_ledger.mark_sent(tenant.slug, key)
        if message_id and hour_ms is not None:
            chain_posts.record(tenant.slug, kind=kind, message_id=message_id,
                               channel_id=tenant.pings_to, hours=[hour_ms])

    async def _tidy_posts(self, tenant, payload: Dict, now_ms: int) -> None:
        """
        Revise or remove pings that have stopped being true.

        ⚠️ Two different ways of stopping being true, and they want different
        answers. A gap that somebody FILLED should lose that line — the message
        is a request, and the request was met. A gap whose hour has simply
        PASSED is history, and history in a ping channel is clutter.

        ⚠️ A shift ping is never revised, only removed. "Your shift starts in
        five minutes" has no live state to correct; it was true when sent.
        """
        slug = tenant.slug
        # ⚠️ Minutes, and 0 is meaningful: remove the ping the moment its hour
        # ends. The old hours-based setting overloaded 0 to mean "never", so
        # the most likely wish was the one thing it could not express.
        grace_ms = chain_settings.get(slug, "ping_cleanup_minutes") * 60_000
        by_hour = {h["hour_start"]: h for h in payload.get("hours", [])}

        for post in chain_posts.posts_for(slug):
            # ⚠️ Flight warnings are kept. They record why a slot went
            # uncovered, and a lead asking "why did nobody hit at 04:00" during
            # payout review is asking after the chain has finished.
            if post.get("kind") == "flight":
                continue
            expired = all(h + HOUR_MS + grace_ms <= now_ms for h in post["hours"])
            if expired:
                await self.sender.delete(post["channel_id"], post["message_id"])
                chain_posts.forget(slug, post["message_id"])
                continue
            # ⚠️ Gap posts are reconciled every tick by _gap_pings, which owns
            # the single standing message. Touching them here as well would
            # mean two writers for one message, and they would fight.
            if post.get("kind") == "gap":
                continue

    async def _send_once(self, tenant, key: str, content: Optional[str],
                         hour_ms: Optional[int] = None, kind: str = "shift") -> None:
        if content is None or chain_ledger.already_sent(tenant.slug, key):
            return
        message_id = await self.sender.say(tenant.pings_to, content)
        chain_ledger.mark_sent(tenant.slug, key)
        if message_id and hour_ms is not None:
            chain_posts.record(tenant.slug, kind=kind, message_id=message_id,
                               channel_id=tenant.pings_to, hours=[hour_ms])

    async def _tidy_posts(self, tenant, payload: Dict, now_ms: int) -> None:
        """
        Revise or remove pings that have stopped being true.

        ⚠️ Two different ways of stopping being true, and they want different
        answers. A gap that somebody FILLED should lose that line — the message
        is a request, and the request was met. A gap whose hour has simply
        PASSED is history, and history in a ping channel is clutter.

        ⚠️ A shift ping is never revised, only removed. "Your shift starts in
        five minutes" has no live state to correct; it was true when sent.
        """
        slug = tenant.slug
        # ⚠️ Minutes, and 0 is meaningful: remove the ping the moment its hour
        # ends. The old hours-based setting overloaded 0 to mean "never", so
        # the most likely wish was the one thing it could not express.
        grace_ms = chain_settings.get(slug, "ping_cleanup_minutes") * 60_000
        by_hour = {h["hour_start"]: h for h in payload.get("hours", [])}

        for post in chain_posts.posts_for(slug):
            # ⚠️ Flight warnings are kept. They record why a slot went
            # uncovered, and a lead asking "why did nobody hit at 04:00" during
            # payout review is asking after the chain has finished.
            if post.get("kind") == "flight":
                continue
            expired = all(h + HOUR_MS + grace_ms <= now_ms for h in post["hours"])
            if expired:
                await self.sender.delete(post["channel_id"], post["message_id"])
                chain_posts.forget(slug, post["message_id"])
                continue
            # ⚠️ Gap posts are reconciled every tick by _gap_pings, which owns
            # the single standing message. Revising them here too would mean
            # two writers for one message.
            if post.get("kind") == "gap":
                continue
            continue

            live = []
            for hour_ms in post["hours"]:
                hour = by_hour.get(hour_ms)
                # ⚠️ An hour that has fallen off the board is not "covered" —
                # we simply cannot see it any more. Left alone rather than
                # silently reported as solved.
                if hour is None or hour_ms + HOUR_MS + grace_ms <= now_ms:
                    continue
                open_slots = hour.get("open_slots")
                if open_slots is None:
                    open_slots = max(0, int(hour.get("slots_per_hour", 2))
                                     - len(hour.get("watchers", [])))
                if open_slots <= 0:
                    continue             # somebody signed up: request met
                until = hour_ms - now_ms
                live.append({
                    "hour": hour, "open_slots": open_slots,
                    "stage": "last-call" if until <= LAST_CALL_HOURS * HOUR_MS else "first",
                })

            if not live:
                await self.sender.delete(post["channel_id"], post["message_id"])
                chain_posts.forget(slug, post["message_id"])
                continue
            content = chain_formatter.build_gap_ping(
                live, last_call_hours=LAST_CALL_HOURS)
            # ⚠️ Only when it actually changed. Editing every tick is a Discord
            # call per message per five minutes for as long as the event runs,
            # to produce identical text.
            if content and content != post.get("content"):
                alive = await self.sender.edit(
                    post["channel_id"], post["message_id"], content)
                if alive:
                    chain_posts.update(slug, post["message_id"], content)
                else:
                    chain_posts.forget(slug, post["message_id"])
