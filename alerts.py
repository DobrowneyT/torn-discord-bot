"""
Pure logic: turn a list of (enriched) crime dicts + a member-status map
into a structured alerts dict suitable for the formatter.

The crimes passed in are expected to already carry display names on
slot.user.name and slot.item_requirement.name (see enrich.py). If those
names are missing the code falls back to "user_<id>" / "item_<id>".

Three alert categories per crime:
  - missing_items: assigned member doesn't have the slot's required item
                   AND crime executes within WINDOW_MISSING_ITEMS_HOURS
  - unavailable:   assigned member is in Hospital/Jail/Abroad/etc.
                   AND crime executes within WINDOW_UNAVAILABLE_HOURS
  - low_cpr:       assigned member's CPR is below the configured threshold
                   for that crime+position (no time window)

Crimes with zero issues are filtered out.
"""

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import flight
from config import (
    ACTIVE_CRIME_STATUSES,
    CPR_REQUIREMENTS,
    CRIME_URL_TEMPLATE,
    DEFAULT_CPR_THRESHOLD,
    UNAVAILABLE_STATES,
    WINDOW_MISSING_ITEMS_HOURS,
    WINDOW_UNAVAILABLE_HOURS,
)

_INSTANCE_SUFFIX = re.compile(r"\s*#\d+\s*$")

OverrideKey = Tuple[int, int, str]  # (crime_id, user_id, position_label)

# Severity rules (per entry):
#   red    → critical: drives embed color, also signals bot.py to send-and-delete
#            a heartbeat to keep the thread alive
#   yellow → warning: shown but not critical enough to wake an archived thread
#   None   → no severity (ordinary alert)
#
# Hospital/Jail/Federal severities depend on whether the member's `until`
# timestamp overlaps the crime's `ready_at`. Traveling/Abroad show up
# only inside the 6h window so they're always at least yellow.
_OVERLAP_STATES = {"Hospital", "Jail", "Federal"}
_TRAVEL_STATES = {"Traveling", "Abroad"}

# CPR severity bands (gap = required - cpr).
_CPR_RED_GAP = 15  # gap >= 15 → red
_CPR_YELLOW_GAP = 10  # 10 <= gap < 15 → yellow; below 10 also yellow but flagged less aggressively

# Missing-item severity bands (hours until crime executes).
_MISSING_RED_HOURS = 1     # < 1h  → red
_MISSING_YELLOW_HOURS = 12 # < 12h → yellow; otherwise blue (advance notice)


def build_alerts(
    crimes: List[Dict],
    member_status: Optional[Dict[int, Dict]],
    now_ts: int,
    *,
    cpr_overrides: Optional[Set[OverrideKey]] = None,
) -> Dict:
    """
    Args:
        crimes: list of enriched crime dicts (see enrich.enrich_crimes).
        member_status: optional map of user_id -> {"state": str, "until": int}.
                       Pass None or {} if status data isn't available this tick.
        now_ts: current unix timestamp.
        cpr_overrides: optional set of (crime_id, user_id, position) tuples
                       that have been explicitly approved; matching low_cpr
                       entries are suppressed (counted instead).

    Returns:
        {
          "generated_at": iso8601 str,
          "crimes": [ ...one entry per crime with at least one issue... ]
        }
    """
    member_status = member_status or {}
    cpr_overrides = cpr_overrides or set()

    flagged = []
    for crime in crimes:
        if crime.get("status") not in ACTIVE_CRIME_STATUSES:
            continue
        ready_at = crime.get("ready_at")
        if not ready_at:
            continue

        hours_until = (ready_at - now_ts) / 3600.0
        crime_name = crime.get("name", "")
        position_thresholds = CPR_REQUIREMENTS.get(crime_name, {}).get("positions", {})

        # Status drives which checks run:
        #   Planning  → crime is full; ready_at is the execution time. All
        #               three checks apply.
        #   Recruiting → crime is not full; it can't execute until it fills,
        #               so missing-items and unavailability don't matter yet.
        #               Only the CPR check fires. ready_at then represents
        #               when planning will pause if no one new joins.
        is_full = crime.get("status") == "Planning"
        phase = "executing" if is_full else "pausing"

        missing_items: List[Dict] = []
        unavailable: List[Dict] = []
        low_cpr: List[Dict] = []

        for slot in crime.get("slots", []):
            user = slot.get("user")
            if not user:
                continue

            label = _position_label(slot)
            user_id = user.get("id")
            user_name = user.get("name") or f"user_{user_id}"

            # 1) Missing item — only when crime is full (Planning) and within 24h window.
            if is_full and hours_until <= WINDOW_MISSING_ITEMS_HOURS:
                req = slot.get("item_requirement")
                if req and not req.get("is_available", True):
                    missing_items.append({
                        "user_id": user_id,
                        "name": user_name,
                        "position": label,
                        "item": req.get("name") or f"item_{req.get('id')}",
                        "severity": _missing_item_severity(hours_until),
                    })

            # 2) Unavailable — only when crime is full (Planning) and within 6h window.
            if is_full and hours_until <= WINDOW_UNAVAILABLE_HOURS:
                status = member_status.get(user_id)
                if status and status.get("state") in UNAVAILABLE_STATES:
                    severity = _unavailable_severity(status, ready_at, now_ts)
                    # "suppress" — inbound traveler who will land well before
                    # the crime executes, no alert needed at any severity.
                    if severity != "suppress":
                        unavailable.append({
                            "user_id": user_id,
                            "name": user_name,
                            "position": label,
                            "state": status["state"],
                            "until": status.get("until") or 0,
                            "description": status.get("description") or "",
                            "severity": severity,
                        })

            # 3) Low CPR — applies whether the crime is full or recruiting.
            # Falls back to DEFAULT_CPR_THRESHOLD when this crime or position
            # isn't tuned in config.
            required = _resolve_cpr_threshold(label, position_thresholds)
            cpr = slot.get("checkpoint_pass_rate")
            if cpr is not None and cpr < required:
                cpr_int = int(round(float(cpr)))
                low_cpr.append({
                    "user_id": user_id,
                    "name": user_name,
                    "position": label,
                    "cpr": cpr_int,
                    "required": required,
                    "severity": _cpr_severity(cpr_int, required),
                })

        # Apply CPR overrides AFTER collecting low_cpr so the count of
        # approvals is accurate (and the formatter can show it).
        approved = 0
        if cpr_overrides:
            kept = []
            for entry in low_cpr:
                key = (crime["id"], entry["user_id"], entry["position"])
                if key in cpr_overrides:
                    approved += 1
                else:
                    kept.append(entry)
            low_cpr = kept

        if missing_items or unavailable or low_cpr:
            flagged.append({
                "id": crime["id"],
                "name": crime_name,
                "phase": phase,
                "executes_at": ready_at,
                "hours_until": round(hours_until, 1),
                "url": CRIME_URL_TEMPLATE.format(crime_id=crime["id"]),
                "missing_items": missing_items,
                "unavailable": unavailable,
                "low_cpr": low_cpr,
                "low_cpr_approved": approved,
            })

    flagged.sort(key=lambda c: c["executes_at"])

    return {
        "generated_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "crimes": flagged,
    }


def _position_label(slot: Dict) -> str:
    """Prefer position_info.label (e.g. "Muscle #1"); fall back to bare position."""
    info = slot.get("position_info") or {}
    return info.get("label") or slot.get("position") or ""


def _unavailable_severity(status: Dict, ready_at: int, now_ts: int) -> Optional[str]:
    """Severity for an unavailable member.

    Hospital/Jail/Federal:
      - until_ts > ready_at  → red (still unavailable when crime executes)
      - until_ts == 0        → red (unknown duration; treat as worst case)
      - until_ts <= ready_at → None (will be back before crime, low concern)

    Traveling/Abroad: delegated to _travel_severity, which uses plane type +
    destination to decide red / yellow / "suppress" (member will land in
    plenty of time, so no alert at all).
    """
    state = status["state"]
    until_ts = status.get("until") or 0

    if state in _OVERLAP_STATES:
        if not until_ts or until_ts > ready_at:
            return "red"
        return None
    if state in _TRAVEL_STATES:
        return _travel_severity(status, ready_at, now_ts)
    return None


def _travel_severity(status: Dict, ready_at: int, now_ts: int) -> Optional[str]:
    """Severity for a Traveling / Abroad member.

    Required time the member needs in order to be back at their desk:
      outbound ("Traveling to X")  → 2 × one-way (worst case: still going out,
                                                  must come back)
      abroad   ("In X")            → 1 × one-way (already landed, must return)
      inbound  ("Returning from X")→ 1 × one-way (worst case: just took off back)

    If required > seconds-until-crime → "red" (will likely miss execution).
    Otherwise:
      outbound / abroad → "yellow" (still flying within the 6h window, but
                                    will make it back)
      inbound           → "suppress" (sufficient buffer to land; no alert)

    Falls back to "yellow" when we can't identify the destination or plane
    type — same as the previous behaviour, so unknowns don't go silent.
    """
    direction, destination = flight.parse_destination(status.get("description"))
    flight_type = flight.flight_type_for_plane(status.get("plane_image_type"))
    one_way = flight.one_way_minutes(destination, flight_type)

    if one_way is None or direction is None:
        return "yellow"

    secs_until_crime = ready_at - now_ts
    one_way_secs = one_way * 60

    if direction == "outbound":
        required = 2 * one_way_secs
        return "red" if required > secs_until_crime else "yellow"
    if direction == "abroad":
        return "red" if one_way_secs > secs_until_crime else "yellow"
    if direction == "inbound":
        # If even the worst-case full one-way back wouldn't miss the crime,
        # the member is safe — suppress entirely (user's explicit ask).
        return "red" if one_way_secs > secs_until_crime else "suppress"
    return "yellow"


def _missing_item_severity(hours_until: float) -> str:
    """Severity for a missing-item alert based on time until execution.

    < 1h  → red    (critical — won't get the item in time)
    < 12h → yellow (warning — limited time to acquire)
    else  → blue   (advance notice — plenty of time to fix)
    """
    if hours_until < _MISSING_RED_HOURS:
        return "red"
    if hours_until < _MISSING_YELLOW_HOURS:
        return "yellow"
    return "blue"


def _cpr_severity(cpr: int, required: int) -> Optional[str]:
    """Severity for a low-CPR alert based on how far below the target.

    gap = required - cpr
      gap >= 15 → red
      gap > 0   → yellow
    """
    gap = required - cpr
    if gap >= _CPR_RED_GAP:
        return "red"
    if gap > 0:
        return "yellow"
    return None


def _resolve_cpr_threshold(label: str, thresholds: Dict[str, int]) -> int:
    """
    Try the suffixed label first ("Muscle #1"), then the base name ("Muscle"),
    and finally fall back to DEFAULT_CPR_THRESHOLD. The config mixes both
    styles and doesn't list every crime/role, so we always have to be ready
    to fall through to the default.
    """
    if label in thresholds:
        return thresholds[label]
    base = _INSTANCE_SUFFIX.sub("", label)
    if base in thresholds:
        return thresholds[base]
    return DEFAULT_CPR_THRESHOLD
