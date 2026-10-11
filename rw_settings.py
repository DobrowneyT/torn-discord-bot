"""
Ranked-war settings, per faction — the `/rw config` half (#944).

⚠️ **A separate store from `chain_settings`, deliberately.** These are not Chain
Watch tunables and must not appear in `/chain settings`: that command already
carries eleven entries and ends with a footer explaining which half lives on the
dashboard. Adding war-report settings to it would make one command the place to
configure two unrelated features, and the one people cannot find is the one in
the longer list.

⚠️ **The `Setting` machinery is reused rather than reimplemented.** Coercion and
bound-checking are exactly the same job; what differs is which keys exist and
where they are stored. A second copy of `coerce` is how `on`/`off` comes to mean
different things in two commands.
"""

import logging
from typing import Any, Dict, Tuple

import state
from chain_settings import Setting

log = logging.getLogger("rw_settings")

#: Its own block in state.json, beside `chain_settings`.
STATE_KEY = "rw_settings"

SETTINGS: Dict[str, Setting] = {
    s.key: s for s in [
        # ⚠️ **Unset is a refusal, not a default.** There is no sensible
        # fallback: posting a member's permanent performance record into the
        # board channel, or the first forum the bot can see, would publish
        # roll-call and dibs data to an audience nobody chose. Member reports
        # do not run until this is set.
        Setting("member_thread_forum_id", 0, "channel",
                "Forum channel holding one thread per member for post-war reports. "
                "Unset means member reports do not post at all — there is no fallback."),
    ]
}


def _store() -> Dict[str, Dict[str, Any]]:
    block = state.load_state().get(STATE_KEY)
    return block if isinstance(block, dict) else {}


def _for(slug: str) -> Dict[str, Any]:
    return _store().get(slug, {}) or {}


def _save(store: Dict[str, Dict[str, Any]]) -> None:
    st = state.load_state()
    st[STATE_KEY] = store
    state.save_state(st)


def all_settings(slug: str) -> Dict[str, Any]:
    stored = _for(slug)
    return {k: stored.get(k, s.default) for k, s in SETTINGS.items()}


def get(slug: str, key: str) -> Any:
    s = SETTINGS.get(key)
    if s is None:
        raise KeyError(key)
    return _for(slug).get(key, s.default)


def set_value(slug: str, key: str, raw: Any) -> Tuple[bool, str]:
    s = SETTINGS.get(key)
    if s is None:
        return False, f"No setting called `{key}`. Try `/rw config`."
    value, err = s.coerce(raw)
    if err:
        return False, err
    store = _store()
    store.setdefault(slug, {})[key] = value
    _save(store)
    log.info("rw setting %s/%s = %r", slug, key, value)
    return True, f"`{key}` is now **{value}** for `{slug}`."


def reset(slug: str, key: str) -> Tuple[bool, str]:
    s = SETTINGS.get(key)
    if s is None:
        return False, f"No setting called `{key}`."
    store = _store()
    store.get(slug, {}).pop(key, None)
    _save(store)
    return True, f"`{key}` is back to its default (**{s.default}**) for `{slug}`."


def adopt_from_chain(slug: str) -> bool:
    """
    Take over a value that was briefly set through `/chain set`.

    ⚠️ `member_thread_forum_id` shipped in `chain_settings` for about an hour
    before moving here (#943 → #944). Anyone who set it in that window would
    otherwise find member reports silently off, with the forum still visible in
    `/chain settings` — the worst shape, because the setting LOOKS right.

    Returns True when something was adopted. Safe to call every time.
    """
    import chain_settings

    # ⚠️ **RAW state, not `chain_settings._store()`.** That accessor ends with
    # `if k in SETTINGS`, so the moment this key left the chain registry it
    # became invisible there — an adoption reading through it finds nothing and
    # reports success at having done nothing. Found by the test, not by me.
    #
    # ⚠️ And it is a RACE worth winning: the same filter means the next
    # `/chain set` writes the filtered store back and destroys the value for
    # good. `forum_id()` calls this on every tick so the window is small, but
    # the loss would be silent and permanent.
    st = state.load_state()
    chain_block = st.get(chain_settings.STATE_KEY)
    if not isinstance(chain_block, dict):
        return False
    for_slug = chain_block.get(slug)
    if not isinstance(for_slug, dict):
        return False
    stale = for_slug.get("member_thread_forum_id")
    if stale in (None, 0):
        return False

    store = _store()
    # ⚠️ Does not overwrite a value set here. If both exist, the one set through
    # the command that still exists is the one somebody meant.
    if (store.get(slug) or {}).get("member_thread_forum_id"):
        # Still clear the stale copy, so two places do not disagree.
        for_slug.pop("member_thread_forum_id", None)
        state.save_state(st)
        return False

    store.setdefault(slug, {})["member_thread_forum_id"] = stale
    for_slug.pop("member_thread_forum_id", None)
    # ⚠️ One save, carrying both blocks. Two saves would leave a window where
    # the value exists in neither place or in both.
    st[STATE_KEY] = store
    state.save_state(st)
    log.info("adopted member_thread_forum_id %s from chain settings for %s", stale, slug)
    return True
