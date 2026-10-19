"""
The OC action feed (#1020, PRD #1018): one message per suggestion, in the
faction's OC thread, with **Done** and **Not now**.

⚠️ **The dashboard owns every suggestion's state** (`oc_suggestions`). This
draws what it is told and reports back which version it drew; anything the bot
has not confirmed comes back next tick, so a crash between "edited the message"
and "told the dashboard" costs one redraw, never a lost or duplicated post.

⚠️ **A closed suggestion's message is DELETED** (MonChoon, 2026-10-19: "delete
the messages automatically once they've been cleared or expired"). Done,
resolved, expired and turned-off suggestions leave nothing in the thread; a
snoozed one stays, because it comes back.

⚠️ **Pings only on the FIRST post**, and only the faction's ping list
(MonChoon, to start). A redraw — an edit, a snooze, a close — never pings.

⚠️ **The buttons carry the suggestion in their custom id**
(`oc_sugg:<action>:<slug>:<id>`) and are a `DynamicItem`, so a button on a
message posted before a restart still routes. Never change the template: every
button already posted would stop working, silently.
"""

import logging
import re
from typing import Dict, List, Optional

import discord

import chain_api
import chain_identity
import chain_tenants
import choon_auth

log = logging.getLogger("oc_feed")

KIND_LABEL = {
    "swap": "⚖️ Better seating",
    "too_low": "⬆️ Joined too low",
    "idle": "💤 Not in an OC",
    "test": "🧪 Test",
}

#: Statuses whose message is removed rather than redrawn.
CLOSED = {"done", "resolved", "expired"}

STATUS_COLOR = {
    "open": 0xE67E22, "snoozed": 0x95A5A6, "done": 0x2ECC71,
    "resolved": 0x2ECC71, "expired": 0x7F8C8D,
}

#: How long "Not now" puts a suggestion away.
SNOOZE_MINUTES = 60


def _ts(ms: Optional[int], style: str = "t") -> str:
    return f"<t:{int(ms // 1000)}:{style}>" if ms else ""


def render(s: Dict) -> discord.Embed:
    """One suggestion as an embed, in whatever state it is in."""
    status = s.get("status")
    label = KIND_LABEL.get(s.get("kind"), s.get("kind") or "Suggestion")
    title = f"{label} — {s.get('title', '')}"
    closed = status in ("done", "resolved", "expired")
    if closed:
        title = f"~~{title}~~"[:256]
    lines: List[str] = []
    if s.get("body"):
        lines.append(s["body"])
    steps = s.get("steps") or []
    if steps and not closed:
        lines.append("\n".join(f"**{i}.** {step}" for i, step in enumerate(steps, 1)))
    value = s.get("value") or {}
    worth = []
    if value.get("success_pp"):
        worth.append(f"+{value['success_pp']:.1f}% success")
    if value.get("ev"):
        worth.append(f"≈ +${value['ev'] / 1e6:,.0f}M expected")
    if worth and not closed:
        lines.append("**Worth:** " + " · ".join(worth))
    if s.get("crime_id"):
        lines.append(f"[Crime #{s['crime_id']}](https://www.torn.com/factions.php?step=your#/tab=crimes&crimeId={s['crime_id']})")

    state = {
        "open": f"Open · expires {_ts(s.get('expires_at'), 'R')}" if s.get("expires_at") else "Open",
        "snoozed": f"💤 Snoozed by {s.get('acted_by') or 'someone'} until {_ts(s.get('snoozed_until'))}",
        "done": f"✅ Marked done by {s.get('acted_by') or 'someone'}",
        "resolved": "✅ Seen happening — resolved",
        "expired": f"⌛ {s.get('close_reason') or 'Expired'}",
    }.get(status, status or "")
    lines.append(state)

    e = discord.Embed(title=title[:256], description="\n\n".join(lines)[:4000],
                      color=STATUS_COLOR.get(status, 0x3498DB))
    e.set_footer(text=f"Suggestion #{s.get('id')} · {s.get('kind')}")
    return e


class SuggestionButton(discord.ui.DynamicItem[discord.ui.Button],
                       template=r"oc_sugg:(?P<action>done|snooze):(?P<slug>[a-z0-9_-]+):(?P<id>\d+)"):
    """Done / Not now on one suggestion. Registered once with `add_dynamic_items`."""

    def __init__(self, action: str, slug: str, suggestion_id: int):
        done = action == "done"
        super().__init__(discord.ui.Button(
            label="Done" if done else "Not now",
            style=discord.ButtonStyle.success if done else discord.ButtonStyle.secondary,
            emoji="✅" if done else "💤",
            custom_id=f"oc_sugg:{action}:{slug}:{suggestion_id}",
        ))
        self.action = action
        self.slug = slug
        self.suggestion_id = suggestion_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button,
                             match: re.Match[str], /):
        return cls(match["action"], match["slug"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        # ⚠️ The faction's managers only — the same gate as `/oc thread`.
        allowed, refusal = choon_auth.may_manage(interaction, self.slug)
        if not allowed:
            await interaction.response.send_message(refusal, ephemeral=True)
            return
        tenant = chain_tenants.get(self.slug)
        if tenant is None:
            await interaction.response.send_message("That faction is no longer configured.", ephemeral=True)
            return
        await interaction.response.defer()
        by = getattr(interaction.user, "display_name", None) or str(interaction.user)
        body = {"action": "done" if self.action == "done" else "snooze", "by": by}
        if self.action == "snooze":
            body["minutes"] = SNOOZE_MINUTES
        result = await chain_api.oc_post(tenant, f"/suggestions/{self.suggestion_id}/act", body)
        if not result.get("ok"):
            await interaction.followup.send(
                "That suggestion has already closed or changed — the message will catch up within a minute."
                if result.get("code") != chain_api.UNREACHABLE else
                "I couldn't reach the dashboard, so I can't confirm that. Try again in a minute.",
                ephemeral=True)
            return
        s = result["suggestion"]
        if s.get("status") in CLOSED:
            # Done: off the thread at once, and say so to the presser only.
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass
            await chain_api.oc_post(tenant, f"/suggestions/{s['id']}/rendered",
                                    {"version": s["version"], "deleted": True})
            await interaction.followup.send("✅ Marked done — removed from the thread.", ephemeral=True)
            return
        # Snoozed: redraw now rather than at the next tick.
        await interaction.message.edit(embed=render(s), view=view_for(self.slug, s), content=None)
        await chain_api.oc_post(tenant, f"/suggestions/{s['id']}/rendered", {"version": s["version"]})


def view_for(slug: str, s: Dict) -> Optional[discord.ui.View]:
    """Buttons while it can still be acted on; none once it is snoozed or closed."""
    if s.get("status") != "open":
        return None
    v = discord.ui.View(timeout=None)
    v.add_item(SuggestionButton("done", slug, s["id"]))
    v.add_item(SuggestionButton("snooze", slug, s["id"]))
    return v


def ping_content(member_ids: List[str]) -> Optional[str]:
    """Mentions for the ping list; a member with no Discord link is skipped."""
    mentions = []
    for mid in member_ids or []:
        did = chain_identity.discord_id_for(str(mid))
        if did:
            mentions.append(f"<@{did}>")
    return " ".join(mentions) or None


async def sync(tenant, payload: Dict, channel: discord.abc.Messageable) -> Dict[str, int]:
    """Draw every suggestion the dashboard says needs it. Returns counts for the log."""
    counts = {"posted": 0, "edited": 0, "skipped": 0, "deleted": 0}
    pings = ping_content(payload.get("ping_member_ids") or [])
    for s in payload.get("findings") or []:
        try:
            await _draw(tenant, s, channel, pings, counts)
        except discord.HTTPException as e:
            # The dashboard still has it as unrendered; next tick retries.
            log.warning("%s: suggestion %s not drawn: %s", tenant.slug, s.get("id"), e)
    return counts


async def _draw(tenant, s: Dict, channel, pings: Optional[str], counts: Dict[str, int]) -> None:
    slug = tenant.slug
    message = None
    if s.get("message_id"):
        try:
            message = await channel.fetch_message(int(s["message_id"]))
        except discord.NotFound:
            message = None
    if s.get("status") in CLOSED:
        # Closed: remove the message (if there still is one) and say so.
        if message is not None:
            try:
                await message.delete()
            except discord.NotFound:
                pass
        await chain_api.oc_post(tenant, f"/suggestions/{s['id']}/rendered",
                                {"version": s["version"], "deleted": True})
        counts["deleted"] = counts.get("deleted", 0) + 1
        return
    if message is None:
        if s.get("status") != "open":
            # Closed before it was ever posted (or its message was deleted):
            # nothing to show, just acknowledge.
            await chain_api.oc_post(tenant, f"/suggestions/{s['id']}/rendered", {"version": s["version"]})
            counts["skipped"] += 1
            return
        sent = await channel.send(
            content=pings, embed=render(s), view=view_for(slug, s),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        await chain_api.oc_post(tenant, f"/suggestions/{s['id']}/rendered",
                                {"version": s["version"], "message_id": str(sent.id), "channel_id": str(channel.id)})
        counts["posted"] += 1
        return
    await message.edit(embed=render(s), view=view_for(slug, s))
    await chain_api.oc_post(tenant, f"/suggestions/{s['id']}/rendered", {"version": s["version"]})
    counts["edited"] += 1
