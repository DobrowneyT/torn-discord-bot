"""
What the board says, and the handful of ways it could lie (#783).

These are about meaning, not pixels. A board that renders beautifully while
saying an uncovered hour is covered is the single worst outcome here: leadership
stops checking the page because the channel told them it was fine.
"""

import chain_formatter as f
import chain_mock


HOUR = 3_600_000
NOW = 1_800_000_000_000
TOP = NOW - (NOW % HOUR)


def hour(offset, watchers, bonus=False, slots=2):
    return {
        "hour_start": TOP + offset * HOUR,
        "bonus": bonus,
        "slots_per_hour": slots,
        "watchers": watchers,
        "open_slots": max(0, slots - len(watchers)),
    }


def w(name, travel=None):
    return {"member_id": "1", "name": name, "travel": travel}


def board(hours, **over):
    watch = {
        "event": {"title": "Halloween 100k Chain", "slots_per_hour": 2},
        "chain": {"current": 61_204, "target": 100_000},
        "projection": {"measured": False, "reason": "too-early"},
        "gap_horizon_hours": 6,
        "hours": hours,
        **over,
    }
    return f.build_board(watch, now_ms=NOW)[0]


def test_counts_unfilled_slots_not_unfilled_hours():
    # ⚠️ An hour holding one of two watchers is ONE unfilled slot — not zero
    # (it looks covered) and not two (it looks abandoned). Both errors change
    # what a leader does next.
    e = board([hour(1, [w("Goosey")]), hour(2, [])])
    assert "3 slots unfilled" in e.description


def test_an_hour_with_nobody_turns_the_board_red():
    assert board([hour(1, [])]).color.value == f.COLOR_NAKED


def test_a_thin_hour_is_yellow_and_a_full_one_green():
    assert board([hour(1, [w("A")])]).color.value == f.COLOR_THIN
    assert board([hour(1, [w("A"), w("B")])]).color.value == f.COLOR_OK


def test_only_the_next_six_hours_decide_the_colour():
    # ⚠️ A gap eleven hours out is real but not urgent. Colouring the board red
    # for it means the board is red for most of a twelve-day chain, and a
    # permanently red board is a board nobody reads.
    e = board([hour(1, [w("A"), w("B")]), hour(9, [])])
    assert e.color.value == f.COLOR_OK
    assert "Every slot in the next 6 hours is covered" in e.description


def test_says_plainly_when_the_projection_cannot_be_made():
    # An absent projection is fine. A confident wrong one is not.
    assert "not enough chain yet" in board([hour(1, [w("A"), w("B")])]).description


def test_renders_a_measured_projection_as_a_band():
    e = board([hour(1, [w("A"), w("B")])], projection={
        "measured": True, "earliest": TOP + 40 * HOUR,
        "latest": TOP + 60 * HOUR, "hits_per_hour": 242.5,
    })
    # Two timestamps, not one — the band is the honest shape.
    assert e.description.count(f"<t:{(TOP + 40 * HOUR) // 1000}:f>") == 1
    assert f"<t:{(TOP + 60 * HOUR) // 1000}:f>" in e.description


def test_drops_hours_that_have_finished():
    e = board([hour(-4, [w("Ghost")]), hour(1, [w("A"), w("B")])])
    assert "Ghost" not in e.description


def test_the_mock_still_renders():
    # The mock is the contract stand-in until the endpoint is wired (#785); if
    # it stops rendering, the two have drifted.
    import time
    f.build_board(chain_mock.mock_watch(int(time.time())), now_ms=int(time.time() * 1000))


def test_shift_ping_mentions_when_linked_and_names_when_not():
    base = {"name": "Goosey", "hour_start": TOP + HOUR, "chain": {"current": 61_204}}
    assert "<@42>" in f.build_shift_ping({**base, "discord_id": 42})
    # ⚠️ Not a bare name: leadership needs to see WHO to chase, and the id is
    # what identifies them when two members share a display name (#784).
    assert "**Goosey**" in f.build_shift_ping(base)


def test_shift_ping_says_when_you_are_alone():
    solo = f.build_shift_ping({"name": "A", "hour_start": TOP + HOUR})
    assert "only watcher" in solo
    paired = f.build_shift_ping({"name": "A", "hour_start": TOP + HOUR,
                                 "partner": {"name": "B"}})
    assert "only watcher" not in paired and "**B**" in paired
