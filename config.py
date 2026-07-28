"""
OC Watcher bot configuration.

CPR_REQUIREMENTS mirrors `cprRequirements` from src/config.js (lines 56-103).
Keep both in sync if thresholds change.
"""

POLL_INTERVAL_SECONDS = 60 * 5
WINDOW_MISSING_ITEMS_HOURS = 24
WINDOW_UNAVAILABLE_HOURS = 6

# Fallback CPR threshold applied when a crime or position is not listed in
# CPR_REQUIREMENTS below. Used for any role we haven't explicitly tuned.
DEFAULT_CPR_THRESHOLD = 75

# Statuses that mean the crime is still in flight (not yet executed/expired).
ACTIVE_CRIME_STATUSES = {"Recruiting", "Planning"}

# Member states that count as "unavailable" for a soon-to-execute crime.
UNAVAILABLE_STATES = {"Hospital", "Jail", "Abroad", "Traveling", "Federal"}

CRIME_URL_TEMPLATE = "https://www.torn.com/factions.php?step=your#/tab=crimes&crimeId={crime_id}"

CPR_REQUIREMENTS = {
    "Blast from the Past": {
        "difficulty": 7,
        "positions": {
            "Bomber": 75, "Engineer": 75, "Hacker": 70,
            "Muscle": 75, "Picklock #1": 70, "Picklock #2": 50,
        },
    },
    "Break the Bank": {
        "difficulty": 8,
        "positions": {
            "Robber": 60, "Thief #1": 50, "Thief #2": 65,
            "Muscle #1": 60, "Muscle #2": 60, "Muscle #3": 65,
        },
    },
    "Stacking the Deck": {
        "difficulty": 8,
        "positions": {
            "Cat Burglar": 68, "Driver": 50, "Imitator": 68, "Hacker": 68,
        },
    },
    "Ace in the Hole": {
        "difficulty": 8,
        "positions": {
            "Hacker": 63, "Driver": 53, "Imitator": 63,
            "Muscle #1": 63, "Muscle #2": 63,
        },
    },
    "Clinical Precision": {
        "difficulty": 8,
        "positions": {
            "Cat Burglar": 67, "Cleaner": 67, "Imitator": 70, "Assassin": 67,
        },
    },
    "Bidding War": {
        "difficulty": 6,
        "positions": {
            "Driver": 75, "Robber #1": 70, "Robber #2": 75,
            "Robber #3": 75, "Bomber #1": 70, "Bomber #2": 75,
        },
    },
    "Honey Trap": {
        "difficulty": 5,
        "positions": {
            "Muscle #1": 75, "Muscle #2": 75, "Enforcer": 75,
        },
    },
}
