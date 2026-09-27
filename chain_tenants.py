"""
The factions this bot watches chains for (#783).

The bot began single-faction: one Torn key, one channel. Chain Watch serves
five, each pinging only its own members in its own channels.

⚠️ **Chain Watch needs no Torn key per faction.** It polls each tenant's
dashboard, which already holds that faction's key pool — so adding a faction
here costs a URL and a token, not a new set of API credentials and a new share
of somebody's rate limit.

⚠️ **`guild_id` is per tenant on purpose.** It makes the design indifferent to
whether the five factions are channels in one Discord server or five separate
servers — a question that would otherwise have to be asked now and could change
later without warning. It is also what scopes the identity auto-match (#784):
matching a faction roster against the wrong guild links the wrong people.

⚠️ **The token never comes from a slash command.** Discord command arguments are
visible client-side and land in logs; a tenant bearer pasted into a channel is a
leaked credential for that faction. It is supplied out of band through the
environment, as `CHAIN_WATCH_TOKEN_<SLUG>`.

⚠️ **The OC watcher is untouched.** It has its own key and its own loop. Folding
it into this config would couple two features that have no reason to move
together, and the OC watcher is the part that is already working.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

import state

log = logging.getLogger("chain_tenants")

STATE_KEY = "chain_tenants"

#: Slugs match the dashboard's own constraint — lowercase, digits, hyphen.
SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")

#: Where a tenant's bearer is read from. `forge` → CHAIN_WATCH_TOKEN_FORGE.
TOKEN_ENV = "CHAIN_WATCH_TOKEN_{}"


class Tenant:
    """One faction, and everything needed to talk to it and about it."""

    def __init__(self, slug: str, base_url: str, guild_id: int,
                 board_channel_id: int, ping_channel_id: int = 0):
        self.slug = slug
        self.base_url = base_url.rstrip("/")
        self.guild_id = int(guild_id)
        self.board_channel_id = int(board_channel_id)
        self.ping_channel_id = int(ping_channel_id)

    @property
    def pings_to(self) -> int:
        """Where pings land — the ping channel, or the board's when unset."""
        return self.ping_channel_id or self.board_channel_id

    @property
    def token_env(self) -> str:
        return TOKEN_ENV.format(self.slug.upper().replace("-", "_"))

    def token(self) -> Optional[str]:
        """
        ⚠️ Read on every use, never cached at import.

        A token rotated in the environment should take effect on the next
        restart without anybody having to remember that this module snapshotted
        it — and a missing one must read as missing at the moment it is needed,
        not as an empty string that 401s mysteriously.
        """
        return os.environ.get(self.token_env) or None

    @property
    def url(self) -> str:
        return f"{self.base_url}/api/internal/chain-watch"

    @property
    def sign_up_url(self) -> str:
        """The page a member actually claims a slot on.

        ⚠️ Per tenant, from `base_url`. Every faction has its own host, so a
        single hardcoded link would send four factions to a fifth's sheet —
        where they would see somebody else's roster and not their own slots."""
        return f"{self.base_url}/members/chain-watch"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "base_url": self.base_url,
            "guild_id": self.guild_id,
            "board_channel_id": self.board_channel_id,
            "ping_channel_id": self.ping_channel_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Tenant":
        return cls(
            slug=d["slug"], base_url=d["base_url"], guild_id=d.get("guild_id", 0),
            board_channel_id=d.get("board_channel_id", 0),
            ping_channel_id=d.get("ping_channel_id", 0),
        )


def _store() -> List[Dict[str, Any]]:
    return state.load_state().get(STATE_KEY, []) or []


def _save(items: List[Dict[str, Any]]) -> None:
    st = state.load_state()
    st[STATE_KEY] = items
    state.save_state(st)


def all_tenants() -> List[Tenant]:
    out = []
    for raw in _store():
        try:
            out.append(Tenant.from_dict(raw))
        except (KeyError, TypeError, ValueError):
            # ⚠️ One malformed row must not take the other four offline. The
            # whole point of per-tenant isolation is that a faction's problem
            # stays that faction's problem.
            log.warning("dropping malformed tenant entry: %r", raw)
    return out


def get(slug: str) -> Optional[Tenant]:
    return next((t for t in all_tenants() if t.slug == slug), None)


def add(slug: str, base_url: str, guild_id: int,
        board_channel_id: int, ping_channel_id: int = 0):
    """Returns (ok, message) — the message is what Discord shows."""
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        return False, f"`{slug}` is not a valid slug — lowercase letters, digits and hyphens."
    if not base_url.startswith("https://"):
        # ⚠️ https only. The bearer is sent on every poll; over http it is
        # readable by anything between here and the box, five times a minute,
        # forever.
        return False, "The dashboard URL must start with `https://`."
    items = [t for t in _store() if t.get("slug") != slug]
    replaced = len(items) != len(_store())
    items.append(Tenant(slug, base_url, guild_id, board_channel_id, ping_channel_id).to_dict())
    _save(items)
    verb = "updated" if replaced else "added"
    env = TOKEN_ENV.format(slug.upper().replace("-", "_"))
    note = "" if os.environ.get(env) else (
        f"\n⚠️ `{env}` is not set, so polling will fail. "
        "Set it in the bot's environment and restart — never through a command.")
    return True, f"Tenant `{slug}` {verb}.{note}"


def remove(slug: str):
    items = _store()
    kept = [t for t in items if t.get("slug") != slug]
    if len(kept) == len(items):
        return False, f"No tenant called `{slug}`."
    _save(kept)
    return True, f"Tenant `{slug}` removed."
