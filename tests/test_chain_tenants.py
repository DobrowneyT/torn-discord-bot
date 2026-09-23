"""
The tenant registry (#783).

Five factions, one bot. The requirement that matters most here is not that
adding a faction works — it is that a faction going wrong stays that faction's
problem. One dashboard being down must not silence the other four.
"""

import chain_tenants as ct


def add_forge(**over):
    args = dict(slug="forge", base_url="https://forge.monchoon.me", guild_id=7,
                board_channel_id=100, ping_channel_id=101)
    args.update(over)
    return ct.add(**args)


def test_add_then_read_back():
    ok, _ = add_forge()
    assert ok
    t = ct.get("forge")
    assert t.url == "https://forge.monchoon.me/api/internal/chain-watch"
    assert t.guild_id == 7 and t.board_channel_id == 100 and t.pings_to == 101


def test_pings_fall_back_to_the_board_channel():
    add_forge(ping_channel_id=0)
    assert ct.get("forge").pings_to == 100


def test_adding_the_same_slug_updates_rather_than_duplicating():
    add_forge()
    add_forge(board_channel_id=200)
    assert len([t for t in ct.all_tenants() if t.slug == "forge"]) == 1
    assert ct.get("forge").board_channel_id == 200


def test_a_trailing_slash_does_not_become_a_double_slash():
    add_forge(base_url="https://forge.monchoon.me/")
    assert "//api" not in ct.get("forge").url


def test_http_is_refused():
    # ⚠️ The bearer goes out on every poll. Over http it is readable by anything
    # on the path, five times a minute, for as long as the bot runs.
    ok, msg = add_forge(base_url="http://forge.monchoon.me")
    assert not ok and "https" in msg


def test_a_bad_slug_is_refused():
    # ⚠️ The slug is interpolated into an env-var name and appended to a URL, so
    # it is an injection boundary, not a label. `forge/../tnl` is the case worth
    # naming: it would otherwise point one faction's poll at another's endpoint.
    for bad in ("for ge", "forge/../tnl", "", "forge?x=1"):
        ok, _ = add_forge(slug=bad)
        assert not ok, bad


def test_a_typed_slug_is_normalised_rather_than_refused():
    # A leader typing "Forge" means forge. Rejecting it would be pedantry; what
    # matters is that it cannot become a second, separate tenant.
    ok, _ = add_forge(slug="  Forge ")
    assert ok
    assert [t.slug for t in ct.all_tenants()] == ["forge"]


def test_the_token_comes_from_the_environment_per_faction(monkeypatch):
    # ⚠️ Never from a slash command: command arguments are visible client-side
    # and land in logs, and a tenant bearer in a channel is a leaked credential.
    add_forge()
    t = ct.get("forge")
    assert t.token_env == "CHAIN_WATCH_TOKEN_FORGE"
    assert t.token() is None
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "abc")
    assert t.token() == "abc"


def test_a_hyphenated_slug_maps_to_a_legal_env_name():
    add_forge(slug="big-chain")
    assert ct.get("big-chain").token_env == "CHAIN_WATCH_TOKEN_BIG_CHAIN"


def test_adding_a_tenant_without_a_token_says_so():
    ok, msg = add_forge()
    assert ok and "CHAIN_WATCH_TOKEN_FORGE" in msg


def test_one_malformed_entry_does_not_take_the_others_offline(monkeypatch):
    # ⚠️ The whole point of per-tenant isolation. A hand-edited state.json with
    # one broken row must not stop four healthy factions being polled.
    add_forge()
    ct._save(ct._store() + [{"nonsense": True}])
    slugs = [t.slug for t in ct.all_tenants()]
    assert slugs == ["forge"]


def test_remove():
    add_forge()
    ok, _ = ct.remove("forge")
    assert ok and ct.get("forge") is None
    assert ct.remove("forge")[0] is False
