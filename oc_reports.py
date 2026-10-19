"""
`/oc digest` and `/oc report` rendering (#1024). On request only — nothing here
is posted on a schedule (MonChoon, 2026-10-19).
"""

from typing import Dict, List

import discord


def digest_embed(slug: str, d: Dict) -> discord.Embed:
    m = d.get("members") or {}
    lines: List[str] = [
        f"**{m.get('seated', 0)}** of **{m.get('current', 0)}** members are in an OC; **{m.get('idle', 0)}** aren't.",
        "",
        "`Lvl  crimes  open  seated  waiting*`",
    ]
    for l in d.get("levels") or []:
        lines.append(f"`L{l['difficulty']:<3} {l['crimes']:>6} {l['open']:>5} {l['seated']:>7} {l['idle_qualified']:>8}`")
    lines.append("_*waiting: not in an OC, and clear the bar for an open seat at that level_")
    start = d.get("start_next") or []
    lines.append("")
    if start:
        for s in start:
            lines.append(f"➕ **Start another L{s['difficulty']}** — {s['short_by']} more qualified "
                         f"member{'s' if s['short_by'] != 1 else ''} waiting than open seats.")
    else:
        lines.append("No level has more qualified members waiting than open seats.")
    lines.append("_Scope isn't tracked by the dashboard, so it isn't in this digest._")
    return discord.Embed(title=f"OC supply — {slug.upper()}", description="\n".join(lines)[:4000], color=0x3498DB)


def report_embed(slug: str, r: Dict) -> discord.Embed:
    lines: List[str] = []
    n = r.get("finished", 0)
    lines.append(f"**{n}** crime{'s' if n != 1 else ''} finished in the last {r.get('days')} days — "
                 f"**{r.get('successful', 0)}** successful, **{r.get('failed', 0)}** failed.")
    if r.get("analysed"):
        lines.append(f"Seating efficiency: **{r['average_efficiency']}%** across {r['analysed']} crews "
                     f"(100% = every crew in its best arrangement). "
                     f"**{r.get('could_have_seated_better', 0)}** could have been seated better.")
    gaps = r.get("biggest_gaps") or []
    if gaps:
        lines.append("\n**Biggest seating gaps**")
        for g in gaps:
            lines.append(f"• {g['name']} (L{g['difficulty']}, {g['status'].lower()}, <t:{g['executed_at'] // 1000}:d>) "
                         f"— +{g['weighted_gain']} weighted CPR: " + "; ".join(g.get("moves") or []))
    fails = r.get("failures") or []
    if fails:
        lines.append("\n**Failures — the weakest heavy seat**")
        for f in fails:
            w = f["weakest"]
            lines.append(f"• {f['name']} (L{f['difficulty']}, <t:{f['executed_at'] // 1000}:d>) — "
                         f"{w['label']} ({w['weight_pct']}% weight) held by {w.get('member_name') or w['member_id']} at CPR {w['cpr']}")
    if r.get("caveat"):
        lines.append(f"\n_{r['caveat']}_")
    return discord.Embed(title=f"OC report — {slug.upper()}", description="\n".join(lines)[:4000], color=0x9B59B6)
