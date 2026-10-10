"""
The post-war member report, as an embed-shaped dict.

⚠️ **Plain data, not a `discord.Embed`** — the same split `war_overview_format`
and `chain_formatter` already use, so the tests never need discord.py.

⚠️ **Absence is rendered as absence, never as zero.** A member who never
answered the roll call and a member who answered "I will not be available" are
different facts, and so are "claimed nothing" and "we did not count this war
yet". A report that prints `0` for all of them is a report nobody can act on —
and this one is going into a thread with that member's name on it, where a wrong
figure is not a cosmetic problem.
"""

from typing import Any, Dict, List, Optional

DESCRIPTION_MAX = 4096

AVAILABILITY_LABEL = {
    "on_time": "on time",
    "late_1h": "1–2h late",
    "late_3h": "3–5h late",
    "late_6h": "6h+ late",
    "unavailable": "not available",
}


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def war_title(p: Dict) -> str:
    w = p.get("war") or {}
    m = p.get("member") or {}
    name = m.get("name") or f"id {m.get('id')}"
    opp = w.get("opponent_name") or f"faction {w.get('opponent_id')}"
    return f"{name} — war vs {opp}"


def score_line(p: Dict) -> str:
    """
    THIS member's contribution, raw and normalised.

    MonChoon, 2026-10-10: *"since this is per member, I dont think we need the
    Won line, instead can we use their score from the war?"* — the faction
    result is the same sentence in everybody's thread and says nothing about the
    person whose thread it is.

    ⚠️ **Both figures come from the same ledger.** Torn's war report and our
    `faction_attacks` are different sources, and a sync gap puts them out of
    step — taking "raw" from one and "normalised" from the other would print a
    pair whose difference is meaningless.

    ⚠️ The normalised figure caps bonus-chain hits at 10 respect, which is the
    basis every payout is calculated on. Naming it is the point: a member who
    landed the 25k hit sees a large raw number and a smaller payout one, and
    that gap needs an explanation on the line rather than in a DM later.
    """
    sc = p.get("score")
    if not sc:
        # ⚠️ Not zero. No contribution row means we have nothing for them in
        # this war, which is a different fact from having scored nothing.
        return "⚔️ **Score** · not recorded"
    raw = _num(sc.get("raw"))
    norm = _num(sc.get("normalised"))
    line = f"⚔️ **Score** · **{raw:,.2f}** raw · **{norm:,.2f}** normalised"
    bonus = _num(sc.get("bonus"))
    if bonus > 0:
        # Only when capping actually bit — "−0.00 from bonus hits" on every
        # ordinary row is noise that trains people to skip the line.
        line += f" _(−{bonus:,.2f} from bonus hits capped at 10)_"
    return line


def hits_lines(p: Dict) -> List[str]:
    """
    The same three rows `/rw overview` prints, in the same order.

    ⚠️ Same order AND same wording on purpose: somebody comparing their thread
    against the command should not have to work out whether they match.
    """
    s = p.get("summary") or {}
    out = s.get("outgoing") or {}
    inc = s.get("incoming") or {}
    rev = s.get("revives") or {}
    return [
        f"🟢 **Outgoing** · **{_int(out.get('attack')):,}** Attack · "
        f"**{_int(out.get('assist')):,}** Assist · **{_int(out.get('loss')):,}** Loss · "
        f"**{_int(out.get('total')):,}** Total",
        f"🔴 **Incoming** · **{_int(inc.get('attack')):,}** Attack · "
        f"**{_int(inc.get('assist')):,}** Assist · **{_int(inc.get('loss')):,}** Loss · "
        f"**{_int(inc.get('total')):,}** Total",
        f"🔵 **Revives** · **{_int(rev.get('success')):,}** Success · "
        f"**{_int(rev.get('failure')):,}** Fail · **{_int(rev.get('total')):,}** Total",
    ]


def roll_call_line(p: Dict) -> str:
    """
    What they said they would do, and whether they did it.

    ⚠️ "Did not answer" is NOT "said no". The roll call is a question some
    people never see, and printing silence as a refusal in somebody's own
    performance thread is an accusation the data does not support.
    """
    rc = p.get("roll_call")
    if not rc or not rc.get("responded"):
        return "📋 **Roll call** · no answer recorded"

    said = "on at the start" if rc.get("on_at_start") else "not on at the start"
    avail = AVAILABILITY_LABEL.get(rc.get("availability"), rc.get("availability") or "—")
    bits = [f"said **{said}** ({avail})"]

    if not rc.get("on_at_start"):
        # Only meaningful for a "no" — the panel renders "—" for everyone else.
        if rc.get("can_self_hospital"):
            bits.append("could self-hosp")
        elif rc.get("needs_merc"):
            bits.append("needed a merc")

    offset = rc.get("first_action_offset_sec")
    if offset is None:
        # ⚠️ Not "did not show". We simply have no attack to measure against,
        # which is also true of somebody who spent the war being revived.
        bits.append("turn-up not measured")
    elif rc.get("on_at_start"):
        mins = offset // 60
        bits.append("**turned up**" if offset <= 900 else f"first hit **{mins}m** in")

    if rc.get("revised"):
        bits.append("_(answer revised)_")
    return "📋 **Roll call** · " + " · ".join(bits)


def dibs_line(p: Dict) -> str:
    """
    Their claiming record.

    ⚠️ `None` means the war has not been summarised yet — the job waits for the
    attacks to land — and that is different from "claimed nothing". Saying
    "0 claims" to somebody who claimed twenty would be the worst version of this
    whole feature.
    """
    d = p.get("dibs")
    if d is None:
        return "🎯 **Dibs** · not counted yet"
    claims = _int(d.get("claims"))
    if claims == 0 and _int(d.get("took_from_others")) == 0:
        return "🎯 **Dibs** · no claims"

    bits = [f"**{claims:,}** claimed", f"**{_int(d.get('converted')):,}** converted"]
    for key, label in (("no_swing", "no swing"), ("taken_from", "taken from them")):
        n = _int(d.get(key))
        if n:
            bits.append(f"**{n:,}** {label}")
    # ⚠️ Green is stated whenever it happened, because it is the line that
    # EXPLAINS a claim that produced nothing — and the one somebody points at
    # when they say their dibs was taken.
    green = _int(d.get("green_means_go"))
    if green:
        bits.append(f"**{green:,}** green (fair game)")
    took = _int(d.get("took_from_others"))
    if took:
        bits.append(f"**{took:,}** taken from others")
    return "🎯 **Dibs** · " + " · ".join(bits)


def stacks_line(p: Dict) -> Optional[str]:
    """Pre-war readiness, when it was snapshotted. Omitted entirely when not."""
    st = p.get("stacks")
    if not st:
        return None
    energy = _int(st.get("energy"))
    od = " · ⚠️ **overdosed**" if st.get("overdosed") else ""
    return f"⚡ **Pre-war** · {energy:,} energy{od}"


def build_member_report(p: Dict) -> Dict[str, Any]:
    """An embed-shaped dict: `{title, description, colour, footer}`."""
    lines: List[str] = [score_line(p), ""]
    lines.extend(hits_lines(p))
    lines.append("")
    lines.append(roll_call_line(p))
    lines.append(dibs_line(p))
    stacks = stacks_line(p)
    if stacks:
        lines.append(stacks)

    # ⚠️ The war result survives only as the colour. It is the same sentence in
    # every member's thread, so it earns a stripe rather than a line.
    won = (p.get("war") or {}).get("won")
    colour = 0x2ECC71 if won is True else 0xE74C3C if won is False else 0x95A5A6
    return {
        "title": war_title(p),
        "description": "\n".join(lines)[:DESCRIPTION_MAX],
        "colour": colour,
        "footer": "Same figures as the War Overview page · /rw overview",
    }
