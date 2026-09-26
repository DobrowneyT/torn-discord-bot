"""
What the board says, and the handful of ways it could lie (#783).

These are about meaning, not pixels. A board that renders beautifully while
saying an uncovered hour is covered is the single worst outcome here: leadership
stops checking the page because the channel told them it was fine.
"""

import re

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
    base = {"name": "Goosey", "member_id": "873341"}
    linked = f.build_shift_ping([{**base, "discord_id": 42}], hour_start=TOP + HOUR)
    assert "<@42>" in linked
    # ⚠️ `Name [ID]`, not a bare name: leadership needs to see WHO to chase, and
    # the id is what tells two members with similar display names apart (#784).
    assert "Goosey [873341]" in f.build_shift_ping([base], hour_start=TOP + HOUR)


def test_one_message_names_everybody_on_the_hour():
    # ⚠️ One post per watcher was two near-identical messages seconds apart,
    # each telling one of the pair about the other. Nobody reads the second.
    both = f.build_shift_ping(
        [{"name": "A", "member_id": "1", "discord_id": 11},
         {"name": "B", "member_id": "2", "discord_id": 22}],
        hour_start=TOP + HOUR)
    assert "<@11>" in both and "<@22>" in both
    assert both.count("chain watch starts") == 1


def test_the_shift_ping_names_TCT_like_everything_else():
    body = f.build_shift_ping([{"name": "A", "member_id": "1"}], hour_start=TOP + HOUR)
    assert "TCT" in body


def test_no_watchers_produces_no_message():
    assert f.build_shift_ping([], hour_start=TOP + HOUR) is None


def test_shift_ping_says_when_you_are_alone():
    solo = f.build_shift_ping([{"name": "A", "member_id": "1"}], hour_start=TOP + HOUR)
    assert "only watcher" in solo
    # ⚠️ Trailing the sentence, not wedged between the name and the time.
    assert solo.index("chain watch starts") < solo.index("only watcher")
    paired = f.build_shift_ping([{"name": "A", "member_id": "1"},
                                 {"name": "B", "member_id": "2"}],
                                hour_start=TOP + HOUR)
    assert "only watcher" not in paired and "B [2]" in paired


def test_every_board_row_names_TCT():
    # ⚠️ On every row, not just the footer. The board scrolls for a twelve-day
    # chain and gets screenshotted a line at a time, so a footer is out of
    # sight for all but the first few hours — and a bold time with no unit
    # reads as "some timezone", most likely the reader's own, which is the
    # number sitting right beside it.
    e = board([hour(1, [w("A")]), hour(2, [w("A"), w("B")])])
    # ⚠️ Require the backtick: the LEGEND line also starts with 🟢 and would
    # otherwise be asserted against, failing for a reason unrelated to the rows.
    rows = [l for l in e.description.splitlines()
            if l.startswith(("🟢", "🟡", "🔴")) and "`" in l]
    assert rows, "no hour rows rendered"
    for row in rows:
        assert " TCT " in row, row


def test_a_collapsed_run_names_TCT_too():
    e = board([hour(1, []), hour(2, []), hour(3, [])])
    run = [l for l in e.description.splitlines() if "hours, nobody signed up" in l]
    assert len(run) == 1 and " TCT " in run[0]


# ── compact board (an embed cannot be made wider) ───────────────────────────
#
# ⚠️ Discord fixes the embed width; there is no API for it. A mention renders
# as the member's SERVER NICKNAME, so a convention like
# "MonChoon_616 [2250591] (TNLF)" is 30 characters — two of those plus the time
# prefix is ~84 against the ~55-60 a row fits. Two watchers can therefore never
# share a row while mentions are used, however the text is arranged.

LINKED = {"member_id": "2250591", "name": "MonChoon_616",
          "discord_id": 179518259471712256, "travel": None}
UNLINKED = {"member_id": "873341", "name": "Goosey", "travel": None}


def compact_board(hours):
    return board(hours, **{}) if False else f.build_board(
        {"event": {"title": "T", "slots_per_hour": 2},
         "chain": None, "projection": {"measured": False},
         "gap_horizon_hours": 6, "hours": hours},
        now_ms=NOW, compact=True)[0]


def test_compact_drops_the_mention_for_a_linked_member():
    e = compact_board([hour(1, [LINKED, LINKED])])
    row = [l for l in e.description.splitlines() if "`" in l and l.startswith("🟢")][0]
    assert "<@" not in row
    assert "MonChoon_616" in row


def test_compact_keeps_the_id_for_an_UNLINKED_member():
    # ⚠️ It is the one thing a leader needs in order to fix the link, and those
    # are the minority of rows, so it costs little width.
    e = compact_board([hour(1, [UNLINKED, UNLINKED])])
    assert "Goosey [873341]" in e.description


def _rendered(line):
    """
    Roughly what Discord shows.

    ⚠️ `<t:1800003600:t>` is 16 characters of markup that renders as about 7
    ("6:00 PM"), so measuring the raw string overstates every row by nine.
    Measuring the wrong string is how a width test passes while the board still
    wraps — or fails while it does not, which is what happened here first.
    """
    return re.sub(r"<t:\d+:[a-zA-Z]>", "6:00 PM", line)


def test_compact_actually_fits_two_watchers_on_a_line():
    # The point of the whole setting. ~55-60 is what an embed row holds.
    e = compact_board([hour(1, [LINKED, UNLINKED])])
    row = [l for l in e.description.splitlines() if "`" in l and l.startswith("🟢")][0]
    shown = _rendered(row)
    assert len(shown) < 60, f"{len(shown)} chars: {shown}"


def test_the_mentioning_board_is_the_one_that_cannot_fit():
    # ⚠️ Pins the reason the setting exists. A mention renders as the server
    # NICKNAME, not the Torn name, so the rendered row is far wider than the
    # source suggests — and no arrangement of the text fixes that.
    nickname = "@MonChoon_616 [2250591] (TNLF)"
    e = board([hour(1, [LINKED, LINKED])])
    row = [l for l in e.description.splitlines() if "`" in l and l.startswith("🟢")][0]
    shown = _rendered(row).replace("<@179518259471712256>", nickname)
    assert len(shown) > 60, "if this ever fits, the setting has stopped earning its place"


def test_the_default_board_still_mentions():
    # MonChoon likes the pingable names; compact is opt-in.
    e = board([hour(1, [LINKED, LINKED])])
    assert "<@179518259471712256>" in e.description


def test_compact_does_not_touch_the_shift_ping():
    # ⚠️ The board's mention notifies nobody (embeds do not ping); the shift
    # ping is a plain message, where a mention DOES notify — which is the whole
    # point of it and why compact never reaches this far.
    assert "<@42>" in f.build_shift_ping(
        [{"name": "A", "member_id": "1", "discord_id": 42}], hour_start=TOP + HOUR)
