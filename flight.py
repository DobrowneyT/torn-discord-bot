"""
Flight-time lookups for the Traveling/Abroad severity check.

Loads flight_info.json (one-way minutes per destination per flight type) and
exposes helpers to:
  - parse a Torn status description into (direction, destination)
  - map a plane_image_type to one of our internal flight types
  - look up the one-way time for (destination, flight_type)

Ambiguity note: plane_image_type "airliner" can be either Standard or BCT.
We can't distinguish them, so airliner falls back to "standard" — the slowest
option — to bias toward over-alerting rather than missing a crime.
"""

import json
import logging
import os
import re
from typing import Optional, Tuple

log = logging.getLogger("flight")

_DATA: Optional[dict] = None


def _load() -> dict:
    global _DATA
    if _DATA is None:
        path = os.path.join(os.path.dirname(__file__), "flight_info.json")
        with open(path) as f:
            _DATA = json.load(f)
    return _DATA


def flight_type_for_plane(plane_image_type: Optional[str]) -> str:
    """Map plane_image_type → internal flight type. Unknown/missing → 'standard'
    (worst case), so we don't quietly under-estimate the trip."""
    if not plane_image_type:
        return "standard"
    return _load()["plane_image_to_flight_type"].get(plane_image_type, "standard")


def parse_destination(description: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse the Torn status description.

    Returns (direction, destination) where direction is one of:
      'outbound' — flying away from Torn
      'inbound'  — flying back to Torn
      'abroad'   — landed, can leave any time
    `destination` is always the FOREIGN country, in both directions, because
    that is what the flight time is looked up against.
    Returns (None, None) for unrecognised strings.

    ⚠️ **Torn actually writes "Traveling from Torn to UAE"** — not
    "Traveling to UAE". Observed live on /v2/faction/members:

        Traveling from Torn to UAE            -> ('outbound', 'UAE')
        Traveling from UAE to Torn            -> ('inbound',  'UAE')
        In Switzerland                        -> ('abroad',   'Switzerland')

    Every one of those returned (None, None) before this. That silently
    disabled the Traveling/Abroad severity check in the OC watcher as well as
    Chain Watch's arrival band — a check that cannot fire looks exactly like a
    check with nothing to report.

    The older prefixes are kept: they cost nothing, and a wrong (None, None)
    here is a warning nobody gets.
    """
    if not description:
        return (None, None)
    text = description.strip()

    leg = re.match(r"Traveling from\s+(.+?)\s+to\s+(.+)$", text, re.IGNORECASE)
    if leg:
        origin, target = leg.group(1).strip(), leg.group(2).strip()
        if target.lower() == "torn":
            return ("inbound", origin)
        return ("outbound", target)

    for prefix, direction in (
        ("Traveling to ", "outbound"),
        ("Returning from ", "inbound"),
        ("In ", "abroad"),
    ):
        if text.startswith(prefix):
            return (direction, text[len(prefix):].strip())
    return (None, None)


def canonical_destination(destination: Optional[str]) -> Optional[str]:
    """
    Torn's name for a place, mapped to flight_info.json's.

    ⚠️ Torn abbreviates: it says "UAE" where the table says "United Arab
    Emirates". Without this the parse succeeds, the lookup misses, and the
    result is still "arrival unknown" — the same symptom, one layer deeper.
    """
    if not destination:
        return None
    data = _load()
    if destination in data["destinations"]:
        return destination
    return data.get("aliases", {}).get(destination)


def one_way_minutes(destination: Optional[str], flight_type: str) -> Optional[int]:
    """Look up one-way time. None when destination is missing/unknown so the
    caller can fall back to the prior yellow-only behaviour."""
    name = canonical_destination(destination)
    if not name:
        if destination:
            # ⚠️ Logged, not swallowed. A destination Torn added, or renamed,
            # otherwise degrades to "arrival unknown" forever with nothing to
            # say which place it was.
            log.info("no flight time for destination %r — add it to flight_info.json "
                     "or its aliases", destination)
        return None
    info = _load()["destinations"].get(name)
    if not info:
        return None
    return info.get(flight_type)
