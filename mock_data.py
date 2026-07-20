"""
Hand-crafted mock crime payloads for Phase 1 (formatter iteration).

Shape matches /v2/faction/crimes — including position_info.label — but with
slot.user.name and slot.item_requirement.name pre-populated as if enrich.py
had already run. format_preview.py feeds these straight to alerts.build_alerts.
"""

from typing import Dict, List


def mock_crimes(now_ts: int) -> List[Dict]:
    """Return a list of crime dicts covering all three alert types plus a clean crime."""
    HOUR = 3600

    return [
        # 1) Executes in 3h: one member hospitalised + one missing item.
        {
            "id": 800001,
            "name": "Bidding War",
            "difficulty": 6,
            "status": "Planning",
            "ready_at": now_ts + 3 * HOUR,
            "executed_at": None,
            "expired_at": None,
            "slots": [
                _slot("Driver", "Driver",      1, 1234567, "MonChoon",        88, item_ok=True),
                _slot("Robber", "Robber #1",   1, 2345678, "PicklockPete",    78, item_ok=False, item_name="Lock Pick"),
                _slot("Robber", "Robber #2",   2, 3456789, "DiabolicPickles", 90, item_ok=True),
                _slot("Robber", "Robber #3",   3, 4567890, "CleverGirl",      82, item_ok=True),
                _slot("Bomber", "Bomber #1",   1, 5678901, "Marnix0126",      73, item_ok=True),
                _slot("Bomber", "Bomber #2",   2, 6789012, "Andrew_616",      80, item_ok=True),
            ],
        },

        # 2) Executes in 18h: two members missing items, no other issues.
        {
            "id": 800002,
            "name": "Clinical Precision",
            "difficulty": 8,
            "status": "Planning",
            "ready_at": now_ts + 18 * HOUR,
            "executed_at": None,
            "expired_at": None,
            "slots": [
                _slot("Cat Burglar", "Cat Burglar", 1, 1111111, "ShadowFox",  70, item_ok=False, item_name="Climbing Hooks"),
                _slot("Cleaner",     "Cleaner",     1, 2222222, "DustBunny",  72, item_ok=True),
                _slot("Imitator",    "Imitator",    1, 3333333, "MimicMan",   75, item_ok=False, item_name="Latex Mask"),
                _slot("Assassin",    "Assassin",    1, 4444444, "QuietExit",  68, item_ok=True),
            ],
        },

        # 3) Executes in 2 days: one member below CPR threshold (Bomber needs 75, has 60).
        {
            "id": 800003,
            "name": "Blast from the Past",
            "difficulty": 7,
            "status": "Planning",
            "ready_at": now_ts + 48 * HOUR,
            "executed_at": None,
            "expired_at": None,
            "slots": [
                _slot("Bomber",   "Bomber",      1, 5555555, "BoomerLow",  60, item_ok=True),
                _slot("Engineer", "Engineer",    1, 6666666, "GearHead",   80, item_ok=True),
                _slot("Hacker",   "Hacker",      1, 7777777, "Cipher",     72, item_ok=True),
                _slot("Muscle",   "Muscle",      1, 8888888, "BigBob",     78, item_ok=True),
                _slot("Picklock", "Picklock #1", 1, 9999999, "TumblerOne", 71, item_ok=True),
                _slot("Picklock", "Picklock #2", 2, 1010101, "TumblerTwo", 70, item_ok=True),
            ],
        },

        # 4) Clean crime — should NOT appear in the message.
        {
            "id": 800004,
            "name": "Honey Trap",
            "difficulty": 6,
            "status": "Planning",
            "ready_at": now_ts + 12 * HOUR,
            "executed_at": None,
            "expired_at": None,
            "slots": [
                _slot("Muscle",   "Muscle",   1, 1212121, "Hercules",   80, item_ok=True),
                _slot("Enforcer", "Enforcer", 1, 1313131, "TheCloser",  76, item_ok=True),
            ],
        },
    ]


def mock_member_status(now_ts: int) -> Dict[int, Dict]:
    """user_id -> {state, until} for the unavailable mock members."""
    return {
        1234567: {"state": "Hospital", "until": now_ts + 90 * 60},
    }


def _slot(position: str, label: str, number: int,
          user_id: int, name: str, cpr: float,
          item_ok: bool, item_name: str = "Required Item") -> Dict:
    return {
        "position": position,
        "position_info": {"id": f"P{number}", "label": label, "number": number},
        "position_id": f"P{number}",
        "position_number": number,
        "checkpoint_pass_rate": cpr,
        "user": {
            "id": user_id,
            "name": name,
            "joined_at": 0,
            "outcome": None,
            "progress": 100,
            "item_outcome": None,
        },
        "item_requirement": {
            "id": 0,
            "name": item_name,
            "is_reusable": False,
            "is_available": item_ok,
        },
    }
