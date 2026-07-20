"""
One-off smoke test: feed a real API sample (subset) through the
enrich + alerts pipeline to confirm everything fits together.

This stays in scratch/ as a fixture; safe to delete once Phase 3 lands.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import alerts as alerts_mod
import enrich

# Excerpt of the real /v2/faction/crimes?cat=available response (4 crimes).
RAW_CRIMES = [
    {
        "id": 1604805, "name": "Bidding War", "difficulty": 6, "status": "Recruiting",
        "ready_at": 1778140569, "executed_at": None, "expired_at": 1778572450,
        "slots": [
            {"position": "Robber", "position_info": {"id": "P1", "label": "Robber #1", "number": 1},
             "item_requirement": {"id": 568, "is_reusable": True, "is_available": True},
             "user": {"id": 2864154, "joined_at": 1, "outcome": None, "progress": 75, "item_outcome": None},
             "checkpoint_pass_rate": 78},
            {"position": "Driver", "position_info": {"id": "P2", "label": "Driver", "number": 1},
             "item_requirement": None, "user": None, "checkpoint_pass_rate": 87},
            {"position": "Robber", "position_info": {"id": "P3", "label": "Robber #2", "number": 2},
             "item_requirement": {"id": 222, "is_reusable": False, "is_available": False},
             "user": None, "checkpoint_pass_rate": 89},
            {"position": "Bomber", "position_info": {"id": "P5", "label": "Bomber #1", "number": 1},
             "item_requirement": {"id": 190, "is_reusable": False, "is_available": True},
             "user": {"id": 3291056, "joined_at": 1, "outcome": None, "progress": 0, "item_outcome": None},
             "checkpoint_pass_rate": 75},
        ],
    },
    {
        "id": 1606121, "name": "Break the Bank", "difficulty": 8, "status": "Recruiting",
        "ready_at": 1778100672, "executed_at": None, "expired_at": 1778601652,
        "slots": [
            {"position": "Muscle", "position_info": {"id": "P2", "label": "Muscle #1", "number": 1},
             "item_requirement": {"id": 1331, "is_reusable": True, "is_available": True},
             "user": {"id": 2253786, "joined_at": 1, "outcome": None, "progress": 21, "item_outcome": None},
             "checkpoint_pass_rate": 70},
        ],
    },
    {
        "id": 1586710, "name": "Break the Bank", "difficulty": 8, "status": "Planning",
        "ready_at": 1778175259, "executed_at": None, "expired_at": 1778177990,
        "slots": [
            {"position": "Robber", "position_info": {"id": "P1", "label": "Robber", "number": 1},
             "item_requirement": {"id": 1331, "is_reusable": True, "is_available": True},
             "user": {"id": 3012215, "joined_at": 1, "outcome": None, "progress": 100, "item_outcome": None},
             "checkpoint_pass_rate": 67},
            {"position": "Muscle", "position_info": {"id": "P2", "label": "Muscle #1", "number": 1},
             "item_requirement": {"id": 1331, "is_reusable": True, "is_available": True},
             "user": {"id": 2926373, "joined_at": 1, "outcome": None, "progress": 100, "item_outcome": None},
             "checkpoint_pass_rate": 61},
            {"position": "Thief", "position_info": {"id": "P4", "label": "Thief #1", "number": 1},
             "item_requirement": {"id": 1331, "is_reusable": True, "is_available": True},
             "user": {"id": 3015831, "joined_at": 1, "outcome": None, "progress": 100, "item_outcome": None},
             "checkpoint_pass_rate": 62},
            {"position": "Muscle", "position_info": {"id": "P5", "label": "Muscle #3", "number": 3},
             "item_requirement": {"id": 1331, "is_reusable": True, "is_available": True},
             "user": {"id": 2407528, "joined_at": 1, "outcome": None, "progress": 100, "item_outcome": None},
             "checkpoint_pass_rate": 72},
        ],
    },
    {
        "id": 1586711, "name": "Window of Opportunity", "difficulty": 7, "status": "Planning",
        "ready_at": 1778284201, "executed_at": None, "expired_at": 1778177992,
        "slots": [
            {"position": "Looter", "position_info": {"id": "P3", "label": "Looter #2", "number": 2},
             "item_requirement": {"id": 1509, "is_reusable": True, "is_available": False},
             "user": {"id": 3383262, "joined_at": 1, "outcome": None, "progress": 9, "item_outcome": None},
             "checkpoint_pass_rate": 69},
        ],
    },
]

MOCK_MEMBERS_RESP = {
    "members": [
        {"id": 2864154, "name": "RobberOne",   "status": {"state": "Okay", "until": 0}},
        {"id": 3291056, "name": "BomberOne",   "status": {"state": "Hospital", "until": 1778160000 + 7200}},
        {"id": 2253786, "name": "MuscleOne",   "status": {"state": "Jail", "until": 0}},
        {"id": 3012215, "name": "RobberPrime", "status": {"state": "Okay", "until": 0}},
        {"id": 2926373, "name": "MuscleAlpha", "status": {"state": "Okay", "until": 0}},
        {"id": 3015831, "name": "ThiefAlpha",  "status": {"state": "Okay", "until": 0}},
        {"id": 2407528, "name": "DiabolicPickles", "status": {"state": "Okay", "until": 0}},
        {"id": 3383262, "name": "LooterTwo",   "status": {"state": "Okay", "until": 0}},
    ]
}

MOCK_ITEM_NAMES = {
    568: "Lock Pick", 222: "Smoke Grenade", 190: "Dynamite",
    1331: "Hammer", 1509: "Crowbar",
}


def main() -> None:
    now_ts = 1778160000  # mid-window so the time filters fire interestingly

    member_lookup = enrich.build_member_lookup(MOCK_MEMBERS_RESP)
    status_map = enrich.build_status_map(MOCK_MEMBERS_RESP)
    item_ids = enrich.collect_item_ids(RAW_CRIMES)
    item_names = {i: MOCK_ITEM_NAMES.get(i, f"item_{i}") for i in item_ids}

    enriched = enrich.enrich_crimes(RAW_CRIMES, member_lookup, item_names)
    out = alerts_mod.build_alerts(enriched, status_map, now_ts)

    print("STATUS MAP:", status_map)
    print("ITEM NAMES:", item_names)
    print()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
