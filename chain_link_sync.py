"""
Running the identity auto-match against live Discord and a live roster (#784).

Kept apart from `chain_identity`, which stays pure and testable without
discord.py or a network. This module is the wiring: where the roster comes from,
which guild to match against, and when it happens.

⚠️ **On startup and on member-join, not only when somebody asks.** If the map
only filled when a leader ran a command, the first chain would begin with an
empty map and pings that reach nobody — and the person who notices is the
watcher who was never told their shift started.
"""

import logging
from typing import Dict, List, Optional

import chain_api
import chain_identity
import chain_tenants

log = logging.getLogger("chain_link_sync")


def _guild_members(client, tenant) -> List[chain_identity.GuildMember]:
    """
    The members of the tenant's OWN guild.

    ⚠️ Never "whatever guild we happen to be looking at". Matching Forge's
    roster against TNL's members links the wrong people to the wrong shifts, and
    nothing about the result would look wrong until somebody is pinged for a
    faction they are not in.
    """
    guild = None
    for g in getattr(client, "guilds", []):
        if g.id == tenant.guild_id:
            guild = g
            break
    if guild is None:
        log.warning("not in guild %s for tenant %s — cannot match identities",
                    tenant.guild_id, tenant.slug)
        return []
    return [
        chain_identity.GuildMember(m.id, m.display_name, m.name)
        for m in guild.members
    ]


async def sync_tenant(client, tenant) -> Optional[Dict]:
    """Match one faction. Returns the summary, or None when unreachable."""
    payload = await chain_api.fetch(tenant)
    if payload is None:
        return None
    return chain_identity.sync(payload.get("members", []), _guild_members(client, tenant))


async def sync_all(client) -> Dict[str, Optional[Dict]]:
    """
    Match every configured faction.

    ⚠️ One faction failing must not stop the others — the same isolation rule
    the poller follows. A dashboard being down is that faction's problem.
    """
    out: Dict[str, Optional[Dict]] = {}
    for tenant in chain_tenants.all_tenants():
        try:
            out[tenant.slug] = await sync_tenant(client, tenant)
        except Exception:                                  # noqa: BLE001
            # ⚠️ Broad on purpose. An unexpected shape from one dashboard, or a
            # discord.py edge on one guild, must not abort the loop over the
            # other four.
            log.exception("identity sync failed for %s", tenant.slug)
            out[tenant.slug] = None
    return out


def attach(client) -> None:
    """
    Re-match when somebody joins a guild we watch.

    ⚠️ A new arrival is exactly when the map is wrong and nobody knows it: they
    signed up on the dashboard, the bot has never seen them, and their first
    shift ping would go to a bold name instead of a mention.
    """

    async def on_member_join(member):
        tenant = next(
            (t for t in chain_tenants.all_tenants() if t.guild_id == member.guild.id),
            None)
        if tenant is None:
            return
        try:
            await sync_tenant(client, tenant)
        except Exception:                                  # noqa: BLE001
            log.exception("identity sync on join failed for %s", tenant.slug)

    client.add_listener(on_member_join, "on_member_join")
