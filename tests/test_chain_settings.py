"""
The settings store (#783).

⚠️ The line this file defends is which half of the numbers the bot owns. The
bot owns how it TALKS — cadence, lead times, channels. The dashboard owns the
event: bonus hours, watchers per hour, payout, the gap horizon. Two
authoritative places for one number means the one nobody is looking at drifts,
and here that shows up as a channel insisting an hour is uncovered while the
page says it is fine.
"""

import json

import chain_settings as cs
import state

SLUG = "forge"


def test_the_event_is_not_configurable_from_discord():
    # Naming them explicitly, because the tempting fix for "the bot shows the
    # wrong bonus hours" is to add a setting here rather than to look at why
    # the payload disagrees.
    for forbidden in ("gap_horizon_hours", "slots_per_hour", "payout_per_slot",
                      "bonus_hours", "bonus_multiplier"):
        assert forbidden not in cs.SETTINGS


def test_defaults_come_back_before_anything_is_stored():
    assert cs.get(SLUG, "shift_lead_minutes") == 5
    # ⚠️ An hour, not five minutes. Five minutes' notice is useless to somebody
    # over the Atlantic, which is the case this ping exists for.
    assert cs.get(SLUG, "flight_lead_minutes") == 60


def test_a_set_value_persists_and_resets():
    ok, _ = cs.set_value(SLUG, "shift_lead_minutes", "12")
    assert ok and cs.get(SLUG, "shift_lead_minutes") == 12
    cs.reset(SLUG, "shift_lead_minutes")
    assert cs.get(SLUG, "shift_lead_minutes") == 5


def test_out_of_range_is_refused_with_a_usable_message():
    ok, msg = cs.set_value(SLUG, "board_refresh_seconds", "5")
    assert not ok and "at least 60" in msg
    # ⚠️ and the old value survives the refusal — a rejected edit that silently
    # wiped the setting would be worse than accepting the bad one.
    assert cs.get(SLUG, "board_refresh_seconds") == 300


def test_nonsense_is_refused_rather_than_coerced():
    ok, msg = cs.set_value(SLUG, "board_hours_shown", "lots")
    assert not ok and "whole number" in msg
    ok, msg = cs.set_value(SLUG, "mention_members", "maybe")
    assert not ok and "on/off" in msg


def test_booleans_accept_the_words_people_actually_type():
    for word in ("off", "false", "no", "0"):
        assert cs.set_value(SLUG, "mention_members", word)[0]
        assert cs.get(SLUG, "mention_members") is False
    for word in ("on", "true", "yes", "1"):
        assert cs.set_value(SLUG, "mention_members", word)[0]
        assert cs.get(SLUG, "mention_members") is True


def test_an_unknown_key_points_at_the_list():
    ok, msg = cs.set_value(SLUG, "nope", "1")
    assert not ok and "/chain settings" in msg


# ── per-tenant scoping (#783) ────────────────────────────────────────────────

def test_one_factions_tuning_does_not_touch_another():
    # ⚠️ The failure this prevents is quiet: a leader retunes their own board
    # mid-chain and another faction's cadence changes with it, in a channel
    # nobody is watching at the time.
    cs.set_value("forge", "board_refresh_seconds", "120")
    assert cs.get("forge", "board_refresh_seconds") == 120
    assert cs.get("tnl", "board_refresh_seconds") == 300


def test_reset_is_scoped_too():
    cs.set_value("forge", "shift_lead_minutes", "20")
    cs.set_value("tnl", "shift_lead_minutes", "30")
    cs.reset("forge", "shift_lead_minutes")
    assert cs.get("forge", "shift_lead_minutes") == 5
    assert cs.get("tnl", "shift_lead_minutes") == 30


def _write_flat_state():
    """State as it was written before #783 — one flat block, no slugs."""
    with open(state.STATE_PATH, "w") as f:
        json.dump({"message_id": 1, "chain_settings": {
            "board_channel_id": 999, "board_refresh_seconds": 120,
        }}, f)


def test_pre_multi_tenant_settings_are_migrated_not_dropped():
    # ⚠️ The flat block holds the board channel id. Dropping it leaves the bot
    # posting nowhere with no error, which reads as the bot being broken rather
    # than as a config that moved.
    _write_flat_state()
    assert cs.get(cs.LEGACY_SLUG, "board_channel_id") == 999
    assert cs.get(cs.LEGACY_SLUG, "board_refresh_seconds") == 120


def test_the_first_tenant_adopts_the_old_settings_and_the_second_does_not():
    _write_flat_state()
    assert cs.adopt_legacy("forge") is True
    assert cs.get("forge", "board_channel_id") == 999
    # ⚠️ Only the first. Copying one faction's channel id into five tenants
    # would point every board at one channel, which looks like the bot ignoring
    # its config entirely.
    assert cs.adopt_legacy("tnl") is False
    assert cs.get("tnl", "board_channel_id") == 0


def test_migration_is_idempotent_and_leaves_scoped_state_alone():
    cs.set_value("forge", "board_hours_shown", "12")
    # Reading repeatedly must not re-wrap an already-scoped block into another
    # layer, which would make every value vanish behind a slug called "forge".
    for _ in range(3):
        assert cs.get("forge", "board_hours_shown") == 12
