"""
Talking to a tenant's dashboard for the War Overview (#811).

One client for one endpoint — `GET /api/internal/war-overview` — used by
`/rw overview`.

⚠️ **Its own token, deliberately.** `WAR_OVERVIEW_TOKEN_<SLUG>`, derived from a
different HMAC scope than Chain Watch's. The same bot holds both, and scoping
them apart is what stops a leak of the board credential also surrendering a
war's full attack and revive history (#609).

⚠️ **The address book is shared; nothing else is.** `chain_tenants` already
knows which dashboard a slug lives at, and that is bot-level infrastructure —
making an operator register every faction twice would guarantee the two lists
drift and one feature silently points at the wrong host. No Chain Watch
behaviour, state or settings are touched.

⚠️ **A failure here must be quiet and local**, as in `chain_api`: five factions
share this bot, and one dashboard being down must not raise into a command
handler and look like the bot is broken.
"""

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

import chain_tenants
import choon_registry

log = logging.getLogger("war_overview_api")

#: Where a tenant's War Overview bearer is read from. `forge` → WAR_OVERVIEW_TOKEN_FORGE.
TOKEN_ENV = "WAR_OVERVIEW_TOKEN_{}"

#: ⚠️ Longer than Chain Watch's 10s: this pulls a whole war's attacks, which is
#: tens of thousands of rows, where the board is one small projection. Still
#: bounded — a command that never answers is worse than one that says it could
#: not.
TIMEOUT_SECONDS = 30


def token_env(slug: str) -> str:
    return TOKEN_ENV.format(slug.upper().replace("-", "_"))


def token_for(slug: str) -> Optional[str]:
    """
    This faction's War Overview bearer — from the fleet, else the environment.

    ⚠️ **The fleet first**, for the same reason as Chain Watch: the control
    plane derives it from the fleet secret on demand, so a faction added from a
    phone works immediately and a rotated secret propagates on its own.

    ⚠️ The environment remains the fallback, so a control-plane outage cannot
    stop a faction that is already working.
    """
    from_fleet = choon_registry.token_for(slug, "war_overview_token")
    if from_fleet:
        return from_fleet
    return os.environ.get(token_env(slug)) or None


def base_url_for(slug: str) -> Optional[str]:
    tenant = chain_tenants.get(slug)
    return tenant.base_url if tenant else None


def fetch(slug: str, **params: Any) -> Optional[Dict]:
    """
    Fetch the overview. Returns the payload, or None with the reason logged.

    `params` are passed through as the query string — `war`, `mode`, `member`,
    `warring_only`.
    """
    base = base_url_for(slug)
    if not base:
        log.warning("no tenant called %s — add it with /choon faction add", slug)
        return None
    tok = token_for(slug)
    if not tok:
        # ⚠️ Named explicitly. A missing token is a provisioning mistake, not an
        # outage, and the two want different responses from whoever is looking.
        log.warning("no War Overview token for %s — the control plane mints "
                    "these; check FLEET_METRICS_SECRET on the admin container, "
                    "or set %s as a fallback", slug, token_env(slug))
        return None

    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    url = f"{base}/api/internal/war-overview"
    if clean:
        url = f"{url}?{urlencode(clean)}"
    try:
        res = requests.get(url, headers={"Authorization": f"Bearer {tok}"},
                           timeout=TIMEOUT_SECONDS)
    except requests.RequestException as e:
        log.warning("%s unreachable: %s", slug, e)
        return None
    if res.status_code == 401:
        log.error("%s rejected our token — is %s current? (rotating the fleet "
                  "secret changes every tenant's token at once)", slug, token_env(slug))
        return None
    if res.status_code == 404:
        # ⚠️ The route is UNREGISTERED when the tenant has no token set, so a
        # 404 here means the DASHBOARD side is unprovisioned — a different fix
        # from our own missing token, and worth saying so.
        log.error("%s has no war-overview route — set WAR_OVERVIEW_TOKEN on "
                  "api-%s and restart it (node db/war-overview-token.mjs --write %s)",
                  slug, slug, slug)
        return None
    if res.status_code != 200:
        log.warning("%s answered %s", slug, res.status_code)
        return None
    try:
        return res.json()
    except ValueError:
        log.warning("%s returned something that is not JSON (a proxy error page?)", slug)
        return None
