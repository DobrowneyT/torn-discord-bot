"""
Runtime settings for the Chain Watch surfaces — editable from Discord, no
restart, persisted to state.json.

⚠️ **The line this file draws matters more than any setting in it.**

What the bot owns is *how it talks*: how often the board refreshes, how much
lead time a shift ping gets, which channel things land in, how many hours to
show. Those are presentation, and presentation is exactly what wants tuning
live while leaders are looking at it.

What the bot does **not** own is anything about the event itself — bonus hours,
watchers per hour, payout per slot, the six-hour gap horizon. Those live on the
dashboard, and the bot renders what it is handed. Exposing them here would make
two places authoritative for one number, and the one that drifts is always the
one nobody is looking at. If a leader wants the bonus hours changed, that is a
dashboard control; the bot will show the change on its next refresh.

⚠️ The gap horizon is the sharpest case: the board and the dashboard page must
never disagree about what is unfilled, so the horizon arrives **in the payload**
rather than being configured twice.

⚠️ **Every setting is per tenant** (#783). Five factions share one bot, and a
board cadence tuned for a faction mid-chain must not change another faction's
quiet board. `board_channel_id` makes this obvious — one value for five
factions would post every board into one channel — but it is just as true of
the lead times, which are about when a given faction's members want waking.
"""

import logging
from typing import Any, Dict, Optional, Tuple

import state

log = logging.getLogger("chain_settings")

STATE_KEY = "chain_settings"


class Setting:
    def __init__(self, key: str, default: Any, kind: str, help_text: str,
                 lo: Optional[float] = None, hi: Optional[float] = None):
        self.key = key
        self.default = default
        self.kind = kind          # "int" | "bool" | "channel"
        self.help = help_text
        self.lo = lo
        self.hi = hi

    def coerce(self, raw: Any) -> Tuple[Optional[Any], Optional[str]]:
        """Parse and bound-check. Returns (value, error) — never raises at the caller."""
        if self.kind == "bool":
            s = str(raw).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True, None
            if s in ("0", "false", "no", "off"):
                return False, None
            return None, f"`{self.key}` is on/off — got `{raw}`."
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return None, f"`{self.key}` is a whole number — got `{raw}`."
        if self.lo is not None and n < self.lo:
            return None, f"`{self.key}` must be at least {self.lo:g}."
        if self.hi is not None and n > self.hi:
            return None, f"`{self.key}` must be at most {self.hi:g}."
        return n, None


SETTINGS: Dict[str, Setting] = {
    s.key: s for s in [
        Setting("board_channel_id", 0, "channel",
                "Where the standing board lives. It is edited in place, never re-posted."),
        Setting("ping_channel_id", 0, "channel",
                "Where shift pings go. Falls back to the board channel when unset."),
        Setting("board_refresh_seconds", 300, "int",
                "How often the board is re-drawn.", lo=60, hi=3600),
        Setting("board_hours_shown", 24, "int",
                "How many upcoming hours the board lists.", lo=4, hi=72),
        # ⚠️ The debounce for dashboard nudges. Leadership filling a rota
        # assigns eight slots in twenty seconds; without coalescing that is
        # eight redraws and a board that flickers while somebody works.
        # ⚠️ An embed CANNOT be made wider — its width is fixed by the Discord
        # client and there is no API for it. A mention renders as the member's
        # server nickname, so "MonChoon_616 [2250591] (TNLF)" is 30 characters;
        # two of those plus the time is ~84 against the ~55-60 a row fits. This
        # is the only way to get one hour per line.
        Setting("board_compact", False, "bool",
                "Use plain Torn names on the board instead of mentions, so an "
                "hour fits on one line. Shift pings are unaffected — a mention "
                "in an embed never notified anybody anyway."),
        Setting("board_debounce_seconds", 5, "int",
                "After the dashboard says a slot changed, how long to wait for "
                "more changes before redrawing.", lo=1, hi=60),
        # ⚠️ Notification timing, which is the bot's business. The GAP HORIZON is
        # not here on purpose — it comes from the dashboard so the board and the
        # page cannot disagree about what is unfilled.
        Setting("shift_lead_minutes", 5, "int",
                "How long before a shift its watcher is pinged.", lo=1, hi=120),
        Setting("flight_lead_minutes", 60, "int",
                "Earlier warning for a watcher in the air — five minutes' notice is "
                "useless to someone over the Atlantic.", lo=5, hi=360),
        # ⚠️ PINGS only. The board always renders a mention for a linked
        # member, because a mention inside an EMBED is a blue link that does not
        # notify anybody — so it costs nothing there and reads better. Off here
        # is the dry run: real messages, nobody's phone buzzing.
        Setting("mention_members", True, "bool",
                "Ping people by mention in shift alerts. Off names them instead, "
                "without notifying. Does not affect the board, where a mention "
                "never notifies anyway."),
        # ⚠️ A ping is a request with a shelf life. Left up, the channel fills
        # with claims that are no longer true — "03:00 needs 2 slots" about an
        # hour that was covered, or that happened yesterday — and a reader
        # cannot tell which of them still hold.
        Setting("ping_cleanup_hours", 1, "int",
                "Hours after an hour has finished before its ping is removed. "
                "A gap ping is also revised as slots fill, and removed once "
                "they all do. 0 leaves every message up forever.", lo=0, hi=168),
        Setting("quiet_when_covered", False, "bool",
                "Skip the gap line entirely when every slot ahead is filled."),
    ]
}


#: The slug used before settings were scoped per tenant, and where the old flat
#: block is migrated to. See `_migrated`.
LEGACY_SLUG = "default"


def _migrated(st: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lift a pre-#783 flat settings block into the per-tenant shape.

    ⚠️ **Migrated, not reset.** The flat block holds the board channel id and
    whatever cadence somebody tuned while their leaders watched; dropping it
    silently would leave the bot posting nowhere with no error, which reads as
    the bot being broken rather than as a config that moved.

    The old values land under `LEGACY_SLUG`, which `adopt_legacy` then hands to
    whichever tenant is configured first.
    """
    block = st.get(STATE_KEY)
    if not isinstance(block, dict):
        return {}
    # Already per-tenant: every value is itself a dict of settings.
    if all(isinstance(v, dict) for v in block.values()):
        return block
    return {LEGACY_SLUG: {k: v for k, v in block.items() if k in SETTINGS}}


def _store() -> Dict[str, Dict[str, Any]]:
    return _migrated(state.load_state())


def _for(slug: str) -> Dict[str, Any]:
    return _store().get(slug, {}) or {}


def adopt_legacy(slug: str) -> bool:
    """
    Give a newly configured tenant the pre-#783 flat settings, once.

    Returns True when something was adopted. ⚠️ Only the FIRST tenant gets them:
    copying one faction's channel id into five tenants would point every board
    at one channel, which looks like the bot ignoring its config.
    """
    store = _store()
    legacy = store.pop(LEGACY_SLUG, None)
    if not legacy:
        return False
    store.setdefault(slug, {}).update(legacy)
    st = state.load_state()
    st[STATE_KEY] = store
    state.save_state(st)
    log.info("adopted pre-#783 settings into tenant %s", slug)
    return True


def all_settings(slug: str) -> Dict[str, Any]:
    """Every setting with its effective value — stored where set, default otherwise."""
    stored = _for(slug)
    return {k: stored.get(k, s.default) for k, s in SETTINGS.items()}


def get(slug: str, key: str) -> Any:
    s = SETTINGS.get(key)
    if s is None:
        raise KeyError(key)
    return _for(slug).get(key, s.default)


def set_value(slug: str, key: str, raw: Any) -> Tuple[bool, str]:
    """Validate and persist. Returns (ok, message) — the message is what Discord shows."""
    s = SETTINGS.get(key)
    if s is None:
        return False, f"No setting called `{key}`. Try `/chain settings`."
    value, err = s.coerce(raw)
    if err:
        return False, err
    store = _store()
    store.setdefault(slug, {})[key] = value
    st = state.load_state()
    st[STATE_KEY] = store
    state.save_state(st)
    log.info("chain setting %s/%s = %r", slug, key, value)
    return True, f"`{key}` is now **{value}** for `{slug}`."


def reset(slug: str, key: str) -> Tuple[bool, str]:
    s = SETTINGS.get(key)
    if s is None:
        return False, f"No setting called `{key}`."
    store = _store()
    store.get(slug, {}).pop(key, None)
    st = state.load_state()
    st[STATE_KEY] = store
    state.save_state(st)
    return True, f"`{key}` is back to its default for `{slug}`, **{s.default}**."
