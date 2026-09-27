"""
The send-once ledger (#785).

⚠️ Without it the poll loop re-sends every ping on every cycle: twelve messages
per person per shift at a five-minute cadence, and seventy-two about the same
empty 3am slot. A channel that mutes the bot is worse than no bot, because
leadership believes coverage is being watched.
"""

import chain_ledger as led


def test_marks_and_remembers():
    assert not led.already_sent("forge", "a")
    led.mark_sent("forge", "a")
    assert led.already_sent("forge", "a")


def test_factions_do_not_share_suppressions():
    led.mark_sent("forge", "a")
    assert not led.already_sent("tnl", "a")


def test_keys_separate_the_two_kinds_of_shift_ping():
    # ⚠️ The early travel warning and the five-minute reminder are different
    # messages that both need to arrive; a key without the kind lets one
    # silence the other.
    assert led.shift_key("1", 100) != led.shift_key("1", 100, "flight")


def test_keys_separate_members_hours_and_gap_stages():
    assert led.shift_key("1", 100) != led.shift_key("2", 100)
    assert led.shift_key("1", 100) != led.shift_key("1", 200)
    assert led.gap_key(100, "first") != led.gap_key(100, "last-call")


def test_old_entries_are_pruned_but_recent_ones_survive():
    # ⚠️ The file is rewritten whole on every save, so an append-only record of
    # every ping ever sent grows without bound.
    now = 10 * led.KEEP_MS
    led.mark_sent("forge", "ancient", now_ms=now - led.KEEP_MS - 1)
    led.mark_sent("forge", "recent", now_ms=now)
    assert not led.already_sent("forge", "ancient")
    assert led.already_sent("forge", "recent")


def test_pruning_never_forgets_a_ping_still_in_reach():
    # The earliest any ping fires is flight_lead_minutes (capped at 6h) before
    # an hour; the horizon looks a day ahead. 48h of memory covers both.
    assert led.KEEP_MS >= 48 * 3600_000


def test_forget_drops_one_factions_ledger_only():
    led.mark_sent("forge", "a")
    led.mark_sent("tnl", "a")
    led.forget("forge")
    assert not led.already_sent("forge", "a")
    assert led.already_sent("tnl", "a")
