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
import os
from typing import Optional, Tuple

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
      'outbound' — "Traveling to X"     (still flying away from Torn)
      'inbound'  — "Returning from X"   (flying back to Torn)
      'abroad'   — "In X"               (landed, can leave any time)
    Returns (None, None) for unrecognised strings.
    """
    if not description:
        return (None, None)
    for prefix, direction in (
        ("Traveling to ", "outbound"),
        ("Returning from ", "inbound"),
        ("In ", "abroad"),
    ):
        if description.startswith(prefix):
            return (direction, description[len(prefix):].strip())
    return (None, None)


def one_way_minutes(destination: Optional[str], flight_type: str) -> Optional[int]:
    """Look up one-way time. None when destination is missing/unknown so the
    caller can fall back to the prior yellow-only behaviour."""
    if not destination:
        return None
    info = _load()["destinations"].get(destination)
    if not info:
        return None
    return info.get(flight_type)
