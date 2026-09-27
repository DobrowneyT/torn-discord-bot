"""
Turning raw travel facts into a landing band (#785).

⚠️ The dashboard deliberately does not answer this: Torn publishes no arrival
time for a traveller, and the flight-duration data lives here. What these tests
defend is the refusal to guess — a confidently wrong landing time is worse than
an absent one, because a lead waits on it.
"""

import chain_eta as eta
import flight

MIN = 60_000
T = 1_800_000_000_000


def travel(**over):
    base = {"state": "Traveling", "description": "Traveling to South Africa",
            "destination": "South Africa", "plane_image_type": "airliner",
            "departed_at": T}
    base.update(over)
    return base


def test_a_band_not_a_point():
    band = eta.arrival_band(travel())
    assert band["latest"] > band["earliest"]
    # ⚠️ Read from flight_info.json rather than typed in. Torn changes flight
    # times, and a hardcoded number here would fail for a reason that has
    # nothing to do with this module. (It was typed in first, and was wrong.)
    one_way = flight.one_way_minutes("South Africa", "standard")
    assert band["earliest"] < T + one_way * MIN < band["latest"]


def test_no_witnessed_departure_means_no_answer():
    # ⚠️ latchDeparture leaves this null after a sampling gap. Counting from
    # `now` instead would invent a landing time a lead then waits on.
    assert eta.arrival_band(travel(departed_at=None)) is None
    assert eta.arrival_band(travel(departed_at=0)) is None


def test_somebody_not_in_the_air_has_no_band():
    assert eta.arrival_band(travel(state="Abroad")) is None
    assert eta.arrival_band(travel(state="Okay")) is None
    assert eta.arrival_band(None) is None


def test_an_unknown_destination_is_not_guessed_at():
    assert eta.arrival_band(travel(description="Traveling to Atlantis",
                                   destination="Atlantis")) is None


def test_a_return_flight_times_the_country_being_left():
    # ⚠️ The dashboard reports destination "Torn" for an inbound leg — right for
    # saying where somebody is headed, useless for timing it, because there is
    # no "Torn" row in flight_info.json.
    band = eta.arrival_band(travel(description="Returning to Torn from Mexico",
                                   destination="Torn"))
    assert band is not None
    one_way = flight.one_way_minutes("Mexico", "standard")
    assert band["earliest"] < T + one_way * MIN < band["latest"]


def test_the_plane_changes_the_time():
    slow = eta.arrival_band(travel(plane_image_type="airliner"))
    fast = eta.arrival_band(travel(plane_image_type="private_jet"))
    assert fast["latest"] < slow["earliest"]


def test_an_unknown_plane_assumes_the_slowest():
    # Biasing toward over-warning, the same choice flight.py already makes.
    unknown = eta.arrival_band(travel(plane_image_type="zeppelin"))
    standard = eta.arrival_band(travel(plane_image_type="airliner"))
    assert unknown == standard


# ── may_miss ─────────────────────────────────────────────────────────────────

def test_warns_when_the_latest_landing_is_after_the_shift():
    # ⚠️ Judged on the LATEST end, not the middle. Half a band of slack is
    # exactly the margin that turns an actionable warning into a missed shift.
    assert eta.may_miss(travel(), T + 60 * MIN) is True


def test_stays_quiet_when_they_will_certainly_be_down():
    mexico = flight.one_way_minutes("Mexico", "standard")
    assert eta.may_miss(travel(description="Traveling to Mexico",
                               destination="Mexico"), T + (mexico + 60) * MIN) is False


def test_already_abroad_always_warns():
    # They cannot hit anything from overseas, band or no band.
    assert eta.may_miss(travel(state="Abroad"), T + 999 * MIN) is True


def test_in_the_air_with_no_computable_arrival_still_warns():
    # ⚠️ Otherwise the least-known cases are the quietest ones.
    assert eta.may_miss(travel(departed_at=None), T + 999 * MIN) is True
    assert eta.may_miss(travel(description="Traveling to Atlantis",
                               destination="Atlantis"), T + 999 * MIN) is True


def test_somebody_at_home_is_never_warned():
    assert eta.may_miss(None, T) is False
    assert eta.may_miss(travel(state="Okay"), T) is False


# ── Torn's real travel strings (from live /v2/faction/members) ───────────────
#
# ⚠️ These four are copied from MonChoon's faction, not invented. The parser was
# written against "Traveling to X", which Torn never sends, so every one of
# them resolved to nothing — the board said "somewhere" and the band said
# "arrival unknown", both of which read as missing data rather than a bug.

def test_the_real_outbound_string_resolves():
    band = eta.arrival_band(travel(description="Traveling from Torn to South Africa",
                                   destination="South Africa"))
    assert band is not None


def test_the_real_inbound_string_times_the_country_being_left():
    # ⚠️ "Traveling from UAE to Torn": the dashboard reports destination "Torn",
    # which has no row in flight_info.json. The COUNTRY is what the flight time
    # is looked up against.
    band = eta.arrival_band(travel(description="Traveling from UAE to Torn",
                                   destination="Torn"))
    assert band is not None
    one_way = flight.one_way_minutes("United Arab Emirates", "standard")
    assert band["earliest"] < T + one_way * MIN < band["latest"]


def test_torns_abbreviation_resolves_to_the_tables_name():
    # ⚠️ Torn says "UAE"; flight_info.json says "United Arab Emirates". Without
    # the alias the parse succeeds and the LOOKUP misses — same symptom, one
    # layer deeper.
    assert flight.canonical_destination("UAE") == "United Arab Emirates"
    assert flight.canonical_destination("South Africa") == "South Africa"
    assert flight.canonical_destination("Atlantis") is None


def test_an_abroad_member_is_still_read_correctly():
    assert eta.may_miss(travel(state="Abroad", description="In Switzerland",
                               destination="Switzerland"), T + 999 * MIN) is True
