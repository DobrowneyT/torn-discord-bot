"""
The fleet's tenant list, read from the dashboard control plane.

⚠️ **One source of truth, and it is not here.** The control plane derives the
list from `pg_database`, so a faction exists there the moment it is provisioned.
Keeping a second list in the bot — which is what `/choon faction add slug:… 
base_url:…` amounted to — means somebody retypes what is already known, and the
two drift the first time a tenant is renamed.

⚠️ **This does not replace `chain_tenants`.** The registry says which factions
EXIST and where they live; `chain_tenants` says which ones this bot serves, and
holds the per-faction Discord state (channels, manager roles) that the dashboard
has no business knowing. Adding a faction is now "copy an entry from the
registry", not "type it twice".

⚠️ **A failure here must be quiet and local**, as in `chain_api`. The registry
being unreachable must degrade `/choon faction add` to a clear message, never
take the bot down or make the factions it already serves stop working.
"""

import logging
import os
import time
from typing import Dict, List, Optional

import requests

log = logging.getLogger("choon_registry")

#: Where the control plane's local surface answers. Loopback by default: the
#: bot runs on the same box, and the port is published to host loopback only.
URL_ENV = "CHOON_TENANT_REGISTRY_URL"
TOKEN_ENV = "CHOON_TENANT_REGISTRY_TOKEN"
DEFAULT_URL = "http://127.0.0.1:8091/api/internal/tenants"

#: ⚠️ Short. This is called from an autocomplete, which fires per keystroke, and
#: a picker that hangs is worse than one that is briefly empty.
TIMEOUT_SECONDS = 5


def configured() -> bool:
    return bool(os.environ.get(TOKEN_ENV))


#: How long a successful answer is reused.
#:
#: ⚠️ A cache is not an optimisation here, it is a requirement: `Tenant.token()`
#: is called on every poll tick for every faction, and an HTTP round trip per
#: tick would turn the control plane into a dependency of the board's cadence.
#:
#: ⚠️ Short enough that rotating `FLEET_METRICS_SECRET` takes effect on its own.
#: The whole gain of deriving tokens is lost if applying a rotation still means
#: restarting the bot by hand.
CACHE_SECONDS = 300

_cache: Dict[str, object] = {"at": 0.0, "tenants": None}


def _cached() -> Optional[List[Dict]]:
    if _cache["tenants"] is None:
        return None
    if time.time() - float(_cache["at"]) > CACHE_SECONDS:
        return None
    return _cache["tenants"]  # type: ignore[return-value]


def invalidate() -> None:
    """Drop the cache — used after a write that should be visible immediately."""
    _cache["at"] = 0.0
    _cache["tenants"] = None


def fetch(with_tokens: bool = False, use_cache: bool = True) -> Optional[List[Dict]]:
    """
    Every faction the fleet knows about. `None` when it could not be asked.

    With `with_tokens`, each entry also carries `chain_watch_token` and
    `war_overview_token`, derived by the control plane from the fleet secret.

    ⚠️ `None` and `[]` are different answers and callers must not conflate them:
    an empty fleet is a fact, an unreachable registry is an outage. Reporting
    "no factions exist" during an outage is how somebody re-provisions one that
    is already there.

    ⚠️ **Only successes are cached.** Caching a failure would extend one blip
    into five minutes of a bot that believes it has no factions.
    """
    if use_cache and with_tokens:
        hit = _cached()
        if hit is not None:
            return hit

    token = os.environ.get(TOKEN_ENV)
    if not token:
        log.warning("no %s set — the tenant registry is not configured", TOKEN_ENV)
        return None
    url = os.environ.get(URL_ENV) or DEFAULT_URL
    if with_tokens:
        url = f"{url}{'&' if '?' in url else '?'}tokens=1"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.warning("tenant registry unreachable at %s: %s", url, exc)
        return None
    if r.status_code == 503:
        # ⚠️ The control plane distinguishes this from 401 on purpose, so the
        # bot must too — otherwise somebody rotates a token that was fine.
        log.warning("tenant registry is not configured on the dashboard (503)")
        return None
    if r.status_code != 200:
        log.warning("tenant registry said %s", r.status_code)
        return None
    try:
        body = r.json()
    except ValueError:
        log.warning("tenant registry returned something that is not JSON")
        return None
    tenants = body.get("tenants")
    if not isinstance(tenants, list):
        log.warning("tenant registry returned no tenant list")
        return None
    if not body.get("base_url_available"):
        # ⚠️ Surfaced rather than swallowed: without a domain every base_url is
        # null, and a caller that did not notice would write a tenant with no
        # URL and see it fail later as an unreachable dashboard.
        log.warning("tenant registry has no base URLs: %s",
                    body.get("note") or "TENANT_BASE_DOMAIN is not set")
    if with_tokens and body.get("tokens_available") is False:
        # ⚠️ Named as a DASHBOARD fault. Without this the bot would report
        # "no token for forge", sending somebody to look at the bot's own
        # environment — which is the half that is fine.
        log.warning("the control plane cannot mint tokens: FLEET_METRICS_SECRET "
                    "is not set on the admin container")
    if with_tokens:
        _cache["at"] = time.time()
        _cache["tenants"] = tenants
    return tenants


def lookup(slug: str) -> Optional[Dict]:
    """One faction's registry entry, or None."""
    tenants = fetch(with_tokens=True)
    if tenants is None:
        return None
    wanted = (slug or "").strip().lower()
    return next((t for t in tenants if str(t.get("slug", "")).lower() == wanted), None)


def token_for(slug: str, field: str) -> Optional[str]:
    """
    One faction's `chain_watch_token` or `war_overview_token` from the fleet.

    ⚠️ Returns None on any failure, and the caller falls back to the
    environment. That fallback is what stops the control plane becoming a hard
    dependency of a board that is already running: a registry outage must not
    stop factions polling, it must only stop NEW ones being added.
    """
    entry = lookup(slug)
    if not entry:
        return None
    value = entry.get(field)
    return str(value) if value else None


def slugs() -> List[str]:
    """
    Every slug the fleet knows, or [] when it could not be asked.

    ⚠️ May be up to `CACHE_SECONDS` old — see the note on the cache. Empty means
    "could not ask and had nothing recent", never "the fleet is empty".
    """
    tenants = fetch(with_tokens=True)
    return [] if tenants is None else [str(t.get("slug")) for t in tenants if t.get("slug")]
