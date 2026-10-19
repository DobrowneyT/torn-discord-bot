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


def fetch_sync(tenant: "chain_tenants.Tenant", url: Optional[str] = None) -> Optional[Dict]:
    """Blocking fetch of `url` (default: the Chain Watch feed). The payload, or None with the reason logged."""
    token = tenant.token()
    if not token:
        # ⚠️ Named explicitly. A missing token is a provisioning mistake, not an
        # outage, and the two want different responses from whoever is looking.
        # ⚠️ Points at the control plane first. These are minted from the fleet
        # secret now; the environment variable is only the fallback, so sending
        # somebody there is sending them to the half that is usually fine.
        log.warning("no token for %s — the control plane mints these; check "
                    "FLEET_METRICS_SECRET on the admin container, or set %s "
                    "as a fallback", tenant.slug, tenant.token_env)
        return None
    try:
        res = requests.get(
            url or tenant.url,
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


async def fetch_oc(tenant: "chain_tenants.Tenant") -> Optional[Dict]:
    """The OC board's feed (#1019): crimes, roster and each seat's minimum."""
    return await asyncio.to_thread(fetch_sync, tenant, tenant.oc_url)


async def fetch_slug(slug: str) -> Optional[Dict]:
    tenant = chain_tenants.get(slug)
    return await fetch(tenant) if tenant else None


#: What a sign-up call returns when the dashboard could not be asked at all —
#: as distinct from it ANSWERING with a refusal. See `post_sync`.
UNREACHABLE = "unreachable"


def post_sync(tenant: "chain_tenants.Tenant", path: str, body: Dict,
              base: Optional[str] = None) -> Dict:
    """
    Blocking POST to `/api/internal/chain-watch<path>` (#1008).

    Returns the dashboard's JSON — `{"ok": true, ...}` or `{"ok": false,
    "code": ..., "error": ...}` — or `{"ok": False, "code": UNREACHABLE}`.

    ⚠️ **UNREACHABLE is not "nothing happened".** A timeout can fire after the
    dashboard has written the sign-up. The caller must say "could not confirm",
    never "failed", or a member signs up twice in two different hours believing
    the first one did not take.
    """
    token = tenant.token()
    if not token:
        log.warning("no token for %s — cannot sign anybody up", tenant.slug)
        return {"ok": False, "code": UNREACHABLE}
    try:
        res = requests.post(
            (base or tenant.url) + path,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        log.warning("%s unreachable for %s: %s", tenant.slug, path, e)
        return {"ok": False, "code": UNREACHABLE}
    try:
        data = res.json()
    except ValueError:
        log.warning("%s answered %s to %s without JSON", tenant.slug, res.status_code, path)
        return {"ok": False, "code": UNREACHABLE}
    if res.status_code == 401:
        log.error("%s rejected our token on %s", tenant.slug, path)
        return {"ok": False, "code": UNREACHABLE}
    # ⚠️ A body without `ok` is not one of ours — typically Fastify's 404 for a
    # tenant whose dashboard predates this route.
    if not isinstance(data, dict) or "ok" not in data:
        log.warning("%s answered %s to %s with an unexpected body", tenant.slug, res.status_code, path)
        return {"ok": False, "code": UNREACHABLE}
    return data


async def post(tenant: "chain_tenants.Tenant", path: str, body: Dict) -> Dict:
    """Async wrapper — `requests` must not block the gateway (see `fetch`)."""
    return await asyncio.to_thread(post_sync, tenant, path, body)


def get_json_sync(tenant: "chain_tenants.Tenant", url: str) -> Optional[Dict]:
    """A plain authorised GET of any internal route; None on any failure."""
    return fetch_sync(tenant, url)


async def oc_post(tenant: "chain_tenants.Tenant", path: str, body: Dict) -> Dict:
    """POST to `/api/internal/oc-watch<path>` (#1020) — same rules as `post`."""
    return await asyncio.to_thread(post_sync, tenant, path, body, tenant.oc_url)


async def oc_get(tenant: "chain_tenants.Tenant", path: str) -> Optional[Dict]:
    return await asyncio.to_thread(get_json_sync, tenant, tenant.oc_url + path)
