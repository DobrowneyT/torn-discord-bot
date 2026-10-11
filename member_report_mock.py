"""
Hand-crafted payload for the post-war member report (planning mock).

⚠️ **This is a MOCK and must be deleted or wired to the real endpoint before
anything ships.** `chain_mock` carries the same warning for the same reason: a
mock that has drifted from its endpoint is worse than none, because it renders,
it looks right, and it proves nothing.

The numbers below are the SHAPE the three real sources already return, so the
formatter can be judged before any of them is joined up:

  * war figures   — `/api/internal/war-overview` with `member=<id>`, which
                    `/rw overview` already uses (war_overview_api.py)
  * dibs          — `war_dibs_summary`, served today at
                    `/api/wars/:id/dibs-summary` (dashboard #530/#935)
  * roll call     — `war_room_roll_call_v2`, served at
                    `/api/war-room/roll-call?war_id=`

⚠️ Only the first of those is reachable by the bot today. The other two are
session-authed, not bearer-token internal routes — see the plan: one internal
endpoint should carry all three rather than the bot learning three auth styles.
"""

MEMBER_REPORT = {
    "faction": {"slug": "forge", "name": "Forge"},
    "war": {
        "id": 1842,
        "opponent_name": "Romania Elite Kommando",
        "opponent_id": 18569,
        "start_at": 1_791_244_800,
        "end_at": 1_791_504_000,
        "our_score": 5412,
        "opponent_score": 4877,
        "is_termed": True,
        "won": True,
    },
    "member": {"id": 873341, "name": "Goosey"},

    # ⚠️ Both figures from OUR attack ledger, not one from each source. Torn's
    # war report and `faction_attacks` are different ledgers and a sync gap puts
    # them out of step — showing "raw" from one and "normalised" from the other
    # would print a pair that cannot be subtracted from each other.
    #   raw        — respect as Torn awarded it, bonus hits and all
    #   normalised — bonus-chain hits capped at 10, which is the payout basis
    #   bonus      — the difference, i.e. what capping gave back
    "score": {"raw": 1284.50, "normalised": 1096.25, "bonus": 188.25, "hits": 169},

    # Exactly what `/rw overview`'s member mode already prints.
    "summary": {
        "outgoing": {"attack": 148, "assist": 12, "loss": 9, "total": 169},
        "incoming": {"attack": 31, "assist": 0, "loss": 44, "total": 75},
        "revives": {"success": 6, "failure": 1, "total": 7},
    },

    # ⚠️ Three states, from the dashboard: "disabled" (this tenant does not run
    # the dibs summary), "pending" (on, not finished for this war) or
    # "measured". `counts` is non-null only when measured, and then a row of
    # zeros is a real measurement rather than an absence.
    "dibs": {
        "state": "measured",
        "measured_ms": 1790007200000,
        "grace_seconds": 120,
        "counts": {
            "claims": 24, "converted": 17, "attempted": 2, "taken_from": 1,
            "no_swing": 1, "outside": 1, "released": 0, "hopped": 1,
            "overridden": 0, "board_reset": 0, "still_open": 0, "unknown": 0,
            "green_means_go": 1, "swung": 19, "took_from_others": 0,
        },
    },

    # war_room_roll_call_v2, this member's row.
    "roll_call": {
        "responded": True,
        "on_at_start": True,
        "availability": "on_time",
        "can_self_hospital": True,
        "needs_merc": False,
        "revised": False,
        # Did they actually turn up? Derived by comparing their first attack
        # against the war's start — NOT a stored column. See the plan.
        "first_action_offset_sec": 92,
    },

    # Pre-war stacks snapshot (#480), which the roll-call panel already joins.
    "stacks": {"energy": 1000, "overdosed": False, "snapshot_at": 1_791_244_500},
}

# ⚠️ A SECOND fixture, because the interesting cases are the absences. A member
# who never answered the roll call, never claimed anything, and was hit more
# than they hit is the row most likely to render as "0 0 0 —" nonsense.
MEMBER_REPORT_SPARSE = {
    "faction": {"slug": "forge", "name": "Forge"},
    "war": dict(MEMBER_REPORT["war"]),
    "member": {"id": 2642228, "name": "Diethylamine"},
    "summary": {
        "outgoing": {"attack": 0, "assist": 0, "loss": 0, "total": 0},
        "incoming": {"attack": 9, "assist": 0, "loss": 2, "total": 11},
        "revives": {"success": 2, "failure": 0, "total": 2},
    },
    "score": None,         # no contribution row for this member
    # On, but this war has not been summarised — NOT the same as claiming
    # nothing, and the formatter must not collapse the two.
    "dibs": {"state": "pending", "measured_ms": None, "grace_seconds": None, "counts": None},
    "roll_call": None,     # never answered
    "stacks": None,        # not snapshotted
}
