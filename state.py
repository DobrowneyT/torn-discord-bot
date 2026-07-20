"""
Persist the bot's state (message id + low-CPR overrides) to discord/state.json.

The override store is the per-(crime_id, user_id, position) whitelist that
suppresses low_cpr alerts. Overrides are pruned each tick to drop entries
whose crime_id is no longer in the active /faction/crimes list.
"""

import json
import logging
import os
from typing import Dict, Iterable, List, Optional, Set, Tuple

STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")

OverrideKey = Tuple[int, int, str]

log = logging.getLogger("state")


def load_state() -> dict:
    """Load discord/state.json.

    Tolerates the file being missing or empty (returns ``{}``).
    If it exists with non-empty but malformed contents, the file is
    moved aside to ``state.json.broken`` and we start fresh — losing
    a corrupt state file silently is worse than overwriting it.
    """
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r") as f:
            text = f.read()
    except OSError as e:
        log.warning("state.json unreadable (%s) — treating as empty", e)
        return {}
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        backup = STATE_PATH + ".broken"
        try:
            os.replace(STATE_PATH, backup)
            log.error(
                "state.json was not valid JSON (%s) — moved to %s and starting fresh",
                e, backup,
            )
        except OSError as move_err:
            log.error(
                "state.json was not valid JSON (%s) and could not be backed up (%s)",
                e, move_err,
            )
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def get_message_id() -> Optional[int]:
    return load_state().get("message_id")


def set_message_id(message_id: int) -> None:
    state = load_state()
    state["message_id"] = int(message_id)
    save_state(state)


# (crime_id, user_id, position, alert_type) — see formatter.RedAlertKey.
RedAlertKey = Tuple[int, int, str, str]


def get_notified_red_alerts() -> Set[RedAlertKey]:
    """The set of red alerts we've already sent a Strategy C notification for."""
    raw = load_state().get("notified_red_alerts", [])
    out: Set[RedAlertKey] = set()
    for a in raw:
        try:
            out.add((int(a["crime_id"]), int(a["user_id"]), a["position"], a["alert_type"]))
        except (KeyError, TypeError, ValueError):
            log.warning("dropping malformed notified_red_alert entry: %r", a)
    return out


def set_notified_red_alerts(keys: Set[RedAlertKey]) -> None:
    state = load_state()
    state["notified_red_alerts"] = [
        {"crime_id": cid, "user_id": uid, "position": pos, "alert_type": atype}
        for (cid, uid, pos, atype) in sorted(keys)
    ]
    save_state(state)


class CprOverrideStore:
    """
    In-memory cache of low-CPR overrides, persisted to state.json on every
    mutation. One instance lives for the bot's lifetime; views and the alerts
    pipeline share it.

    Each entry is a dict so we can carry display metadata (crime_name,
    user_name) for the management UI without re-running enrichment.
    """

    def __init__(self):
        state = load_state()
        self._items: List[Dict] = list(state.get("cpr_overrides", []))

    def list(self) -> List[Dict]:
        return list(self._items)

    def keys(self) -> Set[OverrideKey]:
        return {(o["crime_id"], o["user_id"], o["position"]) for o in self._items}

    def has(self, crime_id: int, user_id: int, position: str) -> bool:
        key = (int(crime_id), int(user_id), position)
        return any(self._key_of(o) == key for o in self._items)

    def add(self, crime_id: int, user_id: int, position: str,
            *, crime_name: str = "", user_name: str = "") -> bool:
        if self.has(crime_id, user_id, position):
            return False
        self._items.append({
            "crime_id": int(crime_id),
            "user_id": int(user_id),
            "position": position,
            "crime_name": crime_name,
            "user_name": user_name,
        })
        self._save()
        log.info("approved CPR override: crime=%s user=%s position=%s", crime_id, user_id, position)
        return True

    def remove(self, crime_id: int, user_id: int, position: str) -> bool:
        key = (int(crime_id), int(user_id), position)
        before = len(self._items)
        self._items = [o for o in self._items if self._key_of(o) != key]
        if len(self._items) != before:
            self._save()
            log.info("removed CPR override: crime=%s user=%s position=%s", crime_id, user_id, position)
            return True
        return False

    def prune(self, active_crime_ids: Iterable[int]) -> int:
        """Drop overrides whose crime_id is no longer active. Returns number dropped."""
        active = {int(i) for i in active_crime_ids}
        before = len(self._items)
        self._items = [o for o in self._items if o["crime_id"] in active]
        dropped = before - len(self._items)
        if dropped:
            self._save()
            log.info("pruned %d stale CPR override(s)", dropped)
        return dropped

    def _save(self) -> None:
        state = load_state()
        state["cpr_overrides"] = self._items
        save_state(state)

    @staticmethod
    def _key_of(o: Dict) -> OverrideKey:
        return (o["crime_id"], o["user_id"], o["position"])
