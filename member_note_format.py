"""
Membership notes — somebody joined, somebody left (#942).

⚠️ **These could never post.** The drain was written for #943's war reports and
refused anything without a `war_id` in its payload, so every `joined` and `left`
note failed on every tick for ever — the whole of #942, invisible. Found when a
new member joined forge and his thread never appeared.

⚠️ **A note is a LOG LINE, not a report.** One sentence, no embed. A member's
thread is a running log whose entries are mostly war reports; a join note that
looked like one would compete with them for attention while saying far less.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

#: The outbox kinds this module renders. ⚠️ Must match `OUTBOX_KINDS` in
#: `server/src/memberThreads.ts`; a kind in one and not the other is a note
#: that queues and never posts, which is exactly how #942 went unnoticed.
JOINED = "joined"
LEFT = "left"
KINDS = (JOINED, LEFT)


def _when(payload: Dict[str, Any], queued_ms: Optional[Any]) -> Optional[int]:
    """
    When it actually happened, epoch SECONDS, or None.

    ⚠️ `at` is the event itself. `queued_ms` is when the sweep noticed, which
    is its five-minute cadence at best and arbitrarily late after a restart —
    a fallback for notes queued before the dashboard sent `at`, never a
    preference.
    """
    for value, divisor in ((payload.get("at"), 1000), (queued_ms, 1000)):
        try:
            if value is not None:
                return int(int(value) / divisor)
        except (TypeError, ValueError):
            continue
    return None


def note_line(kind: str, payload: Dict[str, Any], queued_ms: Optional[Any] = None,
              member_id: Optional[Any] = None) -> Optional[str]:
    """
    One line for a membership event, or None for a kind we do not render.

    ⚠️ Returns None rather than inventing text for an unknown kind. A note
    nobody designed, posted into somebody's permanent record, is worse than one
    that fails loudly and gets noticed.
    """
    if kind not in KINDS:
        return None

    name = payload.get("member_name") or (f"id {member_id}" if member_id else "A member")
    verb = "joined the faction" if kind == JOINED else "left the faction"
    icon = "📥" if kind == JOINED else "📤"

    when = _when(payload, queued_ms)
    if when is None:
        # ⚠️ No date rather than today's. A permanent log entry dated wrongly is
        # worse than one that simply does not say.
        return f"{icon} **{name}** {verb}."
    # ⚠️ Discord's own timestamp, so it renders in each reader's timezone. A
    # UTC string would be read as local by somebody and be hours out.
    return f"{icon} **{name}** {verb} — <t:{when}:f>"


def thread_title(payload: Dict[str, Any], member_id: Any) -> str:
    """
    The thread name for a member who does not have one yet.

    ⚠️ Carries the id as well as the name: two members have shared a display
    name, and Torn names change. It is what somebody searches the forum for.
    """
    name = payload.get("member_name") or str(member_id)
    return f"{name} [{member_id}]"[:100]
