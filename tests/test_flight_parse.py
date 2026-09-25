"""
Parsing Torn's travel status (shared by the OC watcher and Chain Watch).

⚠️ Every string here is copied from live `/v2/faction/members`. The original
prefixes — "Traveling to X", "Returning from X" — are forms Torn does not send,
so `parse_destination` returned (None, None) for every real traveller. That
silently disabled the OC watcher's Traveling/Abroad severity check as well as
Chain Watch's arrival band: **a check that cannot fire looks exactly like a
check with nothing to report.**
"""

import flight


def test_the_real_outbound_form():
    assert flight.parse_destination("Traveling from Torn to UAE") == ("outbound", "UAE")
    assert flight.parse_destination(
        "Traveling from Torn to South Africa") == ("outbound", "South Africa")


def test_the_real_inbound_form_returns_the_foreign_country():
    # ⚠️ The country, not "Torn" — it is what the flight time is looked up
    # against, and there is no "Torn" row in the table.
    assert flight.parse_destination("Traveling from UAE to Torn") == ("inbound", "UAE")


def test_the_real_abroad_form():
    assert flight.parse_destination("In Switzerland") == ("abroad", "Switzerland")


def test_the_original_prefixes_still_work():
    # Kept because they cost nothing, and a wrong (None, None) here is a
    # warning nobody gets.
    assert flight.parse_destination("Traveling to Mexico") == ("outbound", "Mexico")
    assert flight.parse_destination("Returning from Japan") == ("inbound", "Japan")


def test_nonsense_stays_nonsense():
    for s in (None, "", "Okay", "Hospital", "Traveling"):
        assert flight.parse_destination(s) == (None, None)


def test_every_real_string_resolves_to_a_real_flight_time():
    # ⚠️ The end-to-end check that matters: parse AND lookup, together. Fixing
    # only the parse left "UAE" missing from the table and the symptom
    # unchanged.
    for description in ("Traveling from Torn to UAE",
                        "Traveling from UAE to Torn",
                        "Traveling from Torn to South Africa",
                        "In Switzerland"):
        _, place = flight.parse_destination(description)
        assert flight.one_way_minutes(place, "standard"), description
