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

# ⚠️ A FALLBACK, not the definition. The horizon arrives in the payload
# (`gap_horizon_hours`, dashboard #782) precisely so the board and the page
# cannot disagree about what is unfilled; this value only covers a payload that
# predates that field, and a board drawn from it should be treated as suspect.
GAP_HORIZON_FALLBACK = 6

LEGEND = (
    "🟢 covered  ·  🟡 one of two  ·  🔴 nobody  ·  ✈️ may not land in time  ·  ⭐ double tickets"
)


def _ts(ms: int, style: str = "t") -> str:
    """Discord renders this in each reader's own timezone. No maths here."""
    return f"<t:{int(ms // 1000)}:{style}>"


def _who(watcher: Dict, *, compact: bool = False) -> str:
    """
    How a watcher is named.

    ⚠️ `Name [ID]` when we have no Discord link (#784), never a bare name.
    Leadership has to be able to see exactly who to chase, and the id is what
    tells two members with similar display names apart — the same reason every
    sign-up keys on the Torn id rather than the name the sheet used.

    ⚠️ **`compact` exists because an embed cannot be made wider.** Its width is
    fixed by the Discord client; there is no API for it. A mention renders as
    the member's SERVER NICKNAME, and a convention like
    "MonChoon_616 [2250591] (TNLF)" is 30 characters — two of those plus the
    time prefix is ~84, against the ~55-60 a row fits. So two watchers can
    never share a row while mentions are used, however the text is arranged.
    Compact drops to the Torn name and fits in ~46.

    ⚠️ Nothing is lost by it on the BOARD: a mention inside an embed is a blue
    link that notifies nobody. The notification happens in the shift ping,
    which `mention_members` governs separately and which compact never touches.
    """
    name = watcher.get("name") or "unknown"
    member_id = watcher.get("member_id")
    discord_id = watcher.get("discord_id")
    if compact:
        # ⚠️ The id survives for UNLINKED members even here. It is the one
        # thing a leader needs in order to fix the link, and those are the
        # minority of rows, so it costs little width.
        return name if discord_id else (f"{name} [{member_id}]" if member_id else name)
    if discord_id:
        return f"<@{discord_id}>"
    return f"{name} [{member_id}]" if member_id else name


def _tct(ms: int) -> str:
    """TCT is UTC. Canonical, because it is what Torn shows."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M")


def _travel_phrase(travel: Dict) -> str:
    """
    What we can honestly say about somebody in the air.

    ⚠️ The dashboard serves the departure and the plane, never an arrival —
    Torn publishes no arrival time for a traveller, so an ETA has to be counted
    from a take-off that was witnessed. `arrival` is filled in by the poller
    from flight.py (#785) when it can be; when it cannot, we say so. A
    fabricated landing time is worse than an absent one, because a lead waits
    on it.
    """
    where = travel.get("destination") or "somewhere"
    if travel.get("state") == "Abroad":
        return f"🛬 in {where}"
    arrival = travel.get("arrival")
    if arrival and arrival.get("earliest") and arrival.get("latest"):
        return (f"✈️ {where} "
                f"(lands {_ts(arrival['earliest'])}–{_ts(arrival['latest'])})")
    return f"✈️ {where} (arrival unknown)"


def _hour_line(hour: Dict, slots_per_hour: int, *, compact: bool = False) -> str:
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
        # ⚠️ Flying is shown ON the row, as a band rather than a point — Torn's
        # flight times carry variance and a stated minute is a promise we
        # cannot keep.
        who = _who(w, compact=compact)
        names.append(f"{who} {_travel_phrase(travel)}" if travel else who)

    if open_slots:
        names.append("*nobody signed up*" if open_slots == slots_per_hour
                     else f"*{open_slots} open*")

    # ⚠️ The unit is spelled out on EVERY row, not just in the footer. The board
    # scrolls for a twelve-day chain and gets screenshotted a line at a time, so
    # a footer is out of sight for all but the first few hours — and a bold time
    # with no unit reads as "some timezone", most likely the reader's own, which
    # is the number sitting right beside it.
    return (f"{mark} `{_tct(hour['hour_start'])}` TCT {_ts(hour['hour_start'])}{bonus} — "
            + ", ".join(names))


def _hour_lines(hours: List[Dict], slots_per_hour: int, *,
                compact: bool = False) -> List[str]:
    """
    One line per hour, except that a RUN of completely empty hours collapses
    into one.

    ⚠️ Sixteen consecutive "nobody signed up" lines is sixteen lines nobody
    reads, and on a phone it pushes the hours that ARE covered off the screen —
    so the board is longest and least useful exactly when coverage is worst.
    A run says its span once.

    ⚠️ Only fully-empty, non-bonus hours collapse. A half-covered hour names
    somebody who needs a partner, and a bonus hour is a recruiting pitch; both
    have to stay visible as themselves.
    """
    out: List[str] = []
    run: List[Dict] = []

    def flush() -> None:
        if not run:
            return
        if len(run) == 1:
            out.append(_hour_line(run[0], slots_per_hour, compact=compact))
        else:
            first, last = run[0], run[-1]
            out.append(
                f"🔴 `{_tct(first['hour_start'])}`–`{_tct(last['hour_start'])}` TCT "
                f"{_ts(first['hour_start'])}–{_ts(last['hour_start'])} — "
                f"*{len(run)} hours, nobody signed up*")
        run.clear()

    for h in hours:
        empty = not h.get("watchers") and not h.get("bonus")
        if empty:
            run.append(h)
            continue
        flush()
        out.append(_hour_line(h, slots_per_hour, compact=compact))
    flush()
    return out


def build_board(watch: Dict, *, now_ms: int, hours_shown: int = 24,
                stale: bool = False, compact: bool = False) -> List[discord.Embed]:
    """The standing board: what is covered, what is not, and where the chain is going."""
    event = watch.get("event", {})
    slots_per_hour = int(event.get("slots_per_hour", 2))
    hours = [h for h in watch.get("hours", []) if h["hour_start"] + 3_600_000 > now_ms]

    horizon_hours = int(watch.get("gap_horizon_hours") or GAP_HORIZON_FALLBACK)
    horizon_ms = now_ms - (now_ms % 3_600_000) + horizon_hours * 3_600_000
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
        # ⚠️ `.get`, not `[...]`, and both spellings. The dashboard served the
        # rate as `hitsPerHour` while this read `hits_per_hour`, and the
        # KeyError took the whole tick down with it — including the pings.
        # ⚠️ The rate is also OPTIONAL in the rendering: a band with no rate is
        # still worth showing, and a missing field must never cost a board.
        rate = proj.get('hits_per_hour', proj.get('hitsPerHour'))
        band = f"**100k around** {_ts(proj['earliest'], 'f')} – {_ts(proj['latest'], 'f')}"
        lines.append(f"{band} · {rate:.0f} hits/h" if rate is not None else band)
    else:
        # ⚠️ Say it plainly. An absent projection is fine; a confident wrong one
        # is not.
        lines.append("**100k around** — not enough chain yet")

    lines.append("")
    if open_soon:
        lines.append(f"**{open_soon} slot{'' if open_soon == 1 else 's'} unfilled "
                     f"in the next {horizon_hours} hours**")
    else:
        lines.append(f"**Every slot in the next {horizon_hours} hours is covered.**")
    lines.append("")

    lines.extend(_hour_lines(hours[:hours_shown], slots_per_hour, compact=compact))

    embed = discord.Embed(
        title=f"{EMBED_TITLE} — {event.get('title', 'Chain')}",
        description="\n".join(lines)[:4000],
        color=color,
    )
    # ⚠️ A failed poll leaves the LAST GOOD board up, marked. Blanking it would
    # read as "nobody is signed up", which is the one message that must never be
    # wrong — and an unmarked stale board is worse still, because it reads as
    # current.
    footer = "Times shown in TCT and your own timezone · sign up on the dashboard"
    if stale:
        footer = "⚠️ Dashboard unreachable — this board is the last good reading · " + footer
    embed.set_footer(text=footer)
    return [embed]


def build_gap_ping(gaps: List[Dict], *, last_call_hours: int = 2) -> Optional[str]:
    """
    ONE message about every hour that needs cover this tick.

    ⚠️ One message per hour was four posts in a row the first time this ran
    live, because everything inside the horizon entered it at once. Four posts
    is how a channel learns to mute the bot, which costs more than the gaps do.

    ⚠️ **The local time cannot carry a zone name.** Discord renders `<t:…:t>` in
    each reader's own timezone, client-side — the bot never learns what that
    zone is, and there is no timestamp style that includes it. Printing "CST"
    would mean printing the SERVER's zone to everybody, which is worse than
    printing nothing: a member in London would read a confident, wrong label.
    The header says whose clock it is instead.

    `gaps` is `[{hour, open_slots, stage}]`. Returns None for an empty list.
    """
    if not gaps:
        return None

    lines = []
    for g in gaps:
        hour, open_slots, stage = g["hour"], g["open_slots"], g["stage"]
        slots = f"{open_slots} slot{'' if open_slots == 1 else 's'}"
        bonus = " ⭐ *double tickets*" if hour.get("bonus") else ""
        nobody = " — **nobody at all**" if not hour.get("watchers") else ""
        when = f"`{_tct(hour['hour_start'])}` TCT · {_ts(hour['hour_start'])}"
        if stage == "last-call":
            lines.append(f"⏰ {when} — {slots}{nobody}{bonus}, "
                         f"**starts in under {last_call_hours} hours**")
        else:
            lines.append(f"🔴 {when} — {slots}{nobody}{bonus}")

    subject = "hour still needs" if len(gaps) == 1 else "hours still need"
    # ⚠️ The second time is deliberately NOT explained. Once each line carries
    # "TCT" the pairing reads for itself — one labelled time, one in your own
    # clock — and saying so was noise on every message. It could not be
    # labelled properly anyway: Discord renders it in the reader's own zone,
    # client-side, so the bot never learns which zone that is.
    return (f"**{len(gaps)} {subject} cover** · sign up on the dashboard\n"
            + "\n".join(lines))


def build_travel_warning(shift: Dict) -> str:
    """
    One person, one problem: they are away and may not make their shift.

    ⚠️ Kept separate from the ordinary reminder even though both are about the
    same hour. The instruction differs — this one asks them to DROP the slot —
    and it fires much earlier, so folding the two together would either make
    the warning late or the reminder absurdly early.
    """
    who = _who(shift)
    travel = shift.get("travel") or {}
    return (f"{who} your chain watch starts at {_ts(shift['hour_start'])} and you are "
            f"**away** — {_travel_phrase(travel)}. "
            f"If that is too late, drop the slot on the dashboard so somebody can cover it.")


def build_shift_ping(watchers: List[Dict], *, hour_start: int, bonus: bool = False,
                     chain: Optional[Dict] = None, lead_in_minutes: int = 5) -> Optional[str]:
    """
    ONE message for everybody on the same hour.

    ⚠️ One message per watcher meant two near-identical posts seconds apart,
    each telling one of the pair about the other. Nobody reads the second, and
    a channel that trains people to skip the bot's messages costs more than the
    shift it is announcing.

    ⚠️ Mentions go in the CONTENT, never an embed. A mention inside an embed
    renders as a blue link and notifies nobody — which is exactly the trap the
    board falls into deliberately, and which this must not.
    """
    if not watchers:
        return None

    names = [_who(w) for w in watchers]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"

    when = f"`{_tct(hour_start)}` TCT · {_ts(hour_start)}"
    star = " ⭐ *double tickets this hour*" if bonus else ""
    chain = chain or {}
    at = f" Chain is at {chain['current']:,}." if chain.get("current") else ""
    # ⚠️ Trails the sentence rather than interrupting it. Being the only watcher
    # is the thing a leader needs to notice, and it reads as an afterthought
    # wedged between the name and the time.
    alone = " **You are the only watcher this hour.**" if len(names) == 1 else ""
    return (f"{who} — your chain watch starts at {when} "
            f"(in {lead_in_minutes} min).{star}{at}{alone}")


