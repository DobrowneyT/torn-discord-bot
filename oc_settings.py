"""
OC board settings, per faction — `/oc thread` (#1019).

⚠️ **Its own store**, beside `chain_settings` and `rw_settings`, for the same
reason those two are apart: `/chain settings` is Chain Watch's list, and a
setting people cannot find is one they assume does not exist.

The OC board used to be ONE thread for ONE faction, named by the
`DISCORD_CHANNEL_ID` environment variable. `adopt_legacy` carries that thread,
its message and its red-alert memory onto the faction it served, once, so the
board in Discord — and the override button on it — keep working unchanged.
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple

import chain_tenants
import state
from chain_settings import Setting

log = logging.getLogger("oc_settings")

STATE_KEY = "oc_settings"
#: Per-faction board message ids, beside the settings.
BOARD_KEY = "oc_board_messages"
#: Per-faction red alerts already announced (see oc_runtime).
NOTIFIED_KEY = "oc_notified_red"

#: Which faction the pre-#1019 single board belonged to.
LEGACY_SLUG_ENV = "OC_WATCH_LEGACY_SLUG"
LEGACY_SLUG_DEFAULT = "forge"

SETTINGS: Dict[str, Setting] = {
    s.key: s for s in [
        Setting("oc_thread_id", 0, "channel",
                "Channel or thread holding this faction's OC board. Unset means "
                "no OC board for this faction."),
    ]
}


def _store() -> Dict[str, Dict[str, Any]]:
    raw = state.load_state().get(STATE_KEY) or {}
    return raw if isinstance(raw, dict) else {}


def get(slug: str, key: str) -> Any:
    s = SETTINGS[key]
    return _store().get(slug, {}).get(key, s.default)


def set_value(slug: str, key: str, raw: Any) -> Tuple[bool, str]:
    s = SETTINGS.get(key)
    if s is None:
        return False, f"No setting called `{key}`."
    value, err = s.coerce(raw)
    if err:
        return False, err
    st = state.load_state()
    st.setdefault(STATE_KEY, {}).setdefault(slug, {})[key] = value
    # A new thread means a new board message there.
    if key == "oc_thread_id":
        (st.get(BOARD_KEY) or {}).pop(slug, None)
    state.save_state(st)
    log.info("oc setting %s/%s = %r", slug, key, value)
    return True, f"`{key}` is now **{value}** for `{slug}`."


def board_message(slug: str) -> Optional[int]:
    v = (state.load_state().get(BOARD_KEY) or {}).get(slug)
    return int(v) if v else None


def set_board_message(slug: str, message_id: Optional[int]) -> None:
    st = state.load_state()
    board = st.setdefault(BOARD_KEY, {})
    if message_id:
        board[slug] = int(message_id)
    else:
        board.pop(slug, None)
    state.save_state(st)


def adopt_legacy() -> Optional[str]:
    """
    Move the old single board onto its faction. Returns the slug, or None.

    ⚠️ Only when NO faction has a thread yet — after that, the settings are the
    truth and the environment variable is history. And only onto a faction the
    bot actually serves, so a typo cannot invent one.
    """
    channel = os.environ.get("DISCORD_CHANNEL_ID")
    if not channel:
        return None
    store = _store()
    if any(v.get("oc_thread_id") for v in store.values() if isinstance(v, dict)):
        return None
    slug = os.environ.get(LEGACY_SLUG_ENV) or LEGACY_SLUG_DEFAULT
    if chain_tenants.get(slug) is None:
        return None
    st = state.load_state()
    st.setdefault(STATE_KEY, {}).setdefault(slug, {})["oc_thread_id"] = int(channel)
    # ⚠️ The existing message, so the board edits in place and the override
    # button already posted on it keeps routing.
    if st.get("message_id"):
        st.setdefault(BOARD_KEY, {})[slug] = int(st["message_id"])
    if st.get("notified_red_alerts"):
        st.setdefault(NOTIFIED_KEY, {})[slug] = st["notified_red_alerts"]
    state.save_state(st)
    log.info("adopted the legacy OC board (channel %s) for %s", channel, slug)
    return slug
