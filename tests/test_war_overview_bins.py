"""
The bin-size argument (#811 follow-up).

⚠️ The point of this parameter is that a chart in Discord and the same war on
the page can be made to agree. A parser that quietly turns an unrecognised
value into `auto` would defeat that silently, which is why the refusal cases
matter more here than the happy ones.
"""

import pytest

import war_overview_bins as bins


class TestParse:
    def test_nothing_and_auto_mean_the_automatic_width(self):
        for raw in (None, "", "   ", "auto", "AUTO", " Auto "):
            seconds, err = bins.parse(raw)
            assert (seconds, err) == (None, None), raw

    def test_a_bare_number_is_seconds(self):
        assert bins.parse("300") == (300, None)

    def test_the_unit_suffixes(self):
        assert bins.parse("90s") == (90, None)
        assert bins.parse("5m") == (300, None)
        assert bins.parse("2h") == (7200, None)
        assert bins.parse("1d") == (86400, None)

    def test_the_same_width_written_three_ways(self):
        # ⚠️ `5m`, `300` and `300s` must reach the endpoint identically, or two
        # people comparing charts find they disagree over spelling.
        assert bins.parse("5m")[0] == bins.parse("300")[0] == bins.parse("300s")[0]

    def test_case_and_padding_do_not_matter(self):
        assert bins.parse("  2H  ") == (7200, None)

    @pytest.mark.parametrize("raw", ["five", "5x", "-60", "1.5m", "1,5", "∞", "12٣", "1_0", "+60", ""])
    def test_a_value_that_is_not_a_width_is_refused_not_defaulted(self, raw):
        if raw == "":
            pytest.skip("empty is auto, covered above")
        seconds, err = bins.parse(raw)
        assert seconds is None
        assert err, f"{raw!r} silently became auto"

    def test_zero_is_refused(self):
        assert bins.parse("0")[1]
        assert bins.parse("0m")[1]

    def test_the_refusal_says_what_to_type_instead(self):
        # ⚠️ "invalid" sends somebody back to guessing at the one thing this
        # parameter was added to stop them guessing at.
        err = bins.parse("banana")[1]
        assert "90s" in err and "2h" in err


class TestLabel:
    def test_it_prefers_the_largest_whole_unit(self):
        assert bins.label(86400) == "1d"
        assert bins.label(21600) == "6h"
        assert bins.label(3600) == "1h"
        assert bins.label(300) == "5m"
        assert bins.label(90) == "90s"
        assert bins.label(None) == "auto"

    def test_it_round_trips_through_parse(self):
        for seconds in (60, 300, 3600, 21600, 86400, 90, 137):
            assert bins.parse(bins.label(seconds))[0] == seconds


class TestChoices:
    def test_it_offers_the_same_widths_the_page_does(self):
        values = [v for _, v in bins.choices("")]
        # Auto / 1m / 5m / 1h / 6h / 1d — the page's Bin size control.
        assert values == ["auto", "60", "300", "3600", "21600", "86400"]

    def test_auto_comes_first_when_nothing_is_typed(self):
        assert bins.choices("")[0][1] == "auto"

    def test_a_typed_custom_width_is_offered_back_resolved(self):
        # ⚠️ Discord allows free text on an autocompleted parameter, so without
        # this somebody types `2h` into a box that shows them nothing.
        top = bins.choices("2h")[0]
        assert top == ("2h (custom)", "7200")

    def test_a_typed_preset_is_not_duplicated(self):
        # `1h` is already in the list; offering it twice looks broken.
        assert [v for _, v in bins.choices("1h")].count("3600") == 1

    def test_nonsense_narrows_to_nothing_rather_than_inventing_a_width(self):
        assert all("custom" not in name for name, _ in bins.choices("banana"))

    def test_it_never_exceeds_discords_limit(self):
        assert len(bins.choices("")) <= 25
