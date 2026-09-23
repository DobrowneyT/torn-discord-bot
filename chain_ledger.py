"""
What has already been said (#785).

⚠️ **Without this the poller re-sends every ping on every cycle.** The loop
re-evaluates the same upcoming hour every `board_refresh_seconds`; a five-minute
cadence means the same person is mentioned twelve times about one shift, and a
six-hour gap horizon means seventy-two identical messages about the same empty
3am slot. The channel learns to mute the bot, and at that point the feature is
worse than nothing — leadership believes coverage is being watched.

⚠️ **It survives a restart.** A ledger held in memory means every redeploy
re-pings everybody, and redeploys happen most during the days the feature is
being tuned.

⚠️ **It prunes.** An append-only record of every ping ever sent would grow
unboundedly in a file that is rewritten in full on every save.
"""

import logging
import time
from typing import Dict

import state

log = logging.getLogger("chain_ledger")

STATE_KEY = "chain_sent"

#: Entries older than this are dropped on save.
#:
#: ⚠️ Comfortably longer than any ping's reach — the earliest a shift ping goes
#: is `flight_lead_minutes` (capped at 6h) before an hour, and gap pings look at
#: most a day ahead — so pruning can never resurrect a ping by forgetting it.
KEEP_MS = 48 * 3600_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store() -> Dict[str, Dict[str, int]]:
    return state.load_state().get(STATE_KEY, {}) or {}


def already_sent(slug: str, key: str) -> bool:
    return key in _store().get(slug, {})


def mark_sent(slug: str, key: str, now_ms: int = None) -> None:
    now_ms = _now_ms() if now_ms is None else now_ms
    store = _store()
    store.setdefault(slug, {})[key] = now_ms
    _prune(store, now_ms)
    st = state.load_state()
    st[STATE_KEY] = store
    state.save_state(st)


def _prune(store: Dict[str, Dict[str, int]], now_ms: int) -> None:
    for slug, entries in list(store.items()):
        for key, at in list(entries.items()):
            if now_ms - int(at) > KEEP_MS:
                entries.pop(key)
        if not entries:
            store.pop(slug)


def forget(slug: str) -> None:
    """Drop a faction's whole ledger — used when an event ends."""
    store = _store()
    if store.pop(slug, None) is not None:
        st = state.load_state()
        st[STATE_KEY] = store
        state.save_state(st)


def shift_key(member_id: str, hour_ms: int, kind: str = "shift") -> str:
    """
    ⚠️ Keyed on member AND hour AND kind.

    Dropping the hour re-pings nobody for their second shift of the day.
    Dropping the kind means the early travel warning and the ordinary five-minute
    reminder suppress each other — and they are different messages that both
    need to arrive.
    """
    return f"{kind}:{member_id}:{hour_ms}"


def gap_key(hour_ms: int, stage: str) -> str:
    """`stage` separates the first announcement from the last-call at T-2h."""
    return f"gap:{hour_ms}:{stage}"
