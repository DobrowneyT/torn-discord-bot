"""
The poll loop (#785).

⚠️ Every test here uses a faked clock and a fake sender, because the questions
that decide whether this feature survives contact with a channel — "did it ping
twice", "did it go quiet when the chain ended", "did a failed poll blank the
board" — cannot be asked of a live gateway at all.
"""

import asyncio

import pytest

import chain_api
import chain_identity as ci
import chain_ledger
import chain_settings
import chain_tenants
import chain_watcher as cw


HOUR = 3_600_000
NOW = 1_800_000_000_000
TOP = NOW - (NOW % HOUR)


class FakeSender(cw.Sender):
    def __init__(self):
        self.said = []
        self.boards = []
        self.edited = []
        self.deleted = []
        self.next_id = 500
        self.gone = set()          # ids somebody deleted by hand

    async def board(self, channel_id, embeds, message_id):
        self.boards.append({"channel": channel_id, "embeds": embeds,
                            "message_id": message_id})
        if message_id:
            return message_id
        self.next_id += 1
        return self.next_id

    async def say(self, channel_id, content):
        self.next_id += 1
        self.said.append({"channel": channel_id, "content": content,
                          "id": self.next_id})
        return self.next_id

    async def edit(self, channel_id, message_id, content):
        if message_id in self.gone:
            return False
        self.edited.append({"id": message_id, "content": content})
        return True

    async def delete(self, channel_id, message_id):
        self.deleted.append(message_id)


def hour(offset, watchers, bonus=False, slots=2):
    return {"hour_start": TOP + offset * HOUR, "bonus": bonus,
            "slots_per_hour": slots, "watchers": watchers,
            "open_slots": max(0, slots - len(watchers))}


def w(member_id, name, travel=None):
    return {"member_id": member_id, "name": name, "travel": travel}


def payload(hours, **over):
    return {
        "server_now": NOW,
        "gap_horizon_hours": 6,
        "event": {"title": "Halloween 100k Chain", "slots_per_hour": 2,
                  "actually_ended_at": None},
        "chain": {"current": 61_204, "target": 100_000},
        "projection": {"measured": False, "reason": "too-early"},
        "members": [],
        "hours": hours,
        **over,
    }


@pytest.fixture
def forge(monkeypatch):
    chain_tenants.add("forge", "https://forge.monchoon.me", 11, 100, 101)
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "t")
    return chain_tenants.get("forge")


def serve(monkeypatch, *payloads):
    """Answer successive polls; None means the dashboard was unreachable."""
    queue = list(payloads)

    async def _fetch(tenant):
        return queue.pop(0) if queue else (payloads[-1] if payloads else None)

    monkeypatch.setattr(chain_api, "fetch", _fetch)


def run(watcher, tenant, now_ms):
    asyncio.run(watcher.tick(tenant, now_ms))


# ── the board ────────────────────────────────────────────────────────────────

def test_draws_a_board_then_edits_the_same_message(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [w("1", "A"), w("2", "B")])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, NOW)
    run(watcher, forge, NOW + 60_000)
    assert s.boards[0]["message_id"] is None
    # ⚠️ Re-posting instead of editing is what turns a standing board into spam.
    assert s.boards[1]["message_id"] == 501
    assert s.boards[0]["channel"] == 100


def test_a_failed_poll_leaves_the_last_board_up_and_marks_it(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [w("1", "A"), w("2", "B")])]), None)
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, NOW)
    run(watcher, forge, NOW + 60_000)
    # ⚠️ Still drawn — blanking it reads as "nobody is signed up", the one
    # message that must never be wrong — and marked, because an unmarked stale
    # board reads as current.
    assert len(s.boards) == 2
    assert "unreachable" in s.boards[1]["embeds"][0].footer.text


def test_a_failed_first_poll_draws_nothing_rather_than_an_empty_board(monkeypatch, forge):
    serve(monkeypatch, None)
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, NOW)
    assert s.boards == []


def test_nothing_is_pinged_from_a_stale_payload(monkeypatch, forge):
    # ⚠️ The slot may have been dropped minutes ago, and the correction cannot
    # arrive while the poll is still failing.
    serve(monkeypatch, payload([hour(1, [w("1", "A")])]), None)
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)                       # an hour out: no pings yet
    s.said.clear()
    run(watcher, forge, TOP + HOUR - 60_000)       # stale poll, inside the lead
    assert s.said == []


# ── shift pings ──────────────────────────────────────────────────────────────

def test_pings_a_watcher_shortly_before_their_shift_exactly_once(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [w("1", "Goosey"), w("2", "Muttley")])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    at = TOP + HOUR - 4 * 60_000          # inside the 5-minute lead
    run(watcher, forge, at)
    # ⚠️ ONE message naming both, not one each.
    assert len(s.said) == 1
    assert "Goosey" in s.said[0]["content"] and "Muttley" in s.said[0]["content"]
    # ⚠️ The loop re-evaluates the same hour every cycle. Without the ledger
    # this is twelve messages per person per shift.
    run(watcher, forge, at + 60_000)
    assert len(s.said) == 1


def test_the_ledger_survives_a_restart(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [w("1", "Goosey")])]))
    at = TOP + HOUR - 60_000
    run(cw.ChainWatcher(FakeSender()), forge, at)
    # A redeploy: brand new watcher object, same persisted ledger.
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, at)
    assert s.said == []


def test_does_not_ping_for_an_hour_that_has_already_started(monkeypatch, forge):
    serve(monkeypatch, payload([hour(0, [w("1", "Goosey")])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP + 10 * 60_000)
    assert s.said == []


def test_mentions_a_linked_member_and_names_an_unlinked_one(monkeypatch, forge):
    ci.link("1", 4242)
    serve(monkeypatch, payload([hour(1, [w("1", "Goosey"), w("2", "Muttley")])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP + HOUR - 60_000)
    blob = " ".join(m["content"] for m in s.said)
    assert "<@4242>" in blob
    assert "Muttley [2]" in blob


def test_mention_members_off_names_everybody(monkeypatch, forge):
    ci.link("1", 4242)
    chain_settings.set_value("forge", "mention_members", "off")
    serve(monkeypatch, payload([hour(1, [w("1", "Goosey")])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP + HOUR - 60_000)
    assert "<@4242>" not in s.said[0]["content"]
    assert "Goosey [1]" in s.said[0]["content"]


def test_pings_go_to_the_ping_channel(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [w("1", "Goosey")])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP + HOUR - 60_000)
    assert s.said[0]["channel"] == 101


# ── the travel warning ───────────────────────────────────────────────────────

# ⚠️ `slots=1` throughout this section, so the only message that can appear is
# the travel one. With the default two slots a lone watcher also produces a gap
# ping, and "did the travel warning fire" becomes a count nobody can read.
FLYING = {"state": "Traveling", "description": "Traveling to South Africa",
          "destination": "South Africa", "plane_image_type": "airliner"}


def test_warns_a_flyer_an_hour_out_not_five_minutes_out(monkeypatch, forge):
    # ⚠️ The whole point: five minutes' notice is useless to somebody over the
    # Atlantic. Standard airliner to South Africa is 331 minutes, so a departure
    # 30 minutes ago lands long after the shift starts.
    travel = {**FLYING, "departed_at": TOP - 30 * 60_000}
    serve(monkeypatch, payload([hour(1, [w("1", "CalliBee", travel)], slots=1)]))
    s = FakeSender()
    at = TOP + HOUR - 50 * 60_000       # 50 min out: inside flight lead, not shift lead
    run(cw.ChainWatcher(s), forge, at)
    assert len(s.said) == 1
    assert "away" in s.said[0]["content"]
    assert "South Africa" in s.said[0]["content"]


def test_a_flyer_who_will_land_in_time_gets_the_ordinary_ping(monkeypatch, forge):
    # Mexico is 26 minutes standard — departed an hour ago, so long since down.
    travel = {"state": "Traveling", "description": "Traveling to Mexico",
              "destination": "Mexico", "plane_image_type": "airliner",
              "departed_at": TOP - 60 * 60_000}
    serve(monkeypatch, payload([hour(1, [w("1", "CalliBee", travel)], slots=1)]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP + HOUR - 50 * 60_000)
    assert s.said == []          # not yet inside the 5-minute shift lead


def test_somebody_already_abroad_is_warned_early(monkeypatch, forge):
    # ⚠️ They cannot hit anything from overseas — the one finding #774 treats as
    # not a judgment call.
    travel = {"state": "Abroad", "description": "In Switzerland",
              "destination": "Switzerland"}
    serve(monkeypatch, payload([hour(1, [w("1", "CalliBee", travel)], slots=1)]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP + HOUR - 50 * 60_000)
    assert len(s.said) == 1


def test_the_travel_warning_and_the_shift_ping_do_not_suppress_each_other(
        monkeypatch, forge):
    # ⚠️ Different messages that both need to arrive. A ledger key without the
    # kind would let the early warning silence the reminder.
    travel = {**FLYING, "departed_at": TOP - 30 * 60_000}
    serve(monkeypatch, payload([hour(1, [w("1", "CalliBee", travel)], slots=1)]),
          payload([hour(1, [w("1", "CalliBee", None)], slots=1)]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP + HOUR - 50 * 60_000)     # the flight warning
    run(watcher, forge, TOP + HOUR - 60_000)          # landed; the ordinary one
    assert len(s.said) == 2


# ── gap pings ────────────────────────────────────────────────────────────────

def test_announces_a_gap_once_then_once_more_as_a_last_call(monkeypatch, forge):
    serve(monkeypatch, payload([hour(5, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)                      # 5h out: enters the horizon
    assert len(s.said) == 1 and "2 slots" in s.said[0]["content"]

    # ⚠️ Re-checked every five minutes for six hours would be 72 identical
    # messages about the same empty 3am slot.
    for m in range(5, 180, 5):
        run(watcher, forge, TOP + m * 60_000)
    assert len(s.said) == 1

    run(watcher, forge, TOP + 3 * HOUR + 30 * 60_000)   # inside 2h
    assert len(s.said) == 2
    assert "starts in under 2 hours" in s.said[1]["content"]

    for m in range(5, 60, 5):
        run(watcher, forge, TOP + 3 * HOUR + 30 * 60_000 + m * 60_000)
    assert len(s.said) == 2          # and then silence


def test_a_gap_beyond_the_horizon_is_not_announced(monkeypatch, forge):
    serve(monkeypatch, payload([hour(9, [])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert s.said == []


def test_the_horizon_comes_from_the_payload_not_a_constant(monkeypatch, forge):
    # ⚠️ So the channel and the dashboard page cannot disagree about "soon".
    serve(monkeypatch, payload([hour(9, [])], gap_horizon_hours=12))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert len(s.said) == 1


def test_a_half_covered_hour_is_announced_as_one_slot(monkeypatch, forge):
    serve(monkeypatch, payload([hour(5, [w("1", "A")])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert "1 slot" in s.said[0]["content"]


def test_a_covered_hour_says_nothing(monkeypatch, forge):
    serve(monkeypatch, payload([hour(5, [w("1", "A"), w("2", "B")])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert s.said == []


# ── the event ending ─────────────────────────────────────────────────────────

def test_goes_silent_the_moment_the_chain_ends(monkeypatch, forge):
    # ⚠️ The sheet was built for twelve days and the chain finished on day nine.
    # One empty-slot ping for a finished chain at 3am is how a channel decides
    # the bot is not worth reading.
    ended = payload([hour(1, [])])
    ended["event"]["actually_ended_at"] = NOW
    serve(monkeypatch, ended)
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert s.said == [] and s.boards == []


def test_no_event_at_all_says_nothing(monkeypatch, forge):
    serve(monkeypatch, payload([], event=None))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert s.said == [] and s.boards == []


def test_the_ledger_is_cleared_when_an_event_ends(monkeypatch, forge):
    # Otherwise the next event inherits suppressions keyed on hours that will
    # never come round again.
    chain_ledger.mark_sent("forge", "gap:1:first")
    ended = payload([])
    ended["event"]["actually_ended_at"] = NOW
    serve(monkeypatch, ended)
    run(cw.ChainWatcher(FakeSender()), forge, TOP)
    assert not chain_ledger.already_sent("forge", "gap:1:first")


# ── isolation ────────────────────────────────────────────────────────────────

def test_one_faction_blowing_up_does_not_stop_the_others(monkeypatch, forge):
    chain_tenants.add("tnl", "https://tnl.monchoon.me", 22, 200)
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_TNL", "t")

    async def _fetch(tenant):
        if tenant.slug == "forge":
            raise RuntimeError("forge is on fire")
        return payload([hour(1, [w("1", "A"), w("2", "B")])])

    monkeypatch.setattr(chain_api, "fetch", _fetch)
    s = FakeSender()
    asyncio.run(cw.ChainWatcher(s).tick_all(NOW))
    assert [b["channel"] for b in s.boards] == [200]


# ── batching (from the live run: four posts in a row) ────────────────────────

def test_every_gap_this_tick_arrives_as_one_message(monkeypatch, forge):
    # ⚠️ The live first run posted four separate messages, because every hour
    # inside the horizon entered it at once. Four posts in a row is how a
    # channel learns to mute the bot — which costs more than the gaps do.
    serve(monkeypatch, payload([hour(1, []), hour(2, [w("1", "A")]),
                                hour(3, []), hour(4, [])]))
    s = FakeSender()
    run(cw.ChainWatcher(s), forge, TOP)
    assert len(s.said) == 1
    body = s.said[0]["content"]
    assert "4 hours still need cover" in body
    # ⚠️ Assert on the HOURS themselves, not on TCT labels typed by hand — the
    # fixture epoch decides those, and a hardcoded "01:00" is a test that fails
    # for a reason unrelated to batching. (It did, first try.)
    for offset in (1, 2, 3, 4):
        assert f"<t:{(TOP + offset * HOUR) // 1000}:t>" in body


def test_one_gap_still_reads_as_english():
    import chain_formatter as f
    one = f.build_gap_ping([{"hour": hour(1, []), "open_slots": 2, "stage": "first"}])
    assert "1 hour still needs cover" in one


def test_every_line_names_TCT_and_the_header_owns_the_second_time():
    # ⚠️ Each line carries its own unit because a ping gets quoted and
    # screenshotted out of context, and a bare "02:00" beside a second number
    # reads as the reader's own timezone.
    #
    # ⚠️ The second time's zone NAME cannot be printed: Discord renders
    # <t:…:t> client-side and the bot never learns the reader's timezone.
    # Printing the server's would give a member in London a confident, wrong
    # label — so the header says whose clock it is instead.
    import chain_formatter as f
    body = f.build_gap_ping([{"hour": hour(1, []), "open_slots": 1, "stage": "first"}])
    assert "TCT ·" in body
    assert "second time is your own" in body


def test_nothing_is_sent_when_no_gap_is_new(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    run(watcher, forge, TOP + 60_000)
    assert len(s.said) == 1


def test_a_failed_send_leaves_the_gaps_unannounced_rather_than_swallowed(
        monkeypatch, forge):
    # ⚠️ Marking the ledger before the send would lose every gap in a tick whose
    # message failed — and those hours would then never be announced at all.
    serve(monkeypatch, payload([hour(1, []), hour(2, [])]))

    class Failing(FakeSender):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def say(self, channel_id, content):
            if self.fail:
                raise RuntimeError("discord said no")
            await super().say(channel_id, content)

    s = Failing()
    watcher = cw.ChainWatcher(s)
    asyncio.run(watcher.tick_all(TOP))        # tick_all swallows the raise
    assert s.said == []
    s.fail = False
    run(watcher, forge, TOP + 60_000)
    assert len(s.said) == 1 and "2 hours still need cover" in s.said[0]["content"]


# ── remembering what was posted (#785 follow-up) ─────────────────────────────

def test_the_board_survives_a_restart_instead_of_being_re_posted(monkeypatch, forge):
    # ⚠️ The message id lived only in memory, so every redeploy left a dead
    # board above a new one — and redeploys happen most while somebody is
    # tuning the thing and watching the channel.
    serve(monkeypatch, payload([hour(1, [w("1", "A"), w("2", "B")])]))
    s1 = FakeSender()
    run(cw.ChainWatcher(s1), forge, NOW)
    first_id = s1.boards[0]["message_id"]
    assert first_id is None                   # nothing known yet: posts

    s2 = FakeSender()                         # a restart: brand new watcher
    run(cw.ChainWatcher(s2), forge, NOW + 60_000)
    assert s2.boards[0]["message_id"] == 501, "should have edited the old board"


def test_a_gap_ping_is_revised_when_a_slot_fills(monkeypatch, forge):
    serve(monkeypatch,
          payload([hour(3, []), hour(4, [])]),
          payload([hour(3, [w("1", "A"), w("2", "B")]), hour(4, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    assert len(s.said) == 1 and "2 hours still need cover" in s.said[0]["content"]

    run(watcher, forge, TOP + 60_000)
    # ⚠️ Edited, not re-posted. The message is a request; the request was
    # partly met, and an edit notifies nobody.
    assert len(s.said) == 1
    assert len(s.edited) == 1
    assert "1 hour still needs cover" in s.edited[0]["content"]


def test_a_gap_ping_is_removed_once_every_slot_fills(monkeypatch, forge):
    serve(monkeypatch,
          payload([hour(3, [])]),
          payload([hour(3, [w("1", "A"), w("2", "B")])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    posted = s.said[0]["id"]
    run(watcher, forge, TOP + 60_000)
    assert s.deleted == [posted]


def test_a_ping_is_removed_once_its_hour_is_long_past(monkeypatch, forge):
    serve(monkeypatch, payload([hour(1, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    posted = s.said[0]["id"]
    # the hour runs, then the grace hour passes
    run(watcher, forge, TOP + 3 * HOUR + 60_000)
    assert posted in s.deleted


def test_a_shift_ping_is_removed_but_never_revised(monkeypatch, forge):
    # ⚠️ "Your shift starts in five minutes" has no live state to correct; it
    # was true when sent. It is only ever taken down.
    serve(monkeypatch, payload([hour(1, [w("1", "A"), w("2", "B")])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP + HOUR - 60_000)
    assert len(s.said) == 1
    run(watcher, forge, TOP + 3 * HOUR + 60_000)
    assert s.edited == []
    assert len(s.deleted) == 1


def test_cleanup_can_be_switched_off(monkeypatch, forge):
    chain_settings.set_value("forge", "ping_cleanup_hours", "0")
    serve(monkeypatch, payload([hour(1, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    run(watcher, forge, TOP + 5 * HOUR)
    assert s.deleted == []


def test_the_message_is_not_edited_when_nothing_changed(monkeypatch, forge):
    # ⚠️ Editing every tick is a Discord call per message per five minutes, for
    # as long as the event runs, to produce identical text.
    serve(monkeypatch, payload([hour(3, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    for m in (5, 10, 15):
        run(watcher, forge, TOP + m * 60_000)
    assert s.edited == []


def test_a_message_deleted_by_hand_is_forgotten(monkeypatch, forge):
    serve(monkeypatch,
          payload([hour(3, []), hour(4, [])]),
          payload([hour(3, [w("1", "A"), w("2", "B")]), hour(4, [])]),
          payload([hour(3, [w("1", "A"), w("2", "B")]), hour(4, [])]))
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    s.gone.add(s.said[0]["id"])              # somebody deleted it in Discord
    run(watcher, forge, TOP + 60_000)
    run(watcher, forge, TOP + 120_000)
    # ⚠️ One attempt, then dropped — not retried forever against a message
    # that will never exist again.
    assert len(s.edited) == 0


def test_the_pings_come_down_when_the_chain_ends(monkeypatch, forge):
    # ⚠️ A chain that finished on day nine leaves a channel full of "03:00
    # needs 2 slots" about hours that will never happen.
    ended = payload([hour(3, [])])
    ended["event"]["actually_ended_at"] = NOW
    serve(monkeypatch, payload([hour(3, [])]), ended)
    s = FakeSender()
    watcher = cw.ChainWatcher(s)
    run(watcher, forge, TOP)
    posted = s.said[0]["id"]
    run(watcher, forge, TOP + 60_000)
    assert s.deleted == [posted]
