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


def test_adding_a_tenant_with_no_obtainable_token_says_so(monkeypatch):
    # ⚠️ The warning still exists, but it now asks whether a token can be
    # OBTAINED rather than whether an env var is set. An absent variable is the
    # normal state now — the control plane mints these on demand.
    import choon_registry
    monkeypatch.setattr(choon_registry, "token_for", lambda slug, field: None)
    monkeypatch.delenv("CHAIN_WATCH_TOKEN_FORGE", raising=False)
    ok, msg = ct.add("forge", "https://forge.monchoon.me", 1, 10)
    assert ok
    assert "will not poll" in msg
    # ⚠️ Points at the half that is actually broken. The old message sent
    # somebody to the bot's environment, which is no longer where this lives.
    assert "FLEET_METRICS_SECRET" in msg


def test_adding_a_tenant_the_fleet_can_mint_for_says_nothing(monkeypatch):
    # ⚠️ The whole point: no warning, no SSH, nothing to do.
    import choon_registry
    monkeypatch.setattr(choon_registry, "token_for", lambda slug, field: "a" * 64)
    ok, msg = ct.add("forge", "https://forge.monchoon.me", 1, 10)
    assert ok and "⚠️" not in msg


def test_falling_back_to_the_environment_is_mentioned_but_not_alarming(monkeypatch):
    # ⚠️ It works, so no warning triangle — but somebody removing that variable
    # later should be able to find out it was load-bearing.
    import choon_registry
    monkeypatch.setattr(choon_registry, "token_for", lambda slug, field: None)
    monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "b" * 64)
    ok, msg = ct.add("forge", "https://forge.monchoon.me", 1, 10)
    assert ok and "⚠️" not in msg
    assert "CHAIN_WATCH_TOKEN_FORGE" in msg

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


def test_the_sign_up_url_is_per_tenant():
    # ⚠️ Every faction has its own host. A single hardcoded link would send
    # four factions to a fifth's sheet, where they would see somebody else's
    # roster and none of their own slots.
    add_forge()
    ct.add("tnl", "https://tnl.monchoon.me", 2, 20)
    assert ct.get("forge").sign_up_url == "https://forge.monchoon.me/members/chain-watch"
    assert ct.get("tnl").sign_up_url == "https://tnl.monchoon.me/members/chain-watch"


def test_the_sign_up_url_survives_a_trailing_slash():
    add_forge(base_url="https://forge.monchoon.me/")
    assert "//members" not in ct.get("forge").sign_up_url


class TestWhereTheTokenComesFrom:
    """
    ⚠️ This class exists because a mutation survived: deleting the fleet lookup
    from `Tenant.token()` entirely broke nothing. Every test used the
    environment, so the feature could have been silently absent while the suite
    stayed green.
    """

    def _tenant(self):
        return ct.Tenant("forge", "https://forge.monchoon.me", 1, 10)

    def test_the_fleet_supplies_it(self, monkeypatch):
        import choon_registry
        monkeypatch.delenv("CHAIN_WATCH_TOKEN_FORGE", raising=False)
        monkeypatch.setattr(choon_registry, "token_for",
                            lambda slug, field: "fleet" if field == "chain_watch_token" else None)
        assert self._tenant().token() == "fleet"

    def test_the_fleet_WINS_over_the_environment(self, monkeypatch):
        # ⚠️ The ordering that matters. With the environment first, a stale
        # pasted value would outlive a rotation of the fleet secret and the
        # faction would 401 until somebody remembered the file.
        import choon_registry
        monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "stale-env")
        monkeypatch.setattr(choon_registry, "token_for", lambda slug, field: "fresh-fleet")
        assert self._tenant().token() == "fresh-fleet"

    def test_the_environment_is_the_fallback(self, monkeypatch):
        # ⚠️ What keeps a running board running when the control plane is down.
        import choon_registry
        monkeypatch.setattr(choon_registry, "token_for", lambda slug, field: None)
        monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", "env-value")
        assert self._tenant().token() == "env-value"

    def test_neither_is_none_rather_than_empty(self, monkeypatch):
        import choon_registry
        monkeypatch.setattr(choon_registry, "token_for", lambda slug, field: None)
        monkeypatch.delenv("CHAIN_WATCH_TOKEN_FORGE", raising=False)
        assert self._tenant().token() is None

    def test_it_asks_for_ITS_OWN_scope(self, monkeypatch):
        # ⚠️ Asking for war_overview_token here would hand the board a
        # credential for the wrong feature — a 401 whose cause is invisible.
        import choon_registry
        seen = []
        monkeypatch.setattr(choon_registry, "token_for",
                            lambda slug, field: seen.append((slug, field)) or "x")
        self._tenant().token()
        assert seen == [("forge", "chain_watch_token")]
