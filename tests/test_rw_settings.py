"""
Ranked-war settings, and their separation from Chain Watch's (#944).

⚠️ The separation is the point. `member_thread_forum_id` shipped in
`chain_settings` for about an hour, and anyone who set it in that window must
not silently lose it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chain_settings  # noqa: E402
import rw_settings  # noqa: E402
import state  # noqa: E402


class TestTheSeparation:
    def test_the_forum_is_NOT_a_chain_setting(self):
        # ⚠️ `/chain settings` already lists eleven entries and ends with a
        # footer explaining which half lives on the dashboard. One command
        # configuring two unrelated features is how the setting nobody can find
        # ends up in the longer list.
        assert "member_thread_forum_id" not in chain_settings.SETTINGS

    def test_it_IS_an_rw_setting(self):
        assert "member_thread_forum_id" in rw_settings.SETTINGS

    def test_the_two_stores_are_different_blocks(self):
        assert rw_settings.STATE_KEY != chain_settings.STATE_KEY

    def test_setting_one_does_not_touch_the_other(self):
        rw_settings.set_value("forge", "member_thread_forum_id", 123)
        chain_settings.set_value("forge", "board_channel_id", 456)
        assert rw_settings.get("forge", "member_thread_forum_id") == 123
        assert chain_settings.get("forge", "board_channel_id") == 456


class TestPerFaction:
    def test_unset_is_zero_not_a_guess(self):
        # ⚠️ No fallback on purpose: guessing an audience for a permanent
        # per-member record is worse than posting nothing.
        assert rw_settings.get("forge", "member_thread_forum_id") == 0

    def test_each_faction_keeps_its_own(self):
        # One faction's forum id in five tenants would publish five factions'
        # records into one channel.
        rw_settings.set_value("forge", "member_thread_forum_id", 111)
        rw_settings.set_value("tnl", "member_thread_forum_id", 222)
        assert rw_settings.get("forge", "member_thread_forum_id") == 111
        assert rw_settings.get("tnl", "member_thread_forum_id") == 222

    def test_reset_puts_it_back_to_unset(self):
        rw_settings.set_value("forge", "member_thread_forum_id", 111)
        ok, _ = rw_settings.reset("forge", "member_thread_forum_id")
        assert ok
        assert rw_settings.get("forge", "member_thread_forum_id") == 0

    def test_a_non_numeric_value_is_refused_with_a_message(self):
        ok, msg = rw_settings.set_value("forge", "member_thread_forum_id", "the-forum")
        assert not ok and "whole number" in msg

    def test_an_unknown_key_points_at_the_right_command(self):
        ok, msg = rw_settings.set_value("forge", "nope", 1)
        assert not ok and "/rw config" in msg


class TestAdoptingTheBrieflyMisplacedValue:
    def _plant_in_chain(self, slug, value):
        st = state.load_state()
        st.setdefault(chain_settings.STATE_KEY, {}).setdefault(slug, {})[
            "member_thread_forum_id"] = value
        state.save_state(st)

    def test_a_value_set_through_chain_set_is_carried_over(self):
        # ⚠️ Otherwise member reports are silently off while the forum is still
        # visible in `/chain settings` — the worst shape, because it LOOKS right.
        self._plant_in_chain("forge", 999)
        assert rw_settings.adopt_from_chain("forge") is True
        assert rw_settings.get("forge", "member_thread_forum_id") == 999

    def test_it_is_removed_from_the_chain_block_so_it_cannot_drift(self):
        self._plant_in_chain("forge", 999)
        rw_settings.adopt_from_chain("forge")
        chain_block = state.load_state().get(chain_settings.STATE_KEY, {})
        assert "member_thread_forum_id" not in (chain_block.get("forge") or {})

    def test_it_does_not_overwrite_a_value_set_properly(self):
        # If both exist, the one set through the command that still exists is
        # the one somebody meant.
        rw_settings.set_value("forge", "member_thread_forum_id", 111)
        self._plant_in_chain("forge", 999)
        assert rw_settings.adopt_from_chain("forge") is False
        assert rw_settings.get("forge", "member_thread_forum_id") == 111

    def test_it_is_safe_to_call_every_time(self):
        # `forum_id()` calls this on every poll tick.
        assert rw_settings.adopt_from_chain("forge") is False
        assert rw_settings.adopt_from_chain("forge") is False

    def test_a_zero_is_not_worth_adopting(self):
        self._plant_in_chain("forge", 0)
        assert rw_settings.adopt_from_chain("forge") is False
