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

import choon_registry
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
                 board_channel_id: int, ping_channel_id: int = 0,
                 manager_role_ids: Optional[List[int]] = None):
        self.slug = slug
        self.base_url = base_url.rstrip("/")
        self.guild_id = int(guild_id)
        self.board_channel_id = int(board_channel_id)
        self.ping_channel_id = int(ping_channel_id)
        #: Roles allowed to CHANGE this faction (#825). Empty means bot-admin
        #: only — see the fail-closed note in `choon_auth`.
        self.manager_role_ids = [int(r) for r in (manager_role_ids or [])]

    @property
    def pings_to(self) -> int:
        """Where pings land — the ping channel, or the board's when unset."""
        return self.ping_channel_id or self.board_channel_id

    @property
    def token_env(self) -> str:
        return TOKEN_ENV.format(self.slug.upper().replace("-", "_"))

    def token(self) -> Optional[str]:
        """
        This faction's Chain Watch bearer — from the fleet, else the environment.

        ⚠️ **The fleet first.** The control plane derives this from the fleet
        secret on demand, so a faction added from a phone works immediately and
        a rotated secret propagates on its own. Requiring somebody to SSH in,
        run the token CLI and paste the result is why factions used to sit in
        the list unable to poll.

        ⚠️ **The environment still works, and that is deliberate.** It is the
        fallback when the control plane cannot be reached, which is what stops
        the registry becoming a hard dependency of a board that is already
        running: an outage must not stop existing factions polling, only stop
        new ones being added.

        ⚠️ Read on every use, never cached here — `choon_registry` does the
        caching, with a TTL short enough that a rotation applies by itself.
        """
        from_fleet = choon_registry.token_for(self.slug, "chain_watch_token")
        if from_fleet:
            return from_fleet
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
            "manager_role_ids": self.manager_role_ids,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Tenant":
        return cls(
            slug=d["slug"], base_url=d["base_url"], guild_id=d.get("guild_id", 0),
            board_channel_id=d.get("board_channel_id", 0),
            ping_channel_id=d.get("ping_channel_id", 0),
            manager_role_ids=d.get("manager_role_ids") or [],
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


def token_note(slug: str) -> str:
    """
    What to say about a faction's polling credential, if anything.

    ⚠️ **Checks whether a token can actually be OBTAINED**, not whether an
    environment variable happens to be set. The old check asked the wrong
    question: it warned whenever `CHAIN_WATCH_TOKEN_<SLUG>` was absent, which is
    now the normal and correct state — the control plane derives the value on
    demand, so there is nothing to put in the environment.

    ⚠️ **Silence when it works; a real warning when it does not.** The warning
    is not removed, because the failure it describes still exists: if the fleet
    secret is missing on the admin container, nothing can mint a token and the
    faction will sit in the list unable to poll. Adding a faction that silently
    never works is the failure this line exists to prevent — it just has to be
    true to be worth reading.
    """
    env = TOKEN_ENV.format(slug.upper().replace("-", "_"))
    if choon_registry.token_for(slug, "chain_watch_token"):
        return ""
    if os.environ.get(env):
        # ⚠️ Worth saying: it works, but from the fallback. Nobody needs to act
        # now, and somebody removing the variable later should know why it was
        # load-bearing.
        return f"\nPolling uses `{env}` from the environment — the fleet did not supply one."
    return ("\n⚠️ No polling token could be obtained, so this faction will not poll. "
            "The control plane mints these; check that `FLEET_METRICS_SECRET` is set "
            "on the admin container and that the bot can reach the tenant registry.")


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
    existing = next((t for t in _store() if t.get("slug") == slug), None)
    items = [t for t in _store() if t.get("slug") != slug]
    replaced = existing is not None
    # ⚠️ Manager roles SURVIVE an update. `add` doubles as "edit", and silently
    # dropping a faction's roles would fail it closed to bot-admin-only the next
    # time somebody corrected its board channel — a permission change nobody
    # asked for, arriving as a side effect of an unrelated edit.
    keep_roles = (existing or {}).get("manager_role_ids") or []
    items.append(Tenant(slug, base_url, guild_id, board_channel_id, ping_channel_id,
                        keep_roles).to_dict())
    _save(items)
    verb = "updated" if replaced else "added"
    note = token_note(slug)
    return True, f"Tenant `{slug}` {verb}.{note}"


def remove(slug: str):
    items = _store()
    kept = [t for t in items if t.get("slug") != slug]
    if len(kept) == len(items):
        return False, f"No tenant called `{slug}`."
    _save(kept)
    return True, f"Tenant `{slug}` removed."


def set_manager_roles(slug: str, role_ids: List[int]):
    """Replace a faction's manager roles. Returns (ok, message)."""
    items = _store()
    for raw in items:
        if raw.get("slug") == slug:
            raw["manager_role_ids"] = sorted({int(r) for r in role_ids})
            _save(items)
            return True, raw["manager_role_ids"]
    return False, f"No tenant called `{slug}`."


def adopt_legacy_lead_role(slug: str, role_id: int) -> bool:
    """
    Carry the old global `CHAIN_LEAD_ROLE_ID` onto ONE tenant, once (#825).

    Returns True when it was written, False when there was nothing to do.

    ⚠️ **One tenant, and only when it has none of its own.** That variable is
    forge's council role which happened to be serving as a global one; applying
    it to every tenant — or re-applying it after somebody deliberately changed a
    faction's roles — would re-create exactly the cross-tenant authority #825
    removes.
    """
    if not role_id:
        return False
    tenant = get(slug)
    if tenant is None or tenant.manager_role_ids:
        return False
    ok, _ = set_manager_roles(slug, [int(role_id)])
    if ok:
        log.warning("adopted legacy lead role %s as %s's manager role; "
                    "CHAIN_LEAD_ROLE_ID is now superseded by per-tenant roles",
                    role_id, slug)
    return bool(ok)
