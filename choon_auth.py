"""
Who may act on which faction (#825).

The bot began with one global leadership role, `CHAIN_LEAD_ROLE_ID`, checked on
every write. That was right while it served one faction and became wrong the
moment it served five: **a holder of forge's council role could move TNL's
board.** Authority here is per tenant.

⚠️ **Discord cannot express this.** `default_member_permissions` is per command
and server-wide — it can require Manage Server, never "forge council governs
forge" — and it is only a *default*, overridable per role under Integrations. It
is a way to keep a command out of the picker, not a boundary. The boundary is
here.

⚠️ **Authority is keyed on the SLUG BEING ACTED ON, not on where the command was
typed.** A forge councillor standing in a forge channel running
`/chain channel faction:tnl` must be refused. Deriving authority from the
channel is the same bug in a subtler costume, and it is the one this module
exists to prevent.

⚠️ **Fail closed.** A tenant with no manager roles configured is bot-admin-only.
The helper this replaces did the opposite — `lead_role_id == 0` meant everybody
was a lead — and that default is why it is called out: if an unconfigured tenant
stayed open, every existing tenant would stay open after this shipped and the
feature would look delivered while doing nothing, which is how #645's "not
measured" read as healthy to a human.
"""

import logging
import os
from typing import List, Optional, Tuple

import chain_tenants

log = logging.getLogger("choon_auth")

#: Bot admins, comma-separated Discord user ids.
ADMIN_ENV = "CHOON_ADMIN_USER_IDS"


def admin_ids() -> List[int]:
    """
    ⚠️ **From the environment, never from a slash command.** An admin list
    editable by a command is only as strong as the gate on that command, which
    is circular — bootstrapping an authority list from inside the thing it
    authorizes is a privilege-escalation path. The tenant tokens follow the same
    rule for the same reason.

    ⚠️ Read on every call, never cached at import, so revoking an admin takes
    effect on the next restart without anybody remembering this module.
    """
    out = []
    for part in (os.environ.get(ADMIN_ENV) or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not part.isascii():
            # ⚠️ Logged and skipped rather than raising. A typo in this variable
            # must not be able to take the bot down, and must not be able to
            # widen access either — an unparseable entry grants nothing.
            log.warning("ignoring non-numeric entry in %s: %r", ADMIN_ENV, part)
            continue
        out.append(int(part))
    return out


def is_admin(user_id: Optional[int]) -> bool:
    return user_id is not None and int(user_id) in admin_ids()


def _user_of(interaction):
    """
    ⚠️ Tolerates a missing interaction and returns None.

    Not defensive clutter: these helpers run inside autocomplete callbacks, and
    **a raising autocomplete is invisible** — Discord shows an empty picker with
    no error anywhere the user can see it. Failing closed to "no authority" is
    the only behaviour that is both safe and diagnosable.
    """
    return getattr(interaction, "user", None) if interaction is not None else None


def _role_ids(user) -> List[int]:
    """The caller's role ids, or [] when Discord gave us a User not a Member.

    ⚠️ A `User` (no roles) must read as "no roles", never as an error and never
    as a pass. It is what arrives in a DM, where no tenant's roles apply.
    """
    return [int(r.id) for r in getattr(user, "roles", []) or []]


def may_read(interaction, slug: str) -> Tuple[bool, Optional[str]]:
    """
    Whether the caller may READ this faction's data. `(ok, refusal)`.

    Guild-scoped, not role-scoped: any member of the guild a tenant is bound to
    may read it, and a bot admin may read everything.

    ⚠️ This gate is new rather than loosened. `/rw-overview` shipped with no
    check at all, so any member of the server could pull any configured
    faction's full member-by-member war history.
    """
    tenant = chain_tenants.get(slug)
    if tenant is None:
        return False, f"No faction called `{slug}`."
    if is_admin(getattr(_user_of(interaction), "id", None)):
        return True, None
    return _same_guild(interaction, tenant)


def may_manage(interaction, slug: str) -> Tuple[bool, Optional[str]]:
    """
    Whether the caller may CHANGE this faction's settings. `(ok, refusal)`.

    Admin → yes for every slug. Otherwise the caller must be in the tenant's own
    guild AND hold one of its manager roles.
    """
    tenant = chain_tenants.get(slug)
    if tenant is None:
        return False, f"No faction called `{slug}`."
    if is_admin(getattr(_user_of(interaction), "id", None)):
        return True, None

    ok, refusal = _same_guild(interaction, tenant)
    if not ok:
        return False, refusal

    wanted = tenant.manager_role_ids
    if not wanted:
        # ⚠️ Fail closed — see the module note.
        return False, (f"`{slug}` has no manager roles configured, so only a bot admin "
                       f"can change it. Set one with `/chain tenant role`.")
    if any(rid in wanted for rid in _role_ids(_user_of(interaction))):
        return True, None
    names = ", ".join(f"<@&{r}>" for r in wanted)
    return False, f"Changing `{slug}` needs one of these roles: {names}."


def _same_guild(interaction, tenant) -> Tuple[bool, Optional[str]]:
    """
    ⚠️ `guild_id == 0` on a tenant means it predates the field being recorded.
    Treated as "no guild matches", not "every guild matches" — the same
    fail-closed rule. It is fixed by re-running `/chain tenant add` in the right
    server, which is what the refusal says.
    """
    if not tenant.guild_id:
        return False, (f"`{tenant.slug}` is not bound to a Discord server yet, so only a "
                       f"bot admin can act on it. Re-add it with `/chain tenant add` "
                       f"in the server it belongs to.")
    if int(getattr(interaction, "guild_id", 0) or 0) != tenant.guild_id:
        return False, f"`{tenant.slug}` belongs to a different Discord server."
    return True, None


def readable_slugs(interaction) -> List[str]:
    """
    Which slugs this caller may read — for narrowing an autocomplete.

    ⚠️ A picker that offers slugs the caller cannot act on teaches people their
    permissions by refusing them, one keystroke at a time.
    """
    return [t.slug for t in chain_tenants.all_tenants()
            if may_read(interaction, t.slug)[0]]


def manageable_slugs(interaction) -> List[str]:
    """Which slugs this caller may change — for narrowing an autocomplete."""
    return [t.slug for t in chain_tenants.all_tenants()
            if may_manage(interaction, t.slug)[0]]


def may_manage_any(interaction) -> Tuple[bool, Optional[str]]:
    """
    Whether the caller manages ANY faction in this guild. `(ok, refusal)`.

    ⚠️ **Deliberately coarser than `may_manage`, and only for the identity
    commands.** `/chain link` and `/chain unlink` write the Discord↔Torn map,
    which is bot-wide rather than per faction — there is no slug to key on, so
    there is no per-faction answer to give. A manager of any faction in this
    guild may edit it.

    ⚠️ This is a known limit, not an oversight: the map itself would have to
    become per-faction before the gate could. Recorded here so the next person
    reading it does not "fix" the gate and leave the map global.
    """
    if is_admin(getattr(_user_of(interaction), "id", None)):
        return True, None
    if manageable_slugs(interaction):
        return True, None
    return False, ("That is a leadership control — you need a manager role for one of "
                   "the factions in this server.")
