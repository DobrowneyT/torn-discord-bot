"""
Wiring the matcher to live Discord (#784).

⚠️ The guild question is the sharp one here. Matching one faction's roster
against another faction's members links the wrong people to the wrong shifts,
and nothing about the result looks wrong until somebody is pinged for a faction
they are not in.
"""

import asyncio

import pytest

import chain_api
import chain_identity as ci
import chain_link_sync as cls
import chain_tenants


class FakeMember:
    def __init__(self, id, display_name, name=None):
        self.id = id
        self.display_name = display_name
        self.name = name or display_name


class FakeGuild:
    def __init__(self, id, members):
        self.id = id
        self.members = members


class FakeClient:
    def __init__(self, guilds):
        self.guilds = guilds
        self.listeners = []

    def add_listener(self, fn, name):
        self.listeners.append((name, fn))


ROSTER = [{"member_id": "873341", "name": "Goosey"}]


@pytest.fixture
def two_factions(monkeypatch):
    chain_tenants.add("forge", "https://forge.monchoon.me", 11, 100)
    chain_tenants.add("tnl", "https://tnl.monchoon.me", 22, 200)
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "a")
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_TNL", "b")


def fake_fetch(by_slug):
    async def _fetch(tenant):
        return by_slug.get(tenant.slug)
    return _fetch


def test_matches_against_the_tenants_own_guild(monkeypatch, two_factions):
    # Goosey is in the TNL guild (22). Syncing FORGE must not link him, even
    # though the bot can see him — he is not in Forge's server.
    client = FakeClient([FakeGuild(11, []), FakeGuild(22, [FakeMember(1, "Goosey")])])
    monkeypatch.setattr(chain_api, "fetch", fake_fetch({"forge": {"members": ROSTER}}))
    summary = asyncio.run(cls.sync_tenant(client, chain_tenants.get("forge")))
    assert summary["linked"] == 0
    assert ci.discord_id_for("873341") is None


def test_links_when_the_member_is_in_the_right_guild(monkeypatch, two_factions):
    client = FakeClient([FakeGuild(11, [FakeMember(1, "Goosey")])])
    monkeypatch.setattr(chain_api, "fetch", fake_fetch({"forge": {"members": ROSTER}}))
    assert asyncio.run(cls.sync_tenant(client, chain_tenants.get("forge")))["linked"] == 1
    assert ci.discord_id_for("873341") == 1


def test_not_being_in_the_guild_is_survivable(monkeypatch, two_factions):
    client = FakeClient([])
    monkeypatch.setattr(chain_api, "fetch", fake_fetch({"forge": {"members": ROSTER}}))
    assert asyncio.run(cls.sync_tenant(client, chain_tenants.get("forge")))["linked"] == 0


def test_one_faction_failing_does_not_stop_the_others(monkeypatch, two_factions):
    # ⚠️ The isolation rule. A dashboard being down is that faction's problem.
    client = FakeClient([FakeGuild(11, [FakeMember(1, "Goosey")]),
                         FakeGuild(22, [FakeMember(2, "Muttley")])])

    async def _fetch(tenant):
        if tenant.slug == "forge":
            raise RuntimeError("forge is on fire")
        return {"members": [{"member_id": "1712182", "name": "Muttley"}]}

    monkeypatch.setattr(chain_api, "fetch", _fetch)
    out = asyncio.run(cls.sync_all(client))
    assert out["forge"] is None
    assert out["tnl"]["linked"] == 1


def test_an_unreachable_dashboard_reports_none_rather_than_an_empty_roster(
        monkeypatch, two_factions):
    # ⚠️ The distinction matters: an empty roster would UNLINK everybody, so a
    # five-minute outage would wipe a map somebody spent an evening correcting.
    ci.link("873341", 1, source=ci.SOURCE_AUTO)
    client = FakeClient([FakeGuild(11, [FakeMember(1, "Goosey")])])
    monkeypatch.setattr(chain_api, "fetch", fake_fetch({}))
    assert asyncio.run(cls.sync_tenant(client, chain_tenants.get("forge"))) is None
    assert ci.discord_id_for("873341") == 1


def test_a_member_who_leaves_the_faction_keeps_their_link(monkeypatch, two_factions):
    # ⚠️ They stop being pinged because they leave the ROSTER. The link stays,
    # because people rejoin and re-linking by hand is the chore the auto-match
    # exists to avoid.
    client = FakeClient([FakeGuild(11, [FakeMember(1, "Goosey")])])
    monkeypatch.setattr(chain_api, "fetch", fake_fetch({"forge": {"members": ROSTER}}))
    asyncio.run(cls.sync_tenant(client, chain_tenants.get("forge")))
    assert ci.discord_id_for("873341") == 1

    monkeypatch.setattr(chain_api, "fetch", fake_fetch({"forge": {"members": []}}))
    summary = asyncio.run(cls.sync_tenant(client, chain_tenants.get("forge")))
    assert summary["total"] == 0
    assert ci.discord_id_for("873341") == 1


def test_a_join_triggers_a_match_for_that_guilds_faction(monkeypatch, two_factions):
    client = FakeClient([FakeGuild(11, [FakeMember(1, "Goosey")])])
    monkeypatch.setattr(chain_api, "fetch", fake_fetch({"forge": {"members": ROSTER}}))
    cls.attach(client)
    assert [n for n, _ in client.listeners] == ["on_member_join"]
    handler = client.listeners[0][1]

    joiner = FakeMember(1, "Goosey")
    joiner.guild = FakeGuild(11, [])
    asyncio.run(handler(joiner))
    assert ci.discord_id_for("873341") == 1


def test_a_join_in_an_unwatched_guild_is_ignored(monkeypatch, two_factions):
    client = FakeClient([])
    called = []

    async def _fetch(tenant):
        called.append(tenant.slug)
        return None

    monkeypatch.setattr(chain_api, "fetch", _fetch)
    cls.attach(client)
    joiner = FakeMember(9, "Somebody")
    joiner.guild = FakeGuild(999, [])
    asyncio.run(client.listeners[0][1](joiner))
    assert called == []


# ── attaching to a REAL client ───────────────────────────────────────────────
#
# ⚠️ These exist because the tests above did NOT catch a live AttributeError:
# `attach` called `client.add_listener(...)`, which is a `commands.Bot` method
# that a plain `discord.Client` does not have. FakeClient had one only because
# I wrote it, so the suite was testing the fake. Anything that touches the
# discord.py API surface gets tested against the real class from here on.

def test_attach_works_on_a_plain_discord_client(monkeypatch, two_factions):
    import discord
    client = discord.Client(intents=discord.Intents.default())
    cls.attach(client)          # must not raise — this is the exact live failure
    assert callable(getattr(client, "on_member_join", None))


def test_attach_works_on_a_commands_bot_too():
    import discord
    from discord.ext import commands
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    cls.attach(bot)
    assert bot.extra_events.get("on_member_join")


def test_attach_chains_the_hosts_own_handler_rather_than_replacing_it(
        monkeypatch, two_factions):
    # ⚠️ discord.Client dispatches by looking up self.on_<event>, so a bare
    # assignment silently deletes a handler the host defined for its own
    # reasons — and nothing would ever report it.
    import discord
    client = discord.Client(intents=discord.Intents.default())
    seen = []

    async def host_handler(member):
        seen.append(member)

    client.on_member_join = host_handler
    cls.attach(client)

    polled = []

    async def _fetch(tenant):
        polled.append(tenant.slug)
        return {"members": ROSTER}

    monkeypatch.setattr(chain_api, "fetch", _fetch)

    joiner = FakeMember(1, "Goosey")
    joiner.guild = FakeGuild(11, [])
    asyncio.run(client.on_member_join(joiner))

    assert seen == [joiner], "the host's own handler must still run"
    # ⚠️ Asserting ours RAN, not that it linked anybody: a real, unconnected
    # discord.Client has no guilds, so there is nothing to match against. The
    # first draft of this test asserted a link and failed for that reason —
    # which is the code being right and the expectation being wrong.
    assert polled == ["forge"], "and ours must run too"
