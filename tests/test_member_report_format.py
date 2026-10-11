"""
The post-war member report formatter.

⚠️ **Every case here is about ABSENCE**, because that is where this feature can
do harm. The report lands in a forum thread with one member's name on it, and
the difference between "never answered the roll call" and "said no", or between
"claimed nothing" and "we have not counted this war yet", is the difference
between a record and an accusation.
"""

import member_report_format as fmt
import member_report_mock as mock


def desc(payload):
    return fmt.build_member_report(payload)["description"]


class TestScoreLine:
    def test_shows_raw_and_normalised_together(self):
        line = fmt.score_line(mock.MEMBER_REPORT)
        assert "1,284.50" in line
        assert "1,096.25" in line

    def test_names_the_cap_when_it_actually_bit(self):
        # ⚠️ A member who landed the 25k hit sees a big raw number and a smaller
        # payout one. The gap needs explaining on the line, not in a DM later.
        assert "from bonus hits" in fmt.score_line(mock.MEMBER_REPORT)
        assert "188.25" in fmt.score_line(mock.MEMBER_REPORT)

    def test_stays_quiet_when_the_cap_changed_nothing(self):
        # "−0.00 from bonus hits" on every ordinary row trains people to skip
        # the line, which costs more than it says.
        p = {"score": {"raw": 500, "normalised": 500, "bonus": 0}}
        assert "bonus hits" not in fmt.score_line(p)

    def test_no_contribution_row_is_not_a_zero_score(self):
        # ⚠️ Having no row is a different fact from having scored nothing.
        assert "not recorded" in fmt.score_line({"score": None})
        assert "0.00" not in fmt.score_line({"score": None})

    def test_the_faction_result_is_not_a_line_any_more(self):
        # MonChoon: the Won/Lost line is identical in every member's thread and
        # says nothing about the person whose thread it is. It survives as the
        # embed colour, which is read without being read.
        d = desc(mock.MEMBER_REPORT)
        assert "Won" not in d and "Lost" not in d


class TestRollCall:
    def test_no_answer_is_not_a_refusal(self):
        # ⚠️ THE case. Printing silence as "said no" in somebody's own
        # performance thread is an accusation the data does not support.
        assert "no answer recorded" in fmt.roll_call_line({"roll_call": None})
        assert "no answer recorded" in fmt.roll_call_line({"roll_call": {"responded": False}})

    def test_says_what_they_promised_and_whether_they_turned_up(self):
        line = fmt.roll_call_line(mock.MEMBER_REPORT)
        assert "on at the start" in line
        assert "turned up" in line

    def test_a_late_first_hit_is_reported_in_minutes(self):
        p = {"roll_call": {"responded": True, "on_at_start": True,
                           "availability": "on_time", "first_action_offset_sec": 3600}}
        assert "60m" in fmt.roll_call_line(p)

    def test_unmeasured_turn_up_is_not_a_no_show(self):
        # ⚠️ No attack to measure against is also true of somebody who spent the
        # war being revived. "Did not show" would be an invention.
        p = {"roll_call": {"responded": True, "on_at_start": True,
                           "availability": "on_time", "first_action_offset_sec": None}}
        line = fmt.roll_call_line(p)
        assert "not measured" in line
        assert "did not" not in line.lower()

    def test_self_hosp_only_qualifies_a_no(self):
        # The panel renders "—" for everyone who IS on at the start; repeating
        # it here would imply it meant something.
        yes = fmt.roll_call_line({"roll_call": {"responded": True, "on_at_start": True,
                                                "availability": "on_time", "can_self_hospital": True}})
        no = fmt.roll_call_line({"roll_call": {"responded": True, "on_at_start": False,
                                               "availability": "late_1h", "can_self_hospital": True}})
        assert "self-hosp" not in yes
        assert "self-hosp" in no


class TestDibs:
    def test_not_counted_is_not_zero_claims(self):
        # ⚠️ The summary job waits for the war's attacks to land. Telling
        # somebody who claimed twenty targets that they claimed none is the
        # worst version of this whole feature.
        assert "not counted yet" in fmt.dibs_line({"dibs": None})

    def test_pending_is_not_counted_yet(self):
        # The job is ON and has not finished for this war.
        assert "not counted yet" in fmt.dibs_line({"dibs": {"state": "pending", "counts": None}})

    def test_disabled_omits_the_line_entirely(self):
        # ⚠️ This tenant does not run the dibs summary, so the absence says
        # nothing about the member. `dibs-summary` is enabled on one of five
        # tenants, so otherwise every one of ~94 threads would carry a standing
        # complaint about a config choice, every war, for ever.
        assert fmt.dibs_line({"dibs": {"state": "disabled", "counts": None}}) is None

    def test_measured_without_counts_does_not_become_a_zero(self):
        # A dashboard bug must not be rendered as a claim about a person.
        assert "not counted yet" in fmt.dibs_line({"dibs": {"state": "measured", "counts": None}})

    def test_no_claims_says_so_plainly(self):
        assert "no claims" in fmt.dibs_line(
            {"dibs": {"state": "measured", "counts": {"claims": 0, "took_from_others": 0}}})

    def test_a_taker_who_claimed_nothing_still_appears(self):
        line = fmt.dibs_line(
            {"dibs": {"state": "measured", "counts": {"claims": 0, "took_from_others": 3}}})
        assert "no claims" not in line
        assert "3" in line

    def test_green_is_named_because_it_explains_a_dead_claim(self):
        line = fmt.dibs_line(mock.MEMBER_REPORT)
        assert "green" in line

    def test_quiet_buckets_are_omitted_rather_than_printed_as_zero(self):
        line = fmt.dibs_line({"dibs": {"state": "measured", "counts": {
            "claims": 5, "converted": 5, "no_swing": 0,
            "taken_from": 0, "green_means_go": 0, "took_from_others": 0}}})
        assert "no swing" not in line
        assert "taken from" not in line


class TestWholeEmbed:
    def test_the_sparse_member_renders_without_inventing_anything(self):
        d = desc(mock.MEMBER_REPORT_SPARSE)
        assert "no answer recorded" in d
        assert "not counted yet" in d
        # ⚠️ No stacks row at all rather than an empty one.
        assert "Pre-war" not in d

    def test_the_full_member_carries_every_section(self):
        d = desc(mock.MEMBER_REPORT)
        for needle in ("Outgoing", "Incoming", "Revives", "Roll call", "Dibs", "Pre-war"):
            assert needle in d

    def test_colour_follows_the_result(self):
        assert fmt.build_member_report(mock.MEMBER_REPORT)["colour"] == 0x2ECC71
        lost = dict(mock.MEMBER_REPORT, war=dict(mock.MEMBER_REPORT["war"], won=False))
        assert fmt.build_member_report(lost)["colour"] == 0xE74C3C

    def test_description_is_clamped_to_discords_limit(self):
        assert len(desc(mock.MEMBER_REPORT)) <= fmt.DESCRIPTION_MAX

    def test_an_empty_payload_does_not_raise(self):
        # A member with no data at all is a real row on a quiet war.
        out = fmt.build_member_report({})
        assert out["title"]
        assert out["description"]
