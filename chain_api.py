"""
Talking to a tenant's dashboard (#784, #785).

One client for one endpoint — `GET /api/internal/chain-watch` — shared by the
identity sync and the board poller, so there is a single place that knows how a
faction is reached and a single place that handles it being unreachable.

⚠️ **A failure here must be quiet and local.** Five factions share this bot: one
dashboard being down, slow, or holding a stale token must not raise into the
poll loop and take the other four offline with it. Every call returns `None` on
failure and logs it; the caller decides what to show.
"""

import asyncio
import logging
from typing import Dict, Optional

import requests

import chain_tenants

log = logging.getLogger("chain_api")

#: ⚠️ Short, and shorter than any sensible poll interval. A dashboard that has
#: stopped answering should surface as a stale board within one cycle, not pile
#: up requests until the loop is permanently behind — the shape of the
#: fleet-metrics incident on the dashboard side.
TIMEOUT_SECONDS = 10


def fetch_sync(tenant: "chain_tenants.Tenant") -> Optional[Dict]:
    """Blocking fetch. Returns the payload, or None with the reason logged."""
    token = tenant.token()
    if not token:
        # ⚠️ Named explicitly. A missing token is a provisioning mistake, not an
        # outage, and the two want different responses from whoever is looking.
        log.warning("no token for %s — set %s in the environment",
                    tenant.slug, tenant.token_env)
        return None
    try:
        res = requests.get(
            tenant.url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        log.warning("%s unreachable: %s", tenant.slug, e)
        return None
    if res.status_code == 401:
        log.error("%s rejected our token — is %s current? (rotating the fleet "
                  "secret changes every tenant's token at once)",
                  tenant.slug, tenant.token_env)
        return None
    if res.status_code != 200:
        log.warning("%s answered %s", tenant.slug, res.status_code)
        return None
    try:
        return res.json()
    except ValueError:
        # Almost always an HTML error page from a proxy in front of the app.
        log.warning("%s did not answer with JSON", tenant.slug)
        return None


async def fetch(tenant: "chain_tenants.Tenant") -> Optional[Dict]:
    """
    Async wrapper.

    ⚠️ `requests` is blocking, and blocking inside a discord.py coroutine stalls
    the whole bot — heartbeats included, which Discord eventually treats as a
    disconnect. A thread keeps one slow dashboard from taking the gateway down.
    """
    return await asyncio.to_thread(fetch_sync, tenant)


async def fetch_slug(slug: str) -> Optional[Dict]:
    tenant = chain_tenants.get(slug)
    return await fetch(tenant) if tenant else None
