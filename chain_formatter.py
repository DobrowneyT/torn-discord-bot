"""
Chain Watch → Discord.

⚠️ **Two mechanisms, because there are two jobs**, and conflating them is how a
channel gets muted:

  • **The board** is a single edit-in-place embed showing the hours ahead. Empty
    slots are a STANDING STATE, not an event. Re-posting "3 slots unfilled"
    every five minutes for the same three slots trains everybody to ignore it —
    the same failure as a flag that fires for every row.

  • **The shift ping** is a one-shot mention to one person, because that IS an
    event: it happens once, it is about them, and it needs to interrupt.

Everything is TCT, which is UTC — it is what Torn shows and what leadership says
out loud. Discord's <t:…:t> renders in each reader's own zone automatically, so
both appear side by side with no timezone maths anywhere.
"""

from typing import Dict, List, Optional

import discord

COLOR_OK = 0x2ECC71        # green — the next six hours are covered
COLOR_THIN = 0xF1C40F      # yellow — somebody is missing
COLOR_NAKED = 0xE74C3C     # red — an hour with nobody at all

EMBED_TITLE = "Chain Watch"

# ⚠️ Six hours, the same horizon the dashboard page warns on. One definition, so
# the board and the page never disagree about what is unfilled.
GAP_HORIZON_HOURS = 6

LEGEND = (
    "🟢 covered  ·  🟡 one of two  ·  🔴 nobody  ·  ✈️ may not land in time  ·  ⭐ double tickets"
)


def _ts(ms: int, style: str = "t") -> str:
    """Discord renders this in each reader's own timezone. No maths here."""
    return f"<t:{int(ms // 1000)}:{style}>"


def _tct(ms: int) -> str:
    """TCT is UTC. Canonical, because it is what Torn shows."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M")


def _hour_line(hour: Dict, slots_per_hour: int) -> str:
    watchers = hour.get("watchers", [])
    open_slots = max(0, slots_per_hour - len(watchers))

    if open_slots == slots_per_hour:
        mark = "🔴"
    elif open_slots > 0:
        mark = "🟡"
    else:
        mark = "🟢"

    bonus = " ⭐" if hour.get("bonus") else ""
    names = []
    for w in watchers:
        travel = w.get("travel")
        # ⚠️ Flying is shown ON the row, with the band rather than a point —
        # Torn's flight times carry variance and a stated minute is a promise we
        # cannot keep.
        if travel:
            names.append(f"{w['name']} ✈️ {travel['destination']} "
                         f"(lands {_ts(travel['eta_earliest'])}–{_ts(travel['eta_latest'])})")
        else:
            names.append(w["name"])

    if open_slots:
        names.append("*nobody signed up*" if open_slots == slots_per_hour
                     else f"*{open_slots} open*")

    return (f"{mark} `{_tct(hour['hour_start'])}` {_ts(hour['hour_start'])}{bonus} — "
            + ", ".join(names))


def build_board(watch: Dict, *, now_ms: int) -> List[discord.Embed]:
    """The standing board: what is covered, what is not, and where the chain is going."""
    event = watch.get("event", {})
    slots_per_hour = int(event.get("slots_per_hour", 2))
    hours = [h for h in watch.get("hours", []) if h["hour_start"] + 3_600_000 > now_ms]

    horizon_ms = now_ms - (now_ms % 3_600_000) + GAP_HORIZON_HOURS * 3_600_000
    soon = [h for h in hours if h["hour_start"] < horizon_ms]
    # ⚠️ Counts SLOTS, not hours. An hour holding one of two watchers is one
    # unfilled slot — not zero, and not two.
    open_soon = sum(max(0, slots_per_hour - len(h.get("watchers", []))) for h in soon)
    naked_soon = [h for h in soon if not h.get("watchers")]

    if naked_soon:
        color = COLOR_NAKED
    elif open_soon:
        color = COLOR_THIN
    else:
        color = COLOR_OK

    chain = watch.get("chain") or {}
    lines = [LEGEND, ""]

    if chain:
        lines.append(f"**Chain** {chain.get('current', 0):,} / {chain.get('target', 100_000):,}")

    proj = watch.get("projection") or {}
    if proj.get("measured"):
        # ⚠️ A band, not a point. An eleven-day projection stated to the hour is
        # false precision somebody will plan around.
        lines.append(f"**100k around** {_ts(proj['earliest'], 'f')} – {_ts(proj['latest'], 'f')} "
                     f"· {proj['hits_per_hour']:.0f} hits/h")
    else:
        # ⚠️ Say it plainly. An absent projection is fine; a confident wrong one
        # is not.
        lines.append("**100k around** — not enough chain yet")

    lines.append("")
    if open_soon:
        lines.append(f"**{open_soon} slot{'' if open_soon == 1 else 's'} unfilled "
                     f"in the next {GAP_HORIZON_HOURS} hours**")
    else:
        lines.append(f"**Every slot in the next {GAP_HORIZON_HOURS} hours is covered.**")
    lines.append("")

    for h in hours[:24]:
        lines.append(_hour_line(h, slots_per_hour))

    embed = discord.Embed(
        title=f"{EMBED_TITLE} — {event.get('title', 'Chain')}",
        description="\n".join(lines)[:4000],
        color=color,
    )
    embed.set_footer(text="Times shown in TCT and your own timezone · sign up on the dashboard")
    return [embed]


def build_shift_ping(shift: Dict, *, lead_in_minutes: int = 5) -> str:
    """
    A one-shot mention, because a shift starting IS an event.

    ⚠️ For somebody in the air this has to go EARLIER than the usual lead-in —
    five minutes' notice is useless to a member over the Atlantic, and the whole
    point is to give them or a leader time to do something about it.
    """
    when = _ts(shift["hour_start"])
    mention = f"<@{shift.get('discord_id')}>" if shift.get("discord_id") else f"**{shift['name']}**"
    bonus = " ⭐ *double tickets this hour*" if shift.get("bonus") else ""

    partner = shift.get("partner")
    with_who = f" with **{partner['name']}**" if partner else " — **you are the only watcher this hour**"

    travel = shift.get("travel")
    if travel:
        return (f"{mention} your chain watch starts at {when} and you are "
                f"**in the air to {travel['destination']}** — landing "
                f"{_ts(travel['eta_earliest'])}–{_ts(travel['eta_latest'])}. "
                f"If that is too late, drop the slot on the dashboard so somebody can cover it.")

    chain = shift.get("chain") or {}
    chain_bit = (f" Chain is at {chain['current']:,}." if chain.get("current") else "")
    return (f"{mention} your chain watch starts at {when} "
            f"(in {lead_in_minutes} min){with_who}.{bonus}{chain_bit}")
