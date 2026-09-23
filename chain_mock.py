"""
Hand-crafted Chain Watch payloads for Phase 1 (formatter iteration).

Shape matches what the dashboard's /api/internal/chain-watch will return, so
chain_preview.py can feed these straight to chain_alerts.build_watch — and
Phase 3 can swap the source without touching the formatter.

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
        return {"hour_start": (top + offset * HOUR) * 1000, "bonus": bonus, "watchers": watchers}

    def w(member_id: int, name: str, **extra) -> Dict:
        return {"member_id": member_id, "name": name, **extra}

    return {
        "event": {
            "title": "Halloween 100k Chain",
            "slots_per_hour": 2,
            "ends_at": (top + 52 * HOUR) * 1000,
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
            # ⚠️ In the air, and the band says they may not land in time. This is
            # the warning worth sending EARLY — five minutes' notice is useless
            # to someone over the Atlantic.
            hour(5, [w(2632966, "CalliBee", travel={
                "state": "Traveling", "destination": "South Africa",
                "eta_earliest": (top + 5 * HOUR + 40 * 60) * 1000,
                "eta_latest": (top + 5 * HOUR + 55 * 60) * 1000,
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
        "member_id": 873341, "name": "Goosey",
        "hour_start": (top + 1 * HOUR) * 1000,
        "bonus": False,
        "partner": {"member_id": 1712182, "name": "Muttley"},
        "chain": {"current": 61_204, "target": 100_000},
        "travel": None,
    }


def mock_shift_ping_flying(now_ts: int) -> Dict:
    """The same ping for somebody who is in the air and may not land in time."""
    top = now_ts - (now_ts % HOUR)
    return {
        **mock_shift_ping(now_ts),
        "member_id": 2632966, "name": "CalliBee",
        "hour_start": (top + 2 * HOUR) * 1000,
        "travel": {
            "state": "Traveling", "destination": "South Africa",
            "eta_earliest": (top + 2 * HOUR + 25 * 60) * 1000,
            "eta_latest": (top + 2 * HOUR + 40 * 60) * 1000,
        },
    }
