"""
Signing up for Chain Watch from the board (#1008, #1009; PRD #1006).

Two permanent buttons ride on every board message: **Sign up** and **Drop my
slot**. Each opens a private menu of hours; picking one asks the dashboard,
which decides.

⚠️ **Identity is the `[id]` in the clicker's server nickname, and nothing else.**
Torn's own Discord verification writes it (`Fluhffy [3099056] (TNL)`). The
ping matcher (`chain_identity.match`) also links people by name alone, which is
fine for deciding who to ping and not good enough to put somebody on a payout
sheet — so it is deliberately NOT consulted here. No `[id]`, no sign-up: the
member is told to re-verify. MonChoon, 2026-10-18: no API keys, two distinct
errors, never a guess.

⚠️ **The faction is the board's, never the user's.** It is read from which
board message the button sits on, so a member cannot act on another faction's
sheet by typing anything.

⚠️ **The dashboard decides; this only asks.** Roster membership, open hours and
races are all re-checked there. The menus below are a courtesy built from the
board's own data and may be a minute stale; whatever the dashboard answers is
what the member is told.

⚠️ **Never rename the custom ids.** They are stored in every board message
already posted; a renamed id routes every existing button to nothing, forever,
with no error (the lesson of `oc_watcher_manage_overrides_v1`).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import discord

import chain_api
import chain_posts
import chain_tenants
from chain_identity import ID_IN_NAME

log = logging.getLogger("chain_signup")

SIGN_UP_ID = "chain_board_sign_up_v1"
DROP_ID = "chain_board_drop_v1"

#: Discord's ceiling on a select menu.
MAX_OPTIONS = 25

HOUR_MS = 3_600_000

#: Called with the faction slug after a successful change, to redraw its board.
OnChange = Callable[[str], Awaitable[None]]


# ── Pure helpers ──────────────────────────────────────────────────────────────

def member_id_from(user) -> Optional[str]:
    """
    The Torn id in this Discord user's server name, or None.

    ⚠️ The SERVER nickname first — that is the one Torn's verification bot
    sets. The global display name and username are fallbacks for servers where
    the member set it themselves.
    """
    for name in (getattr(user, "nick", None), getattr(user, "display_name", None),
                 getattr(user, "global_name", None), getattr(user, "name", None)):
        if not name:
            continue
        m = ID_IN_NAME.search(str(name))
        if m:
            return m.group(1)
    return None


def faction_label(slug: str) -> str:
    return slug.upper()


def _tct_label(ms: int) -> str:
    """`Sat 26 Oct · 14:00 TCT` — TCT is UTC, which is what Torn shows."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%a %d %b · %H:%M TCT")


def _holds(hour: Dict, member_id: str) -> bool:
    return any(str(w.get("member_id")) == member_id for w in hour.get("watchers") or [])


def sign_up_options(payload: Dict, member_id: str, now_ms: int) -> List[Tuple[int, str, str]]:
    """
    `(hour_start, label, description)` for every hour this member could take.

    Open places, not finished, and not already theirs. The hour in progress is
    offered — you are watching it — matching the website.
    """
    out = []
    for h in payload.get("hours") or []:
        start = int(h["hour_start"])
        if start + HOUR_MS <= now_ms or int(h.get("open_slots") or 0) <= 0:
            continue
        if _holds(h, member_id):
            continue
        open_n = int(h["open_slots"])
        total = int(h.get("slots_per_hour") or open_n)
        desc = f"{open_n} of {total} place{'s' if total != 1 else ''} open"
        if h.get("bonus"):
            desc += " · ⭐ bonus hour"
        if start <= now_ms:
            desc += " · happening now"
        out.append((start, _tct_label(start), desc))
    return out[:MAX_OPTIONS]


def drop_options(payload: Dict, member_id: str, now_ms: int) -> List[Tuple[int, str, str]]:
    """This member's own hours that have not started — the website's rule."""
    out = []
    for h in payload.get("hours") or []:
        start = int(h["hour_start"])
        if start > now_ms and _holds(h, member_id):
            out.append((start, _tct_label(start), "Your place"))
    return out[:MAX_OPTIONS]


#: What each dashboard refusal says to the member. ⚠️ Keyed by the stable
#: `code`, never by the English `error` — that wording is the dashboard's to change.
MESSAGES = {
    "not_member": "Torn ID {member_id} isn't a member of {faction}, so you can't use this board.",
    "no_event": "There's no open Chain Watch event right now.",
    "outside_event": "That hour isn't part of the event any more.",
    "not_on_hour": "That hour isn't part of the event any more.",
    "hour_finished": "That hour has already finished.",
    "already_signed_up": "You're already signed up for {hour}.",
    "hour_full": "Someone just took the last place in {hour}. Pick another hour.",
    "not_signed_up": "You're not signed up for {hour}.",
    "hour_started": "{hour} has already started, so it can't be dropped now. Ask leadership.",
    chain_api.UNREACHABLE: (
        "I couldn't get an answer from the dashboard, so I can't confirm that "
        "worked. Check the board before trying again."),
}

NO_ID = (
    "I couldn't find your Torn ID in your server name. Re-verify with Torn's "
    "Discord bot so your name shows `Name [id]`, then try again.")


def result_message(kind: str, result: Dict, *, member_id: str, slug: str, hour_ms: int) -> str:
    hour = _tct_label(hour_ms)
    if result.get("ok"):
        if kind == "drop":
            return f"✅ You've dropped {hour}."
        return f"✅ You're signed up for {hour}."
    template = MESSAGES.get(result.get("code") or "", MESSAGES[chain_api.UNREACHABLE])
    return "⚠️ " + template.format(member_id=member_id, faction=faction_label(slug), hour=hour)


def slug_for_board(message_id: Optional[int]) -> Optional[str]:
    """Which faction's board this message is, or None."""
    if not message_id:
        return None
    for t in chain_tenants.all_tenants():
        if chain_posts.board_message(t.slug) == int(message_id):
            return t.slug
    return None


# ── Discord ──────────────────────────────────────────────────────────────────

class BoardButtons(discord.ui.View):
    """The two permanent buttons on every board. Registered once at startup."""

    def __init__(self, on_change: Optional[OnChange] = None):
        super().__init__(timeout=None)
        self.on_change = on_change

    @discord.ui.button(label="Sign up", style=discord.ButtonStyle.success,
                       custom_id=SIGN_UP_ID, emoji="✋")
    async def sign_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_menu(interaction, "sign_up", self.on_change)

    @discord.ui.button(label="Drop my slot", style=discord.ButtonStyle.secondary,
                       custom_id=DROP_ID)
    async def drop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_menu(interaction, "drop", self.on_change)


async def open_menu(interaction: discord.Interaction, kind: str,
                    on_change: Optional[OnChange]) -> None:
    """The private hour picker, or the reason there is not one."""
    async def say(text: str) -> None:
        await interaction.response.send_message(text, ephemeral=True)

    slug = slug_for_board(getattr(interaction.message, "id", None))
    if slug is None:
        return await say("This board isn't active any more.")
    member_id = member_id_from(interaction.user)
    if member_id is None:
        # ⚠️ Nothing is sent to the dashboard without an id we can stand behind.
        return await say("⚠️ " + NO_ID)

    tenant = chain_tenants.get(slug)
    payload = await chain_api.fetch(tenant) if tenant else None
    if payload is None:
        return await say("⚠️ " + MESSAGES[chain_api.UNREACHABLE])
    roster = {str(m.get("member_id")) for m in payload.get("members") or []}
    if member_id not in roster:
        return await say("⚠️ " + MESSAGES["not_member"].format(
            member_id=member_id, faction=faction_label(slug)))
    if not payload.get("event"):
        return await say("⚠️ " + MESSAGES["no_event"])

    now_ms = int(time.time() * 1000)
    options = (sign_up_options if kind == "sign_up" else drop_options)(payload, member_id, now_ms)
    if not options:
        return await say(
            "Every hour on the board is full or already yours." if kind == "sign_up"
            else "You have no upcoming hours to drop.")

    view = HourPicker(kind, slug, member_id, options, on_change)
    prompt = ("Which hour do you want to watch?" if kind == "sign_up"
              else "Which hour do you want to drop?")
    await interaction.response.send_message(prompt, view=view, ephemeral=True)


class HourPicker(discord.ui.View):
    """One private menu, for one member, for a few minutes."""

    def __init__(self, kind: str, slug: str, member_id: str,
                 options: List[Tuple[int, str, str]], on_change: Optional[OnChange]):
        super().__init__(timeout=300)
        self.kind = kind
        self.slug = slug
        self.member_id = member_id
        self.on_change = on_change
        select = discord.ui.Select(
            placeholder="Pick an hour (TCT)",
            options=[discord.SelectOption(label=label, description=desc, value=str(ms))
                     for ms, label, desc in options])
        select.callback = self._picked
        self.add_item(select)
        self._select = select

    async def _picked(self, interaction: discord.Interaction) -> None:
        hour_ms = int(self._select.values[0])
        verb = "Signing you up for" if self.kind == "sign_up" else "Dropping"
        # ⚠️ The menu goes away at once, so a second click cannot send a second
        # request while the first is in flight.
        await interaction.response.edit_message(
            content=f"⏳ {verb} {_tct_label(hour_ms)}…", view=None)

        tenant = chain_tenants.get(self.slug)
        path = "/signups" if self.kind == "sign_up" else "/signups/drop"
        result = (await chain_api.post(tenant, path, {"member_id": self.member_id, "hour_start": hour_ms})
                  if tenant else {"ok": False, "code": chain_api.UNREACHABLE})
        await interaction.edit_original_response(content=result_message(
            "drop" if self.kind == "drop" else "sign_up", result,
            member_id=self.member_id, slug=self.slug, hour_ms=hour_ms))

        if result.get("ok") and self.on_change is not None:
            try:
                await self.on_change(self.slug)
            except Exception:                                  # noqa: BLE001
                # The dashboard's own nudge and the poll loop are the backstop.
                log.exception("redraw after a board sign-up failed for %s", self.slug)
