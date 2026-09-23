"""
When will this watcher actually land? (#785.)

⚠️ **The dashboard does not answer this, on purpose.** Torn publishes no arrival
time for a traveller, and the dashboard has no flight-duration data — the only
copy in that repo belongs to the War Room, which Chain Watch shares nothing
with. The bot already owns `flight.py` and `flight_info.json` for the OC
watcher, so the endpoint hands over the raw facts and the arithmetic happens
here.

⚠️ **No witnessed departure means no answer.** `departed_at` is null whenever the
sampler did not see the take-off — a restart, a deploy, the job simply not
running (the #330 rule, enforced in the dashboard's `latchDeparture`). Counting
from `now` instead would produce a confidently wrong landing time, and a lead
waits on it. **Wrong is worse than absent**, which is the whole reason that
field can be null.
"""

import logging
from typing import Dict, Optional

import flight

log = logging.getLogger("chain_eta")

MINUTE_MS = 60_000

#: How wide the band is, as a share of the flight.
#:
#: ⚠️ A band, never a point. `flight_info.json` is wiki-sourced one-way times
#: and the real trip varies with the exact moment of departure we witnessed —
#: which is itself only as precise as the sampling interval. A stated minute is
#: a promise we cannot keep, and somebody would plan around it.
BAND = 0.05

#: The band never collapses below this, however short the hop.
MIN_BAND_MS = 2 * MINUTE_MS


def _destination_for_eta(travel: Dict) -> Optional[str]:
    """
    Which country's flight time applies.

    ⚠️ For a RETURN flight that is the country being left, not the destination.
    The dashboard reports `destination: "Torn"` for an inbound leg — correct for
    telling a lead where somebody is headed, useless for timing it, because
    there is no "Torn" row in flight_info.json. The raw description is the
    source of truth and is served alongside precisely for this.
    """
    description = travel.get("description") or ""
    direction, place = flight.parse_destination(description)
    if direction in ("outbound", "inbound", "abroad") and place:
        return place
    # ⚠️ Torn writes "Returning to Torn from South Africa", which flight.py's
    # own prefixes do not cover — it looks for "Returning from ". Handled here
    # rather than by editing flight.py, which the OC watcher depends on.
    marker = " from "
    if description.startswith("Returning") and marker in description:
        return description.split(marker, 1)[1].strip()
    dest = travel.get("destination")
    return None if dest in (None, "Torn") else dest


def arrival_band(travel: Optional[Dict]) -> Optional[Dict]:
    """
    `{'earliest': ms, 'latest': ms}`, or None when we cannot honestly say.

    None for: not travelling, no witnessed departure, or a destination with no
    published flight time.
    """
    if not travel or travel.get("state") != "Traveling":
        return None
    departed_at = travel.get("departed_at")
    if not departed_at:
        return None
    place = _destination_for_eta(travel)
    minutes = flight.one_way_minutes(
        place, flight.flight_type_for_plane(travel.get("plane_image_type")))
    if not minutes:
        # ⚠️ An unknown destination is not an excuse to guess an average. The
        # formatter says "arrival unknown", which is true.
        log.info("no flight time for %r — leaving arrival unknown", place)
        return None
    landing = int(departed_at) + minutes * MINUTE_MS
    spread = max(int(minutes * MINUTE_MS * BAND), MIN_BAND_MS)
    return {"earliest": landing - spread, "latest": landing + spread}


def may_miss(travel: Optional[Dict], hour_start: int) -> bool:
    """
    Should this watcher be warned early that they may not make their shift?

    ⚠️ Judged on the LATEST end of the band, not the middle. The question is
    "could they be late", and half a band of slack is exactly the margin that
    turns a warning somebody could act on into a shift nobody covered.

    ⚠️ Already `Abroad` counts too. They are not in the air, so there is no band
    at all — but they also cannot hit anything from overseas, which is the one
    finding #774 treats as not a judgment call.
    """
    if not travel:
        return False
    if travel.get("state") == "Abroad":
        return True
    band = arrival_band(travel)
    if band is None:
        # ⚠️ In the air with no computable arrival is itself worth warning about.
        # Silence here would mean the least-known cases are the quietest ones.
        return travel.get("state") == "Traveling"
    return band["latest"] > hour_start
