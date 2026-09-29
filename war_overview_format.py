"""
Rendering a war's overview for Discord (#811).

⚠️ **Nothing is counted here.** Every number arrives already computed by the
dashboard's `/api/internal/war-overview`, which runs the same summary the page
does and is held level with it by a parity test. This module only decides how
those numbers read — so a disagreement between Discord and the page can only
ever be a rendering bug, never an arithmetic one.

⚠️ Pure functions, no discord.py types in the signatures, so the whole surface
is testable without a gateway connection.
"""

from typing import Any, Dict, List, Optional, Tuple

#: Discord truncates an embed description at 4096 and a field value at 1024.
DESCRIPTION_MAX = 4000
FIELD_MAX = 1000


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def war_label(war: Dict) -> str:
    """`vs Damage Inc [20747]`, or something honest when the name is missing."""
    name = war.get("opponent_name")
    oid = war.get("opponent_id")
    if name and oid:
        return f"vs {name} [{oid}]"
    if name:
        return f"vs {name}"
    if oid:
        return f"vs faction {oid}"
    return "vs an unnamed opponent"


def war_choice_label(war: Dict) -> str:
    """
    One line for the war picker's autocomplete.

    ⚠️ Dates come from epoch SECONDS on the wire. Never parse a timestamp
    string here — Postgres's bare two-digit offset is rejected outright by
    `datetime.fromisoformat` on older runtimes.
    """
    from datetime import datetime, timezone
    start = _int(war.get("start_at"))
    when = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%d %b %Y") if start else "?"
    live = "" if war.get("end_at") else " · LIVE"
    return f"{war_label(war)} — {when}{live}"


def _row(label: str, bucket: Dict, order: Tuple[str, ...] = ("attack", "assist", "loss", "total")) -> str:
    parts = [f"**{_int(bucket.get(k)):,}** {k.capitalize()}" for k in order]
    return f"{label} · " + " · ".join(parts)


def summary_lines(payload: Dict) -> List[str]:
    """
    The three rows, in the order the page shows them.

    ⚠️ Outgoing, Incoming, Revives — matching the page's stacked summary AND the
    three panels of its chart, so somebody reading both sees the same shape.
    """
    s = payload.get("summary") or {}
    out = s.get("outgoing") or {}
    inc = s.get("incoming") or {}
    rev = s.get("revives") or {}
    return [
        _row("🟢 **Outgoing**", out),
        _row("🔴 **Incoming**", inc),
        "🔵 **Revives** · "
        f"**{_int(rev.get('success')):,}** Success · "
        f"**{_int(rev.get('failure')):,}** Fail · "
        f"**{_int(rev.get('total')):,}** Total",
    ]


def window_line(payload: Dict) -> str:
    """
    `<t:…:f> → <t:…:f>` — rendered in each reader's own timezone by Discord.

    ⚠️ `<t:…>` takes epoch SECONDS; the payload speaks milliseconds. Dividing in
    the wrong place puts the war in 1970, which reads as a data problem rather
    than a units one.
    """
    w = payload.get("window") or {}
    a = _int(w.get("from_ms")) // 1000
    b = _int(w.get("to_ms")) // 1000
    if not a or not b:
        return "Window: unknown"
    live = "" if (payload.get("war") or {}).get("end_at") else "  ·  *still running*"
    return f"<t:{a}:f> → <t:{b}:f>{live}"


def scope_line(payload: Dict) -> str:
    """What was counted — so a filtered embed can never be mistaken for a full one."""
    if payload.get("warring_only"):
        return "Counting **only** the two warring factions."
    return "Counting **every** faction, including players in none."


def header(payload: Dict) -> str:
    """`Marnix0126 [2932241] vs Damage Inc [20747]`, or the faction equivalent."""
    war = payload.get("war") or {}
    if payload.get("mode") == "member":
        name = payload.get("member_name") or f"#{payload.get('member_id')}"
        return f"{name} [{payload.get('member_id')}] {war_label(war)}"
    return war_label(war)


def score_line(payload: Dict) -> Optional[str]:
    war = payload.get("war") or {}
    ours, theirs = _int(war.get("our_score")), _int(war.get("opponent_score"))
    if not ours and not theirs:
        return None
    # ⚠️ `is_termed` is PRINTED, never acted on. It is set by hand on the
    # dashboard, so an untoggled real war looks exactly like a termed one — the
    # bot must not decide anything from it.
    kind = " · termed" if war.get("is_termed") else ""
    return f"**{ours:,}** — **{theirs:,}**{kind}"


def build_overview(payload: Dict) -> Dict[str, Any]:
    """
    An embed-shaped dict: `{title, description, colour, footer}`.

    Returned as plain data rather than a `discord.Embed` so the tests never need
    discord.py — the same split `chain_formatter` uses.
    """
    lines: List[str] = []
    score = score_line(payload)
    if score:
        lines.append(score)
    lines.append(window_line(payload))
    lines.append("")
    lines.extend(summary_lines(payload))
    lines.append("")
    lines.append(scope_line(payload))

    return {
        "title": header(payload),
        "description": "\n".join(lines)[:DESCRIPTION_MAX],
        "colour": 0x3498DB,
        "footer": "Same figures as the War Overview page",
    }


def member_choices(payload: Dict, current: str = "") -> List[Tuple[str, str]]:
    """
    `(label, value)` pairs for the member autocomplete, filtered by `current`.

    ⚠️ Capped at 25 — Discord rejects an autocomplete response with more, and
    the rejection is silent from the user's side: the box simply shows nothing.
    """
    needle = (current or "").lower()
    out = []
    for m in payload.get("members") or []:
        name = str(m.get("name") or m.get("id"))
        if needle and needle not in name.lower():
            continue
        out.append((f"{name} [{m.get('id')}]", str(m.get("id"))))
    return out[:25]


def war_choices(payload: Dict, current: str = "") -> List[Tuple[str, str]]:
    """`(label, value)` pairs for the war autocomplete. Capped at 25, as above."""
    needle = (current or "").lower()
    out = []
    for w in payload.get("wars") or []:
        label = war_choice_label(w)
        if needle and needle not in label.lower():
            continue
        out.append((label, str(w.get("id"))))
    return out[:25]
