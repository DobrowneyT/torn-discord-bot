"""
The settings store (#783).

⚠️ The line this file defends is which half of the numbers the bot owns. The
bot owns how it TALKS — cadence, lead times, channels. The dashboard owns the
event: bonus hours, watchers per hour, payout, the gap horizon. Two
authoritative places for one number means the one nobody is looking at drifts,
and here that shows up as a channel insisting an hour is uncovered while the
page says it is fine.
"""

import chain_settings as cs


def test_the_event_is_not_configurable_from_discord():
    # Naming them explicitly, because the tempting fix for "the bot shows the
    # wrong bonus hours" is to add a setting here rather than to look at why
    # the payload disagrees.
    for forbidden in ("gap_horizon_hours", "slots_per_hour", "payout_per_slot",
                      "bonus_hours", "bonus_multiplier"):
        assert forbidden not in cs.SETTINGS


def test_defaults_come_back_before_anything_is_stored():
    assert cs.get("shift_lead_minutes") == 5
    # ⚠️ An hour, not five minutes. Five minutes' notice is useless to somebody
    # over the Atlantic, which is the case this ping exists for.
    assert cs.get("flight_lead_minutes") == 60


def test_a_set_value_persists_and_resets():
    ok, _ = cs.set_value("shift_lead_minutes", "12")
    assert ok and cs.get("shift_lead_minutes") == 12
    cs.reset("shift_lead_minutes")
    assert cs.get("shift_lead_minutes") == 5


def test_out_of_range_is_refused_with_a_usable_message():
    ok, msg = cs.set_value("board_refresh_seconds", "5")
    assert not ok and "at least 60" in msg
    # ⚠️ and the old value survives the refusal — a rejected edit that silently
    # wiped the setting would be worse than accepting the bad one.
    assert cs.get("board_refresh_seconds") == 300


def test_nonsense_is_refused_rather_than_coerced():
    ok, msg = cs.set_value("board_hours_shown", "lots")
    assert not ok and "whole number" in msg
    ok, msg = cs.set_value("mention_members", "maybe")
    assert not ok and "on/off" in msg


def test_booleans_accept_the_words_people_actually_type():
    for word in ("off", "false", "no", "0"):
        assert cs.set_value("mention_members", word)[0]
        assert cs.get("mention_members") is False
    for word in ("on", "true", "yes", "1"):
        assert cs.set_value("mention_members", word)[0]
        assert cs.get("mention_members") is True


def test_an_unknown_key_points_at_the_list():
    ok, msg = cs.set_value("nope", "1")
    assert not ok and "/chain settings" in msg
