"""
The identity map (#784).

The failure this guards against is not "a link is missing" — that is visible in
`/chain link-status`. It is a link that is WRONG: the bot pings the wrong person
about somebody else's 3am shift, and that person has no way to know the ping was
not for them.
"""

import chain_identity as ci
from chain_identity import GuildMember as GM


ROSTER = [
    {"member_id": "873341", "name": "Goosey"},
    {"member_id": "1712182", "name": "Muttley"},
    {"member_id": "2632966", "name": "CalliBee"},
]


def test_an_id_in_the_nickname_is_taken_exactly():
    links, amb = ci.match(ROSTER, [GM(1, "Goosey [873341]")])
    assert links["873341"]["discord_id"] == 1
    assert links["873341"]["source"] == ci.SOURCE_AUTO
    assert amb == []


def test_the_id_wins_even_when_the_name_is_somebody_elses():
    # ⚠️ The id is exact and the name is a guess. Someone who set their nick to
    # "Muttley [873341]" is Torn 873341 whatever the text says.
    links, _ = ci.match(ROSTER, [GM(1, "Muttley [873341]")])
    assert links["873341"]["discord_id"] == 1
    assert "1712182" not in links


def test_falls_back_to_the_name_when_there_is_no_id():
    links, amb = ci.match(ROSTER, [GM(2, "Muttley")])
    assert links["1712182"]["discord_id"] == 2 and amb == []


def test_matches_through_the_decorations_people_use():
    # ~Goosey~, xX_CalliBee_Xx — ornament, not identity.
    links, _ = ci.match(ROSTER, [GM(3, "~Goosey~"), GM(4, "xX CalliBee Xx")])
    assert links["873341"]["discord_id"] == 3
    assert links["2632966"]["discord_id"] == 4


def test_matches_the_username_when_the_display_name_does_not():
    links, _ = ci.match(ROSTER, [GM(5, display_name="afk til tuesday", name="muttley")])
    assert links["1712182"]["discord_id"] == 5


def test_two_plausible_people_leaves_it_unlinked_and_reports_it():
    # ⚠️ The whole point. Unlinked is visible; wrong is not.
    links, amb = ci.match(ROSTER, [GM(6, "Goosey"), GM(7, "goosey")])
    assert "873341" not in links
    assert amb and amb[0]["member_id"] == "873341"
    assert sorted(amb[0]["candidates"]) == [6, 7]


def test_nobody_matching_simply_stays_unlinked():
    links, amb = ci.match(ROSTER, [GM(8, "SomeoneElse")])
    assert links == {} and amb == []


# ── manual links ─────────────────────────────────────────────────────────────

def test_a_manual_link_survives_a_resync():
    # ⚠️ Otherwise the one correction leadership bothered to make is silently
    # undone on the next restart, which teaches them the command does not work.
    ci.link("873341", 99, name="Goosey")
    links, _ = ci.match(ROSTER, [GM(1, "Goosey [873341]")])
    assert links["873341"]["discord_id"] == 99
    assert links["873341"]["source"] == ci.SOURCE_MANUAL


def test_a_discord_user_claimed_manually_is_not_auto_given_to_someone_else():
    # A leader corrects a bad match; the auto-matcher must not then hand the
    # same Discord user to the member it originally wanted.
    ci.link("2632966", 6, name="CalliBee")
    links, _ = ci.match(ROSTER, [GM(6, "Goosey")])
    assert links["2632966"]["discord_id"] == 6
    assert "873341" not in links


def test_a_stale_auto_link_is_dropped_but_a_manual_one_is_not():
    # ⚠️ A mention that resolves to nobody is silently no notification at all,
    # so an auto-link to somebody who has left the server must go. A manual one
    # is a human's statement and is kept until a human removes it.
    ci.link("873341", 1, source=ci.SOURCE_AUTO)
    ci.link("1712182", 2, source=ci.SOURCE_MANUAL)
    links, _ = ci.match(ROSTER, [], existing=ci.all_links())
    assert "873341" not in links
    assert links["1712182"]["discord_id"] == 2


def test_unlink_and_unlink_discord():
    ci.link("873341", 1)
    ci.link("1712182", 1)
    assert ci.unlink("873341") is True
    assert ci.unlink("873341") is False
    ci.link("873341", 1)
    assert sorted(ci.unlink_discord(1)) == ["1712182", "873341"]
    assert ci.all_links() == {}


# ── the map is standing state, not per-event ─────────────────────────────────

def test_the_map_does_not_depend_on_who_is_signed_up():
    # ⚠️ The explicit worry when this was specified: "will we need to re-run the
    # command every time the signup sheet changes?" No. Nothing here knows the
    # sheet exists, and a link made today resolves a slot claimed next year.
    ci.sync(ROSTER, [GM(1, "Goosey [873341]")])
    assert ci.discord_id_for("873341") == 1
    # a completely different set of watchers, months later
    assert ci.discord_id_for("873341") == 1


def test_sync_reports_what_still_needs_a_human():
    summary = ci.sync(ROSTER, [GM(1, "Goosey [873341]"), GM(6, "x"), GM(7, "x")])
    assert summary["total"] == 3 and summary["linked"] == 1
    assert {u["name"] for u in summary["unlinked"]} == {"Muttley", "CalliBee"}


def test_sync_surfaces_ambiguity_separately_from_simply_missing():
    # They need different actions: one is "pick which", the other is "this
    # person is not on Discord". Collapsing them hides the easy fix.
    summary = ci.sync(ROSTER, [GM(6, "Goosey"), GM(7, "Goosey")])
    assert [a["name"] for a in summary["ambiguous"]] == ["Goosey"]
    assert len(summary["unlinked"]) == 3


def test_decorate_attaches_the_mention_and_leaves_the_rest_alone():
    ci.link("873341", 42)
    out = ci.decorate([{"member_id": "873341", "name": "Goosey"},
                       {"member_id": "1712182", "name": "Muttley"}])
    assert out[0]["discord_id"] == 42
    # ⚠️ No key at all, not a None — the formatter tests truthiness to decide
    # between a mention and `Name [ID]`.
    assert "discord_id" not in out[1]


def test_one_discord_account_never_holds_two_torn_identities():
    # ⚠️ Found by the test above while building this: "Muttley [873341]"
    # normalises to "muttley873341", which CONTAINS "muttley" — so the
    # containment tier handed the same Discord user to Muttley as well as to
    # 873341. One account is one person, and a doubly-linked user gets pinged
    # for somebody else's shifts forever.
    links, _ = ci.match(ROSTER, [GM(1, "Muttley [873341]")])
    holders = [mid for mid, e in links.items() if e["discord_id"] == 1]
    assert holders == ["873341"]


def test_containment_needs_a_long_enough_name():
    # ⚠️ Three characters appear inside half a guild. Without the floor this
    # tier starts inventing links, which is the failure mode that matters.
    links, _ = ci.match([{"member_id": "1", "name": "Bob"}], [GM(2, "Bobcat_Rider")])
    assert links == {}


def test_containment_with_two_hits_is_ambiguous_not_a_coin_flip():
    links, amb = ci.match([{"member_id": "1", "name": "Goosey"}],
                          [GM(2, "xX_Goosey_Xx"), GM(3, "Goosey_TNL")])
    assert links == {} and amb[0]["member_id"] == "1"


def test_an_exact_name_is_not_beaten_by_a_coincidental_substring():
    # Containment only runs when the exact tier found nobody.
    links, _ = ci.match([{"member_id": "1", "name": "Goosey"}],
                        [GM(2, "Goosey"), GM(3, "xX_Goosey_Xx")])
    assert links["1"]["discord_id"] == 2


def test_the_id_claim_does_not_depend_on_roster_order():
    # ⚠️ Found by mutation-testing the previous test, which only passed because
    # Goosey happened to sit before Muttley in the fixture. Process Muttley
    # first and the containment tier takes GM(1) — "muttley" is inside
    # "muttley873341" — and then Goosey's exact id match takes it as well, so
    # one Discord account holds two Torn identities again. The roster arrives
    # sorted by NAME from the dashboard, so this ordering is not hypothetical.
    reversed_roster = list(reversed(ROSTER))
    links, _ = ci.match(reversed_roster, [GM(1, "Muttley [873341]")])
    holders = [mid for mid, e in links.items() if e["discord_id"] == 1]
    assert holders == ["873341"], f"GM(1) claimed by {holders}"
