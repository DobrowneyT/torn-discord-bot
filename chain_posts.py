"""
What the bot has posted, remembered across restarts (#785 follow-up).

Two problems, one store.

⚠️ **The board used to be re-posted on every restart.** Its message id lived in
a dict in memory, so a redeploy — which happens most while the thing is being
tuned — left a dead board above a new one, and then another, until the channel
was a column of abandoned boards. The OC watcher has persisted its message id
since day one; this simply did not.

⚠️ **Gap pings outlive what they are about.** "03:00 needs 2 slots" is a fact
with a shelf life: somebody fills it, or the hour passes. Leaving it sitting
there means the channel accumulates claims that are no longer true, and a reader
cannot tell which still are.
"""

import logging
import time
from typing import Dict, List, Optional

import state

log = logging.getLogger("chain_posts")

BOARD_KEY = "chain_board_messages"
POSTS_KEY = "chain_posts"


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── the standing board ───────────────────────────────────────────────────────

def board_message(slug: str) -> Optional[int]:
    value = (state.load_state().get(BOARD_KEY, {}) or {}).get(slug)
    return int(value) if value else None


def set_board_message(slug: str, message_id: Optional[int]) -> None:
    st = state.load_state()
    board = st.setdefault(BOARD_KEY, {})
    if message_id:
        board[slug] = int(message_id)
    else:
        board.pop(slug, None)
    state.save_state(st)


# ── pings ────────────────────────────────────────────────────────────────────

def _posts() -> Dict[str, List[Dict]]:
    return state.load_state().get(POSTS_KEY, {}) or {}


def _save(posts: Dict[str, List[Dict]]) -> None:
    st = state.load_state()
    st[POSTS_KEY] = posts
    state.save_state(st)


def record(slug: str, *, kind: str, message_id: int, channel_id: int,
           hours: List[int], content: str = "") -> None:
    """
    Remember a ping so it can be revised or removed later.

    `hours` is every hour the message speaks about — a gap ping covers several
    since they were batched, and all of them have to be resolved before the
    message stops being true.
    """
    posts = _posts()
    posts.setdefault(slug, []).append({
        "kind": kind,
        "message_id": int(message_id),
        "channel_id": int(channel_id),
        "hours": [int(h) for h in hours],
        "content": content,
        "at": _now_ms(),
    })
    _save(posts)


def posts_for(slug: str, kind: Optional[str] = None) -> List[Dict]:
    items = list(_posts().get(slug, []))
    return [p for p in items if kind is None or p.get("kind") == kind]


def update(slug: str, message_id: int, content: str) -> None:
    posts = _posts()
    for p in posts.get(slug, []):
        if p["message_id"] == int(message_id):
            p["content"] = content
            _save(posts)
            return


def forget(slug: str, message_id: int) -> None:
    posts = _posts()
    kept = [p for p in posts.get(slug, []) if p["message_id"] != int(message_id)]
    if len(kept) != len(posts.get(slug, [])):
        posts[slug] = kept
        if not kept:
            posts.pop(slug)
        _save(posts)


def forget_all(slug: str, keep_kinds: tuple = ()) -> List[Dict]:
    """
    Drop a faction's tracked pings, returning them so they can be deleted.

    ⚠️ `keep_kinds` stays tracked AND stays posted. Flight warnings use it:
    they are the record of why a slot went uncovered, and payout review happens
    after the chain has ended, so sweeping them at event end would delete the
    evidence exactly when somebody goes looking for it.
    """
    posts = _posts()
    items = posts.get(slug, [])
    kept = [p for p in items if p.get("kind") in keep_kinds]
    gone = [p for p in items if p.get("kind") not in keep_kinds]
    if gone:
        if kept:
            posts[slug] = kept
        else:
            posts.pop(slug, None)
        _save(posts)
    return gone
