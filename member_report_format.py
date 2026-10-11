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
    """
    Whose report this is, and which war.

    ⚠️ **Termed is stated in the title**, because it changes how every number
    below should be read: a termed war is an agreed result, so a low score is
    not the same evidence it would be in a real one. MonChoon asked for it.

    ⚠️ `is_termed` is a HAND toggle on the dashboard's RW History page ("Set
    Real" / "Set Termed"), defaulting to real. A termed war nobody toggled is
    indistinguishable from a real one, so this reports the label, not a fact
    about the war.
    """
    w = p.get("war") or {}
    m = p.get("member") or {}
    name = m.get("name") or f"id {m.get('id')}"
    opp = w.get("opponent_name") or f"faction {w.get('opponent_id')}"
    kind = "termed war" if w.get("is_termed") else "war"
    return f"{name} — {kind} vs {opp}"


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
    basis every payout is calculated on. The DEDUCTION is named on the line —
    a member who landed the 25k hit sees a large raw number and a smaller
    payout one, and that gap needs accounting for — but the rule itself stays
    here. "from bonus hits" is what a reader needs; "capped at 10" is what a
    maintainer needs, and only one of them is reading the embed.
    """
    sc = p.get("score")
    if sc is None:
        # ⚠️ **Only an EMPTY LEDGER reaches here.** The dashboard sends a real
        # zero when it has the war's attacks and this member is not in them, so
        # "not recorded" now means what it says: we have nothing for this war at
        # all. It used to be sent for anybody who had not scored, and MonChoon
        # read it — correctly — as looking like a data-loss bug.
        return "⚔️ **Score** · not recorded"

    raw = _num(sc.get("raw"))
    norm = _num(sc.get("normalised"))
    if _int(sc.get("hits")) == 0:
        # ⚠️ Said plainly rather than as "0.00 raw · 0.00 normalised", which
        # invites the reader to wonder which of the two zeros is the real one.
        return "⚔️ **Score** · **0** · no scoring hits"

    line = f"⚔️ **Score** · **{raw:,.2f}** raw · **{norm:,.2f}** normalised"
    # ⚠️ `bonus_reduction`, the key the dashboard actually sends. This read
    # `bonus` and therefore never fired — the THIRD field-name mismatch of the
    # same kind, and the one that hid itself: a missing deduction looks exactly
    # like a member who landed no bonus hits.
    bonus = _num(sc.get("bonus_reduction"))
    if bonus > 0:
        # Only when capping actually bit — "−0.00 from bonus hits" on every
        # ordinary row is noise that trains people to skip the line.
        line += f" _(−{bonus:,.2f} from bonus hits)_"
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

    # ⚠️ **Turn-up is only asked of somebody who said they would BE there.**
    # Reece said "not on at the start" and made no attacks, and the first
    # version still printed "turn-up not measured" — noise about a question he
    # had already answered, in his own performance thread. Nothing to measure
    # against is the expected outcome of the answer he gave.
    offset = rc.get("first_action_offset_sec")
    if rc.get("on_at_start"):
        if offset is None:
            # ⚠️ They said they would be on and we have no attack from them.
            # Stated as the fact it is — "no attacks recorded" — not as a
            # verdict: somebody revived all war also records none.
            bits.append("⚠️ **no attacks recorded**")
        else:
            mins = offset // 60
            bits.append("**turned up**" if offset <= 900 else f"first hit **{mins}m** in")

    if rc.get("revised"):
        bits.append("_(answer revised)_")
    return "📋 **Roll call** · " + " · ".join(bits)


def dibs_line(p: Dict) -> Optional[str]:
    """
    Their claiming record, or None when the line should not appear at all.

    ⚠️ **THREE states, not two**, and they lead to different lines because they
    lead to different truths:

      disabled → omitted entirely. This tenant does not run the dibs summary,
                 so the absence says nothing about the member. Printing
                 "not counted yet" in all ~94 threads every war would be a
                 standing complaint about a config choice.
      pending  → "not counted yet". The job is on and has not finished; saying
                 "0 claims" to somebody who claimed twenty would be the worst
                 version of this whole feature.
      measured → the numbers, and a genuine zero reads as "no claims" because
                 the job ran and there were none.

    MonChoon, 2026-10-11: *"If dibs summary is disabled we can ship the war
    reports without it rather than wait."*
    """
    d = p.get("dibs")
    # ⚠️ A payload with no `dibs` key at all is treated as pending, not as
    # measured-zero. An older dashboard that does not send the field must not
    # cause "no claims" to be posted about everybody.
    if d is None:
        return "🎯 **Dibs** · not counted yet"
    state = d.get("state")
    if state == "disabled":
        return None
    if state != "measured":
        return "🎯 **Dibs** · not counted yet"
    counts = d.get("counts")
    if counts is None:
        # ⚠️ Measured but no counts is a dashboard bug, not a zero. Say nothing
        # rather than assert something false about a named person.
        return "🎯 **Dibs** · not counted yet"
    d = counts
    claims = _int(d.get("claims"))
    if claims == 0 and _int(d.get("took_from_others")) == 0:
        return "🎯 **Dibs** · no claims"

    # ⚠️ **These eleven are mutually exclusive and exhaustive: they SUM to
    # `claims`.** The dashboard's schema says so explicitly — "a leaderboard
    # whose columns do not add up is one nobody can check, which is the
    # opposite of what a metric with members' names on it needs".
    #
    # The first version printed four of them beside two figures from a
    # DIFFERENT axis, so a real row read "34 claimed · 25 converted · 2 no swing
    # · 2 taken from them · 1 taken from others" — which adds to 30, and the
    # missing 5 (3 attempted, 2 released) were simply never shown. MonChoon
    # caught it by doing the arithmetic.
    # ⚠️ **These eleven are mutually exclusive and exhaustive: they SUM to
    # `claims`.** The dashboard's schema says so explicitly — "a leaderboard
    # whose columns do not add up is one nobody can check, which is the
    # opposite of what a metric with members' names on it needs".
    #
    # ⚠️ **Grouped by WHOSE DOING IT WAS**, which is the only question a reader
    # of somebody's performance thread is actually asking. Eleven raw outcomes
    # made the line unreadable and put jargon in it — MonChoon had to ask what
    # "hopped" meant, which is a fair question about a word in a permanent
    # record with his members' names on it.
    #
    # ⚠️ `overridden` groups with "void", NOT with "dropped": a lead taking a
    # claim off somebody is not that member giving it up, and filing it as
    # theirs would read as a choice they did not make.
    groups = [
        # Theirs, and it went well.
        ("won", ["converted"]),
        # Theirs, they tried, it did not land. Still turned up.
        ("lost", ["attempted"]),
        # A teammate finished it. Not their failure, and worth seeing.
        ("taken by us", ["taken_from"]),
        # ⚠️ THE accountability bucket: they held it and never swung.
        ("no swing", ["no_swing"]),
        # Their own choice to let it go — gave it back, or claimed elsewhere.
        ("dropped", ["released", "hopped"]),
        # ⚠️ Nobody's failure: died elsewhere, board wiped, a lead released it.
        ("void", ["outside", "board_reset", "overridden"]),
        # Still theirs when the war ended.
        ("still held", ["still_open"]),
        # ⚠️ Visibly unexplained rather than quietly folded into "void".
        ("unexplained", ["unknown"]),
    ]
    shown = []
    for label, keys in groups:
        n = sum(_int(d.get(k)) for k in keys)
        if n:
            shown.append((label, n))
    total = sum(n for _, n in shown)

    parts = [f"**{claims:,}** claimed"]
    if shown:
        joined = " · ".join(f"**{n:,}** {label}" for label, n in shown)
        # ⚠️ "=" only when it genuinely adds up. A mismatch means a bucket this
        # formatter does not know about, and claiming an audit that does not
        # hold is worse than not claiming one.
        parts.append(("= " if total == claims else "· of which ") + joined)

    line = "🎯 **Dibs** · " + " ".join(parts)

    # ⚠️ A SECOND axis, on its own line. These do not belong in the sum above:
    # "swung" overlaps converted/attempted, and the other two are about claims
    # that were never this member's to begin with. Putting them in the same
    # ·-separated run is precisely what made the first version look wrong.
    other = []
    swung = _int(d.get("swung"))
    if swung:
        other.append(f"**{swung:,}** swung at")
    took = _int(d.get("took_from_others"))
    if took:
        other.append(f"**{took:,}** taken from others")
    # ⚠️ Green is stated whenever it happened, because it EXPLAINS a claim that
    # produced nothing — and it is the line somebody points at when they say
    # their dibs was taken.
    green = _int(d.get("green_means_go"))
    if green:
        other.append(f"**{green:,}** green (fair game)")
    if other:
        line += "\n↳ also · " + " · ".join(other)
    return line


def stacks_line(p: Dict) -> Optional[str]:
    """
    Pre-war readiness, when it was snapshotted.

    ⚠️ **`has_reading` false is "we could not read it", NOT zero energy.** The
    dashboard sends `energy: null` in that case, and printing `0 energy` reads
    as "turned up on empty" — an accusation in somebody's permanent thread that
    the data does not support. This went out wrong once: the payload sent
    `energy_current`, this function looked for `energy`, and a member with 965
    energy was published as having 0.

    ⚠️ An OD is stated whenever it happened and omitted when it did not —
    MonChoon: *"if they had an OD it would be nice to note that. I think if they
    didnt we can omit it."* It is worth saying even when energy is unreadable,
    which is why the no-reading branch still checks it.
    """
    st = p.get("stacks")
    if not st:
        return None

    od = st.get("overdosed")
    if not st.get("has_reading"):
        # Nothing measurable and nothing to flag: say nothing at all rather
        # than spend a line on "we do not know".
        if not od:
            return None
        return "⚡ **Pre-war** · ⚠️ **overdosed** · _(energy not measured)_"

    energy = _int(st.get("energy"))
    suffix = " · ⚠️ **overdosed**" if od else ""
    return f"⚡ **Pre-war** · {energy:,} energy{suffix}"


def build_member_report(p: Dict) -> Dict[str, Any]:
    """An embed-shaped dict: `{title, description, colour, footer}`."""
    lines: List[str] = [score_line(p), ""]
    lines.extend(hits_lines(p))
    lines.append("")
    lines.append(roll_call_line(p))
    # ⚠️ Omitted entirely when this tenant does not run the dibs summary.
    dibs = dibs_line(p)
    if dibs:
        lines.append(dibs)
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
