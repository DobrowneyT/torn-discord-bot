"""
Talking to a tenant's member-report mailbox (#943, dashboard side #942).

Four calls against one dashboard:

    POST /api/internal/member-threads/claim    → what is waiting, + the registry
    GET  /api/internal/member-threads/report   → one (war, member)'s facts
    POST /api/internal/member-threads/posted   → record the message id
    POST /api/internal/member-threads/failed   → release, with the reason

⚠️ **This is the only dashboard credential the bot holds that WRITES.** `claim`
takes rows for ten minutes and `posted` marks them done against a permanent
per-member record. Two rules follow:

  * **Never claim speculatively.** Not from an autocomplete, not from a health
    check, not to see whether anything is waiting. A claim not followed by
    `posted` or `failed` hides those rows from the next poll for ten minutes.
  * **`failed` is not optional.** Releasing on error is what stops one bad
    thread stranding a member's report for the rest of the war.

⚠️ **A failure here must be quiet and local**, as in `chain_api`. Five factions
share this bot; one dashboard being down must not raise into the poll loop and
take the other four with it. Every call returns None/False on failure and logs
the reason.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests

import chain_tenants
import choon_registry

log = logging.getLogger("member_threads_api")

#: Where a tenant's member-threads bearer is read from, when the control plane
#: cannot be reached. `forge` → MEMBER_THREADS_TOKEN_FORGE.
TOKEN_ENV = "MEMBER_THREADS_TOKEN_{}"

#: ⚠️ Short, and shorter than the poll interval. A dashboard that has stopped
#: answering should surface within one cycle rather than piling up requests.
TIMEOUT_SECONDS = 10


def token_env(slug: str) -> str:
    return TOKEN_ENV.format(slug.upper().replace("-", "_"))


def token_for(slug: str) -> Optional[str]:
    """
    This faction's member-threads bearer — from the fleet, else the environment.

    ⚠️ **The fleet first**, exactly as `war_overview_api` does: the control
    plane derives it from the fleet secret on demand, so a faction added from a
    phone works immediately and a rotated secret propagates on its own.

    ⚠️ The environment remains the fallback, so a control-plane outage cannot
    stop a faction whose reports were already working.
    """
    from_fleet = choon_registry.token_for(slug, "member_threads_token")
    if from_fleet:
        return from_fleet
    return os.environ.get(token_env(slug)) or None


def configured_slugs() -> List[str]:
    """
    Factions this bot could post member reports for.

    ⚠️ Presence of a token is NOT the gate — the control plane mints one for
    every tenant, so that would enable the feature everywhere the moment the
    registry answers. The gate is `member_thread_forum_id`, which somebody has
    to set per faction, and the watcher checks it.
    """
    return [t.slug for t in chain_tenants.all_tenants()]


def _base(slug: str) -> Optional[str]:
    tenant = chain_tenants.get(slug)
    return tenant.base_url if tenant else None


def _call(slug: str, method: str, path: str, **kw: Any) -> Optional[Dict]:
    base = _base(slug)
    if not base:
        log.warning("no base_url for %s — is it in the tenant registry?", slug)
        return None
    tok = token_for(slug)
    if not tok:
        # ⚠️ Named explicitly, and pointing at the control plane first. A
        # missing token is a provisioning mistake rather than an outage, and
        # these are minted from the fleet secret now — the environment variable
        # is only the fallback.
        log.warning("no member-threads token for %s — the control plane mints "
                    "these; check CHOON_TENANT_REGISTRY_TOKEN, or set %s",
                    slug, token_env(slug))
        return None
    try:
        res = requests.request(
            method, f"{base}{path}",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=TIMEOUT_SECONDS, **kw,
        )
    except requests.RequestException as exc:
        log.warning("member-threads %s %s for %s failed: %s", method, path, slug, exc)
        return None
    if res.status_code == 404:
        # ⚠️ 404 is "not provisioned", not "not found". An absent
        # MEMBER_THREADS_TOKEN leaves the routes UNREGISTERED on the dashboard,
        # which is deliberate — so this is the normal state of a tenant nobody
        # has switched on, and it must not read as an error.
        log.debug("member-threads not enabled on %s (route unregistered)", slug)
        return None
    if res.status_code != 200:
        log.warning("member-threads %s %s for %s → HTTP %s: %s",
                    method, path, slug, res.status_code, res.text[:200])
        return None
    try:
        return res.json()
    except ValueError:
        log.warning("member-threads %s %s for %s returned non-JSON", method, path, slug)
        return None


def claim(slug: str) -> Optional[Dict]:
    """
    Take what is waiting. ⚠️ Mutating — see the module note. Call this once per
    poll tick and nowhere else.

    Returns `{"threads": {member_id: {...}}, "pending": [...]}`, or None.
    """
    return _call(slug, "POST", "/api/internal/member-threads/claim", json={})


def report(slug: str, war_id: Any, member_id: Any) -> Optional[Dict]:
    """
    One member's facts for one finished war. Read-only; safe to retry.

    ⚠️ The dashboard answers 400 for a war that is live or unknown rather than
    serving a partial report. That is a refusal to act on, not a transport
    error: posting a scoreless report into a permanent record cannot be undone.
    """
    return _call(slug, "GET", "/api/internal/member-threads/report",
                 params={"war": str(war_id), "member": str(member_id)})


def mark_posted(slug: str, entry_id: int, *, message_id: int,
                thread_id: int, channel_id: int) -> bool:
    """
    Record the message the moment Discord confirms it.

    ⚠️ **Called before anything else after a successful post**, including before
    any logging or further work. A post Discord accepted but we never recorded
    is the one failure that duplicates a permanent record: the next drain sees
    the entry unposted and posts a second embed into the same thread.
    """
    out = _call(slug, "POST", "/api/internal/member-threads/posted", json={
        "id": entry_id, "message_id": str(message_id),
        "thread_id": str(thread_id), "channel_id": str(channel_id),
    })
    return bool(out)


def mark_failed(slug: str, entry_id: int, reason: str) -> bool:
    """
    Release the entry and record why.

    ⚠️ Always called on failure. Without it the row stays claimed until the
    ten-minute timeout, and a permanently failing cause (a deleted thread)
    becomes a silent ten-minute retry loop with nothing saying so.
    """
    out = _call(slug, "POST", "/api/internal/member-threads/failed", json={
        "id": entry_id, "error": reason[:500],
    })
    return bool(out)
