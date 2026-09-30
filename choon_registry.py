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


def fetch() -> Optional[List[Dict]]:
    """
    Every faction the fleet knows about. `None` when it could not be asked.

    ⚠️ `None` and `[]` are different answers and callers must not conflate them:
    an empty fleet is a fact, an unreachable registry is an outage. Reporting
    "no factions exist" during an outage is how somebody re-provisions one that
    is already there.
    """
    token = os.environ.get(TOKEN_ENV)
    if not token:
        log.warning("no %s set — the tenant registry is not configured", TOKEN_ENV)
        return None
    url = os.environ.get(URL_ENV) or DEFAULT_URL
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
    return tenants


def lookup(slug: str) -> Optional[Dict]:
    """One faction's registry entry, or None."""
    tenants = fetch()
    if tenants is None:
        return None
    wanted = (slug or "").strip().lower()
    return next((t for t in tenants if str(t.get("slug", "")).lower() == wanted), None)


def slugs() -> List[str]:
    """Every slug the fleet knows, or [] when it could not be asked."""
    tenants = fetch()
    return [] if tenants is None else [str(t.get("slug")) for t in tenants if t.get("slug")]
