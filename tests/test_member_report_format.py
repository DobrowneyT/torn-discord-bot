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


class TestTheDashboardContract:
    """
    ⚠️ **The tests that would have caught what shipped.**

    Every other test in this file drives the MOCK. The mock was invented before
    the endpoint existed, and two keys drifted: the dashboard sent the raw
    column names `on_at_start`/`energy_current` while this formatter read
    `responded`/`energy`. All 23 tests passed. In production a member who had
    answered the roll call was published as "no answer recorded", and one with
    965 energy as "0 energy", in their own permanent thread.

    These fixtures are transcribed from `MemberRollCall` and `MemberStacks` in
    `server/src/memberWarReport.ts`. If that interface changes, these must too —
    which is the point: a visible place to break.
    """

    def payload(self, **over):
        p = {
            "war": {"id": 50549, "opponent_name": "Romania Elite Kommando",
                    "opponent_id": 18569, "is_termed": False},
            "member": {"id": 2250591, "name": "MonChoon_616"},
            "score": {"raw": 1400.34, "normalised": 1400.34, "bonus_reduction": 0.0,
                      "hits": 87, "bonus_hits": 0},
            "summary": {"outgoing": {"attack": 87, "assist": 13, "loss": 23, "total": 123},
                        "incoming": {"attack": 31, "assist": 28, "loss": 7, "total": 66},
                        "revives": {"success": 20, "failure": 18, "total": 38}},
            "dibs": {"state": "measured", "measured_ms": 1, "grace_seconds": 120,
                     "counts": {"claims": 34, "converted": 25, "no_swing": 2,
                                "taken_from": 2, "took_from_others": 1,
                                "green_means_go": 0}},
            "roll_call": {
                "responded": True, "on_at_start": True, "availability": "on_time",
                "can_self_hospital": None, "needs_merc": None, "revised": False,
                "submitted_ms": 1_791_000_000_000, "first_action_offset_sec": 92,
            },
            "stacks": {
                "energy": 965, "energy_max": 150, "has_reading": True,
                "overdosed": False, "captured_ms": 1_791_000_000_000,
            },
        }
        p.update(over)
        return p

    def test_a_member_who_answered_is_NOT_no_answer_recorded(self):
        line = fmt.roll_call_line(self.payload())
        assert "no answer recorded" not in line
        assert "on at the start" in line

    def test_real_energy_is_not_rendered_as_zero(self):
        line = fmt.stacks_line(self.payload())
        assert line is not None
        assert "965" in line
        assert "0 energy" not in line

    def test_the_whole_embed_renders_the_real_payload(self):
        d = desc(self.payload())
        assert "no answer recorded" not in d
        assert "0 energy" not in d
        assert "965" in d

    # ⚠️ has_reading, the field that distinguishes unknown from empty.
    def test_unreadable_energy_is_not_printed_as_zero(self):
        line = fmt.stacks_line(self.payload(
            stacks={"energy": None, "energy_max": 150, "has_reading": False,
                    "overdosed": False, "captured_ms": None}))
        # Nothing measurable and nothing to flag: no line at all beats "0".
        assert line is None

    def test_an_OD_is_stated_even_when_energy_could_not_be_read(self):
        line = fmt.stacks_line(self.payload(
            stacks={"energy": None, "energy_max": 150, "has_reading": False,
                    "overdosed": True, "captured_ms": None}))
        assert "overdosed" in line
        assert "not measured" in line

    def test_an_OD_is_stated_beside_a_real_energy_reading(self):
        line = fmt.stacks_line(self.payload(
            stacks={"energy": 965, "energy_max": 150, "has_reading": True,
                    "overdosed": True, "captured_ms": None}))
        assert "965" in line and "overdosed" in line

    def test_no_OD_is_omitted_entirely(self):
        # MonChoon: "if they didnt we can omit it."
        assert "overdos" not in fmt.stacks_line(self.payload())

    # ⚠️ real vs termed, which changes how every number below should be read.
    def test_a_real_war_is_titled_plainly(self):
        assert fmt.war_title(self.payload()) == \
            "MonChoon_616 — war vs Romania Elite Kommando"

    def test_a_termed_war_says_so_in_the_title(self):
        p = self.payload()
        p["war"]["is_termed"] = True
        assert "termed war vs" in fmt.war_title(p)

    def test_a_missing_is_termed_reads_as_real_rather_than_crashing(self):
        p = self.payload()
        del p["war"]["is_termed"]
        assert "termed" not in fmt.war_title(p)


class TestTheDibsBucketsAddUp:
    """
    ⚠️ The eleven outcome buckets are mutually exclusive and exhaustive: they
    SUM to `claims`. The dashboard's schema says so, because "a leaderboard
    whose columns do not add up is one nobody can check".

    The first version printed four of them beside two figures from a DIFFERENT
    axis. A real row rendered as "34 claimed · 25 converted · 2 no swing · 2
    taken from them · 1 taken from others" — 30, not 34, with 3 attempted and 2
    released simply never shown. MonChoon caught it by doing the arithmetic.
    """

    REAL_ROW = {
        "claims": 34, "converted": 25, "attempted": 3, "taken_from": 2,
        "no_swing": 2, "outside": 0, "released": 2, "hopped": 0,
        "overridden": 0, "board_reset": 0, "still_open": 0, "unknown": 0,
        "swung": 29, "took_from_others": 1, "green_means_go": 0,
    }

    def line(self, **over):
        row = dict(self.REAL_ROW)
        row.update(over)
        return fmt.dibs_line({"dibs": {"state": "measured", "counts": row}})

    def test_the_real_row_that_shipped_wrong_now_adds_up(self):
        line = self.line()
        assert "34" in line and "=" in line
        for n in ("25", "3", "2"):
            assert n in line

    def test_every_nonzero_group_appears(self):
        # ⚠️ The bug was a SILENT omission, so this asserts presence by label.
        line = self.line()
        for label in ("won", "lost", "taken by us", "no swing", "dropped"):
            assert label in line, label

    def test_zero_groups_are_omitted(self):
        line = self.line()
        for label in ("void", "unexplained", "still held"):
            assert label not in line, label

    def test_each_of_the_eleven_can_be_rendered(self):
        # ⚠️ One at a time, so a bucket missing from the formatter's table is
        # caught rather than hidden behind a more common one. ELEVEN cases for
        # eight groups: the grouping is where a column silently vanishes.
        cases = {
            "converted": "won", "attempted": "lost",
            "taken_from": "taken by us", "no_swing": "no swing",
            "released": "dropped", "hopped": "dropped",
            "overridden": "void", "outside": "void",
            "board_reset": "void", "still_open": "still held",
            "unknown": "unexplained",
        }
        for key, label in cases.items():
            zeroed = {k: 0 for k in self.REAL_ROW}
            zeroed["claims"] = 7
            zeroed[key] = 7
            line = fmt.dibs_line({"dibs": {"state": "measured", "counts": zeroed}})
            assert label in line, f"{key} is not rendered"
            assert "=" in line, f"{key} does not audit"

    def test_the_other_axis_is_on_its_OWN_line(self):
        # ⚠️ `swung` overlaps converted/attempted and `took_from_others` is
        # about claims that were never theirs. Mixing them into the same
        # ·-separated run is exactly what made the sum look wrong.
        line = self.line()
        head, _, rest = line.partition("\n")
        assert "taken from others" not in head
        assert "taken from others" in rest and "swung at" in rest

    def test_green_is_named_on_the_other_line_when_it_happened(self):
        line = self.line(green_means_go=4)
        assert "green (fair game)" in line.split("\n")[1]

    def test_a_row_that_does_NOT_add_up_refuses_to_claim_it_does(self):
        # ⚠️ A bucket this formatter does not know about. Printing "=" would
        # assert an audit that does not hold.
        line = self.line(claims=99)
        assert "=" not in line
        assert "of which" in line

    def test_a_member_with_no_claims_but_who_took_one_still_appears(self):
        zeroed = {k: 0 for k in self.REAL_ROW}
        zeroed["took_from_others"] = 3
        line = fmt.dibs_line({"dibs": {"state": "measured", "counts": zeroed}})
        assert "no claims" not in line
        assert "3" in line


    def test_a_lead_override_is_VOID_not_dropped(self):
        # ⚠️ A lead taking a claim off somebody is not that member giving it
        # up. Filing it under "dropped" reads as a choice they did not make.
        zeroed = {k: 0 for k in self.REAL_ROW}
        zeroed["claims"] = 1
        zeroed["overridden"] = 1
        line = fmt.dibs_line({"dibs": {"state": "measured", "counts": zeroed}})
        assert "void" in line and "dropped" not in line

    def test_released_and_hopped_combine_into_one_dropped_figure(self):
        zeroed = {k: 0 for k in self.REAL_ROW}
        zeroed["claims"] = 5
        zeroed["released"] = 2
        zeroed["hopped"] = 3
        line = fmt.dibs_line({"dibs": {"state": "measured", "counts": zeroed}})
        assert "**5** dropped" in line and "=" in line

    def test_unexplained_is_never_folded_into_void(self):
        # The schema is explicit: visibly unexplained rather than quietly
        # bucketed. Hiding it would make a gap in the data look like a result.
        zeroed = {k: 0 for k in self.REAL_ROW}
        zeroed["claims"] = 2
        zeroed["unknown"] = 2
        line = fmt.dibs_line({"dibs": {"state": "measured", "counts": zeroed}})
        assert "unexplained" in line and "void" not in line
