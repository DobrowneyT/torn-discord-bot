"""
Per-faction authorization (#825).

⚠️ Every test here is a test about a REFUSAL. The feature's whole value is the
cases it says no to, and its worst failure mode is looking delivered while
saying yes to everything — so the fail-closed cases matter more than the
happy path.
"""

import pytest

import chain_tenants
import choon_auth as auth

FORGE_GUILD = 111
TNL_GUILD = 222
FORGE_ROLE = 900
TNL_ROLE = 901
ADMIN = 5150


class FakeRole:
    def __init__(self, rid): self.id = rid


class FakeMember:
    """A guild member: has roles."""
    def __init__(self, uid, roles=()): self.id = uid; self.roles = [FakeRole(r) for r in roles]


class FakeUser:
    """⚠️ A plain User, as arrives in a DM — no `roles` attribute at all."""
    def __init__(self, uid): self.id = uid


class FakeInteraction:
    def __init__(self, user, guild_id): self.user = user; self.guild_id = guild_id


@pytest.fixture(autouse=True)
def tenants(tmp_path, monkeypatch):
    """Two factions in two guilds — the shape the whole issue is about."""
    import state
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"), raising=False)
    monkeypatch.setattr(state, "_state", None, raising=False)
    monkeypatch.setenv(auth.ADMIN_ENV, str(ADMIN))
    chain_tenants._save([
        chain_tenants.Tenant("forge", "https://forge.x", FORGE_GUILD, 1, 0, [FORGE_ROLE]).to_dict(),
        chain_tenants.Tenant("tnl", "https://tnl.x", TNL_GUILD, 2, 0, [TNL_ROLE]).to_dict(),
    ])
    yield


def forge_council(): return FakeInteraction(FakeMember(1, [FORGE_ROLE]), FORGE_GUILD)
def admin_here():   return FakeInteraction(FakeMember(ADMIN), FORGE_GUILD)


class TestTheHeadline:
    """The exact scenario #825 was filed for."""

    def test_forge_council_may_change_forge(self):
        assert auth.may_manage(forge_council(), "forge") == (True, None)

    def test_forge_council_may_not_change_tnl(self):
        ok, why = auth.may_manage(forge_council(), "tnl")
        assert not ok and "different Discord server" in why

    def test_forge_council_standing_in_a_forge_channel_still_may_not_touch_tnl(self):
        # ⚠️ THE test. Authority comes from the slug in the argument, never from
        # where the command was typed. This passing while the one above passes
        # is what proves the check is not reading the channel.
        ok, _ = auth.may_manage(FakeInteraction(FakeMember(1, [FORGE_ROLE]), FORGE_GUILD), "tnl")
        assert not ok

    def test_a_bot_admin_may_change_every_faction(self):
        for slug in ("forge", "tnl"):
            assert auth.may_manage(admin_here(), slug) == (True, None), slug

    def test_holding_the_other_factions_role_is_not_enough(self):
        # Right guild, wrong role — the role list is per tenant, not a pool.
        ok, why = auth.may_manage(FakeInteraction(FakeMember(2, [TNL_ROLE]), FORGE_GUILD), "forge")
        assert not ok and str(FORGE_ROLE) in why


class TestFailsClosed:
    def test_a_tenant_with_no_manager_roles_is_admin_only(self):
        # ⚠️ The helper this replaces returned True when unconfigured. If an
        # unconfigured tenant stayed open, every existing tenant would stay open
        # after this shipped and the feature would look done while doing nothing.
        chain_tenants.set_manager_roles("forge", [])
        ok, why = auth.may_manage(forge_council(), "forge")
        assert not ok and "only a bot admin" in why
        assert auth.may_manage(admin_here(), "forge") == (True, None)

    def test_a_tenant_with_no_guild_is_admin_only(self):
        chain_tenants.add("orphan", "https://o.x", 0, 3)
        ok, why = auth.may_manage(forge_council(), "orphan")
        assert not ok and "not bound to a Discord server" in why

    def test_an_unknown_slug_is_refused_by_name(self):
        for check in (auth.may_manage, auth.may_read):
            ok, why = check(forge_council(), "nope")
            assert not ok and "nope" in why

    def test_a_dm_grants_nothing(self):
        # ⚠️ A plain User has no `roles` at all; that must read as "no roles",
        # not raise and not pass.
        ok, _ = auth.may_manage(FakeInteraction(FakeUser(1), None), "forge")
        assert not ok

    def test_no_admins_configured_means_no_admins(self, monkeypatch):
        monkeypatch.delenv(auth.ADMIN_ENV, raising=False)
        assert auth.admin_ids() == []
        assert not auth.is_admin(ADMIN)

    def test_a_typo_in_the_admin_list_grants_nothing(self, monkeypatch):
        # ⚠️ An unparseable entry must not widen access, and must not crash.
        monkeypatch.setenv(auth.ADMIN_ENV, "not-an-id, 77 , ,88")
        assert auth.admin_ids() == [77, 88]


class TestReads:
    def test_any_member_of_the_tenants_guild_may_read_it(self):
        # Decided 2026-09-30: reads are guild-scoped, not role-scoped.
        nobody = FakeInteraction(FakeMember(9, []), FORGE_GUILD)
        assert auth.may_read(nobody, "forge") == (True, None)

    def test_but_not_another_guilds_faction(self):
        # ⚠️ Closes a real gap: /rw-overview shipped with no check at all, so
        # any server member could pull any faction's full war history.
        nobody = FakeInteraction(FakeMember(9, []), FORGE_GUILD)
        assert not auth.may_read(nobody, "tnl")[0]

    def test_an_admin_reads_everything(self):
        assert auth.may_read(admin_here(), "tnl") == (True, None)

    def test_reading_does_not_imply_changing(self):
        nobody = FakeInteraction(FakeMember(9, []), FORGE_GUILD)
        assert auth.may_read(nobody, "forge")[0]
        assert not auth.may_manage(nobody, "forge")[0]


class TestNarrowedPickers:
    """⚠️ A picker offering slugs the caller cannot act on teaches people their
    permissions by refusing them, one keystroke at a time."""

    def test_a_councillor_sees_only_their_own_faction(self):
        assert auth.manageable_slugs(forge_council()) == ["forge"]

    def test_an_admin_sees_every_faction(self):
        assert sorted(auth.manageable_slugs(admin_here())) == ["forge", "tnl"]

    def test_read_pickers_are_wider_than_write_pickers(self):
        nobody = FakeInteraction(FakeMember(9, []), FORGE_GUILD)
        assert auth.readable_slugs(nobody) == ["forge"]
        assert auth.manageable_slugs(nobody) == []


class TestLegacyMigration:
    def test_the_old_global_role_becomes_one_factions_role(self):
        chain_tenants.set_manager_roles("forge", [])
        assert chain_tenants.adopt_legacy_lead_role("forge", 4242) is True
        assert chain_tenants.get("forge").manager_role_ids == [4242]

    def test_it_never_overwrites_roles_somebody_chose(self):
        # ⚠️ Re-applying the old global role over a deliberate choice would
        # re-create the cross-tenant authority this issue removes.
        assert chain_tenants.adopt_legacy_lead_role("forge", 4242) is False
        assert chain_tenants.get("forge").manager_role_ids == [FORGE_ROLE]

    def test_it_touches_only_the_named_tenant(self):
        chain_tenants.set_manager_roles("forge", [])
        chain_tenants.adopt_legacy_lead_role("forge", 4242)
        assert chain_tenants.get("tnl").manager_role_ids == [TNL_ROLE]

    def test_nothing_to_adopt_is_not_an_error(self):
        assert chain_tenants.adopt_legacy_lead_role("forge", 0) is False
        assert chain_tenants.adopt_legacy_lead_role("nope", 1) is False


class TestRolesSurviveAnEdit:
    def test_correcting_a_board_channel_does_not_drop_the_roles(self):
        # ⚠️ `add` doubles as "edit". Dropping roles here would fail the faction
        # closed to admin-only as a side effect of an unrelated correction.
        chain_tenants.add("forge", "https://forge.x", FORGE_GUILD, 99)
        t = chain_tenants.get("forge")
        assert t.board_channel_id == 99
        assert t.manager_role_ids == [FORGE_ROLE]
