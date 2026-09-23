"""
Hand-crafted Chain Watch payloads for Phase 1 (formatter iteration).

Shape matches what the dashboard's /api/internal/chain-watch actually returns
(dashboard #782), so chain_preview.py can feed these straight to the formatter
and Phase 3 can swap the source without touching rendering.

⚠️ **Keep this honest or delete it.** A mock that has drifted from the endpoint
is worse than no mock: it renders, it looks right, and it proves nothing. The
travel block here carries the RAW facts the dashboard serves — no arrival time,
because the dashboard has no flight-duration data. The bot computes the band
itself from flight.py (#785); an earlier version of this file invented
`eta_earliest`/`eta_latest`, which is exactly the drift being warned about.

⚠️ The numbers are the real October 2025 run's shape: 2 watchers an hour, a
graveyard stretch that nobody wants, and the single-covered hours that were 20%
of that event (43 of 211 served hours had one watcher, not two; none had zero).
"""

from typing import Dict, List

HOUR = 3600


def mock_watch(now_ts: int) -> Dict:
    """An event mid-flight: some hours covered, some thin, one naked, one flyer."""
    top = now_ts - (now_ts % HOUR)

    def hour(offset: int, watchers: List[Dict], bonus: bool = False) -> Dict:
        return {
            "hour_start": (top + offset * HOUR) * 1000,
            "bonus": bonus,
            "slots_per_hour": 2,
            "watchers": watchers,
            "open_slots": max(0, 2 - len(watchers)),
        }

    def w(member_id: int, name: str, travel=None) -> Dict:
        return {"member_id": str(member_id), "name": name, "travel": travel}

    return {
        "server_now": now_ts * 1000,
        # ⚠️ Served, not assumed. The bot is forbidden its own copy so the board
        # and the dashboard page cannot disagree about what "soon" means.
        "gap_horizon_hours": 6,
        "event": {
            "title": "Halloween 100k Chain",
            "slots_per_hour": 2,
            "starts_at": (top - 20 * HOUR) * 1000,
            "ends_at": (top + 52 * HOUR) * 1000,
            "actually_ended_at": None,
            "bonus_multiplier": 2,
            "bonus_applies_to": "tickets",
        },
        "chain": {"current": 61_204, "target": 100_000},
        # ⚠️ A band, never a point — an eleven-day projection stated to the hour
        # is false precision somebody will plan around.
        "projection": {
            "measured": True,
            "earliest": (top + 44 * HOUR) * 1000,
            "latest": (top + 68 * HOUR) * 1000,
            "hits_per_hour": 242.5,
        },
        "hours": [
            hour(0, [w(3538517, "THARAGINCAJIN"), w(3228747, "-Jesse-")]),
            hour(1, [w(3538517, "THARAGINCAJIN")]),                         # one of two
            hour(2, [w(1712182, "Muttley"), w(873341, "Goosey")]),
            hour(3, []),                                                     # nobody at all
            hour(4, [w(3136837, "Sanityloss")]),
            # ⚠️ In the air. The dashboard hands over WHEN THEY LEFT and WHICH
            # PLANE, not an arrival — Torn publishes no arrival time for a
            # traveller, so every ETA is counted from a departure somebody had
            # to witness. The bot turns this into a band with flight.py.
            hour(5, [w(2632966, "CalliBee", travel={
                "state": "Traveling", "destination": "South Africa",
                "plane_image_type": "airliner",
                "departed_at": (top + 5 * HOUR - 20 * 60) * 1000,
            }), w(3383262, "Zyuunii")], bonus=True),
            hour(6, [w(3680626, "Chef_Tris")], bonus=True),
            hour(7, [w(2478585, "Gekents"), w(2719034, "xAnri")]),
            hour(8, []),
            hour(9, [w(3015831, "Warwinds"), w(3130493, "DarthCheetoe")]),
        ],
    }


def mock_shift_ping(now_ts: int) -> Dict:
    """One member, one shift about to start."""
    top = now_ts - (now_ts % HOUR)
    return {
        "member_id": "873341", "name": "Goosey",
        "hour_start": (top + 1 * HOUR) * 1000,
        "bonus": False,
        "partner": {"member_id": "1712182", "name": "Muttley"},
        "chain": {"current": 61_204, "target": 100_000},
        "travel": None,
    }


def mock_shift_ping_flying(now_ts: int) -> Dict:
    """The same ping for somebody who is in the air and may not land in time."""
    top = now_ts - (now_ts % HOUR)
    return {
        **mock_shift_ping(now_ts),
        "member_id": "2632966", "name": "CalliBee",
        "hour_start": (top + 2 * HOUR) * 1000,
        # ⚠️ Raw again — and `departed_at` may legitimately be None when the
        # sampler did not witness the take-off. The formatter has to render
        # that case, because "arrival unknown" is the honest answer and a
        # fabricated one is what a lead would wait on.
        "travel": {
            "state": "Traveling", "destination": "South Africa",
            "plane_image_type": "airliner",
            "departed_at": (top + 2 * HOUR - 15 * 60) * 1000,
        },
    }
