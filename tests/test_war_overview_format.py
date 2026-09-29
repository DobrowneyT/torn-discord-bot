"""
Rendering a war's overview for Discord (#811).

⚠️ Nothing is COUNTED here — every number arrives already computed by the
dashboard, which runs the same summary the page does and is held level with it
by a parity test on that side. So a disagreement between Discord and the page
can only be a rendering bug, and these tests cover exactly that.
"""

import war_overview_format as fmt

PAYLOAD = {
    "mode": "faction",
    "warring_only": False,
    "war": {"id": 49621, "opponent_id": 20747, "opponent_name": "Damage Inc",
            "start_at": 1790449200, "end_at": 1790622540,
            "our_score": 32681, "opponent_score": 34576, "is_termed": False},
    "window": {"from_ms": 1790449200000, "to_ms": 1790622540000},
    "summary": {
        "outgoing": {"attack": 148, "assist": 67, "loss": 26, "total": 241},
        "incoming": {"attack": 105, "assist": 84, "loss": 48, "total": 237},
        "revives": {"success": 44, "failure": 35, "total": 79},
    },
    "members": [{"id": 2250591, "name": "MonChoon_616"}, {"id": 2932241, "name": "Marnix0126"}],
    "wars": [{"id": 49621, "opponent_id": 20747, "opponent_name": "Damage Inc",
              "start_at": 1790449200, "end_at": 1790622540}],
}


def test_the_faction_header_names_the_opponent():
    assert fmt.header(PAYLOAD) == "vs Damage Inc [20747]"


def test_the_member_header_names_both():
    # ⚠️ The member's name alone answers nothing weeks later, which is exactly
    # when these get read — the page learned the same lesson.
    p = {**PAYLOAD, "mode": "member", "member_id": 2932241, "member_name": "Marnix0126"}
    assert fmt.header(p) == "Marnix0126 [2932241] vs Damage Inc [20747]"


def test_the_three_rows_are_outgoing_incoming_revives():
    # ⚠️ The page's order, and the order of the three chart panels. Somebody
    # reading both should see the same shape.
    lines = fmt.summary_lines(PAYLOAD)
    assert "Outgoing" in lines[0] and "Incoming" in lines[1] and "Revives" in lines[2]


def test_the_numbers_are_passed_through_verbatim():
    body = fmt.build_overview(PAYLOAD)["description"]
    for n in ("148", "67", "26", "241", "105", "84", "48", "237", "44", "35", "79"):
        assert n in body


def test_revives_read_success_fail_total():
    assert "Success" in fmt.summary_lines(PAYLOAD)[2]
    assert "Fail" in fmt.summary_lines(PAYLOAD)[2]


def test_missing_numbers_render_as_zero_not_as_a_crash():
    # ⚠️ A partial payload is what a half-provisioned tenant returns. An embed
    # that says 0 is legible; a traceback in a command handler is not.
    assert fmt.summary_lines({})[0].count("0") >= 4
    assert fmt.build_overview({})["title"]


def test_the_window_uses_discord_timestamps_in_seconds():
    # ⚠️ <t:…> takes SECONDS; the payload speaks milliseconds. Dividing in the
    # wrong place puts the war in 1970, which reads as a data problem.
    line = fmt.window_line(PAYLOAD)
    assert "<t:1790449200:f>" in line and "<t:1790622540:f>" in line


def test_a_live_war_says_so():
    p = {**PAYLOAD, "war": {**PAYLOAD["war"], "end_at": None}}
    assert "still running" in fmt.window_line(p)


def test_the_scope_line_distinguishes_a_filtered_embed():
    # ⚠️ Without it a filtered embed is indistinguishable from a full one, and
    # somebody quotes the wrong number in a post-mortem.
    assert "every" in fmt.scope_line(PAYLOAD).lower()
    assert "only" in fmt.scope_line({**PAYLOAD, "warring_only": True}).lower()


def test_the_score_line_prints_termed_without_acting_on_it():
    # ⚠️ is_termed is set BY HAND on the dashboard, so an untoggled real war
    # looks exactly like a termed one. Print it; never decide from it.
    assert "termed" not in (fmt.score_line(PAYLOAD) or "")
    termed = fmt.score_line({**PAYLOAD, "war": {**PAYLOAD["war"], "is_termed": True}})
    assert "termed" in termed


def test_no_score_line_when_there_is_no_score():
    assert fmt.score_line({"war": {}}) is None


def test_autocomplete_filters_and_caps_at_25():
    # ⚠️ Discord rejects more than 25 and the rejection is SILENT from the
    # user's side — the box just shows nothing.
    many = {"members": [{"id": i, "name": f"Player{i}"} for i in range(60)]}
    assert len(fmt.member_choices(many)) == 25
    assert fmt.member_choices(PAYLOAD, "marn")[0][1] == "2932241"
    assert fmt.member_choices(PAYLOAD, "nobody") == []


def test_war_choices_label_a_live_war():
    live = {"wars": [{"id": 1, "opponent_name": "Foes", "opponent_id": 9, "start_at": 1790449200, "end_at": None}]}
    assert "LIVE" in fmt.war_choices(live)[0][0]


def test_an_unnamed_opponent_is_still_labelled_honestly():
    assert fmt.war_label({}) == "vs an unnamed opponent"
    assert fmt.war_label({"opponent_id": 7}) == "vs faction 7"


class TestFooter:
    """⚠️ The footer is where the bar width is stated. A chart whose bars are
    an hour wide, sitting under a footer that says nothing, cannot be compared
    with the same war on the page — which is the whole reason `bins` exists."""

    def _payload(self, **over):
        p = {"mode": "faction", "war": {}, "summary": {}, "chart": {"panels": []},
             "bin": {"seconds": 3600, "auto": True}}
        p.update(over)
        return p

    def test_it_names_the_width_and_marks_the_automatic_one(self):
        assert fmt.footer(self._payload()).endswith("bars 1h (auto)")

    def test_a_width_picked_by_hand_is_not_marked_auto(self):
        f = fmt.footer(self._payload(bin={"seconds": 300, "auto": False}))
        assert f.endswith("bars 5m")
        assert "auto" not in f

    def test_summary_only_says_nothing_about_bars(self):
        # ⚠️ There is no chart, so describing its bars is noise.
        f = fmt.footer(self._payload(chart=None))
        assert "bars" not in f
        assert "War Overview page" in f

    def test_an_older_dashboard_without_a_bin_field_still_gets_a_footer(self):
        # ⚠️ The bot deploys on its own schedule; it will run against a
        # dashboard that predates this field. Missing must degrade, not crash.
        f = fmt.footer(self._payload(bin=None))
        assert f == "Same figures as the War Overview page"
        assert fmt.build_overview(self._payload(bin=None))["footer"] == f
