"""
Attach member names + item names to the raw /v2/faction/crimes payload, and
extract a member-status map from /v2/faction/members.

Why: the crimes endpoint only returns ids on slot.user and item_requirement,
so we need a join with members + an item-id lookup to produce display names.
"""

from typing import Dict, Iterable, List, Set

from config import UNAVAILABLE_STATES


def collect_user_ids(crimes: Iterable[Dict]) -> Set[int]:
    out: Set[int] = set()
    for c in crimes:
        for s in c.get("slots", []):
            user = s.get("user")
            if user and user.get("id") is not None:
                out.add(user["id"])
    return out


def collect_item_ids(crimes: Iterable[Dict]) -> Set[int]:
    out: Set[int] = set()
    for c in crimes:
        for s in c.get("slots", []):
            req = s.get("item_requirement")
            if req and req.get("id") is not None:
                out.add(req["id"])
    return out


def build_member_lookup(members_resp: Dict) -> Dict[int, Dict]:
    """user_id -> raw member object from /v2/faction/members."""
    return {m["id"]: m for m in members_resp.get("members", []) if "id" in m}


def build_status_map(members_resp: Dict) -> Dict[int, Dict]:
    """
    user_id -> {state, until, description, plane_image_type} for members in an
    unavailable state. Members in "Okay" / online / etc. are intentionally
    omitted so a quick `id in status_map` check answers "is this member
    unavailable?".

    description and plane_image_type are needed by the travel-severity check
    in alerts.py — description carries "Traveling to Hawaii" / "In Mexico" /
    etc., and plane_image_type maps to a flight speed tier.
    """
    out: Dict[int, Dict] = {}
    for m in members_resp.get("members", []):
        status = m.get("status") or {}
        if not isinstance(status, dict):
            continue
        state = status.get("state") or status.get("description")
        if state and state in UNAVAILABLE_STATES:
            out[m.get("id")] = {
                "state": state,
                "until": status.get("until") or 0,
                "description": status.get("description") or "",
                "plane_image_type": status.get("plane_image_type"),
            }
    return out


def enrich_crimes(
    crimes: List[Dict],
    member_lookup: Dict[int, Dict],
    item_names: Dict[int, str],
) -> List[Dict]:
    """
    Return a shallow copy of `crimes` with slot.user.name and
    slot.item_requirement.name populated where possible.
    """
    enriched = []
    for c in crimes:
        new_slots = []
        for s in c.get("slots", []):
            new_slot = dict(s)

            user = s.get("user")
            if user:
                u = dict(user)
                if "name" not in u or not u["name"]:
                    member = member_lookup.get(u.get("id"))
                    if member:
                        u["name"] = member.get("name")
                new_slot["user"] = u

            req = s.get("item_requirement")
            if req:
                r = dict(req)
                if "name" not in r or not r["name"]:
                    r["name"] = item_names.get(r.get("id"))
                new_slot["item_requirement"] = r

            new_slots.append(new_slot)

        new_crime = dict(c)
        new_crime["slots"] = new_slots
        enriched.append(new_crime)
    return enriched
