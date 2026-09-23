"""
Which Discord user is which Torn member (#784).

⚠️ **This map is NOT tied to the sign-up sheet.** It is a standing identity
table: link a person once and every future chain, every future event resolves
through it. Nothing re-runs when the sheet changes — the poller looks up
whoever happens to hold a slot at ping time. That was the explicit worry when
this was specified, and the design has to keep answering it.

⚠️ **Auto-match first; `/chain link` is the escape hatch, not the primary path.**
Linking ~100 members one command at a time is exactly the chore that does not
get done, and a half-populated map sends pings that reach some people and not
others — worse than none, because nobody can tell which.

⚠️ **An ambiguous match stays unlinked rather than guessing.** Two Discord users
plausibly the same Torn member means the bot pings the wrong person about
somebody else's 3am shift, and that person has no way to know the ping was not
for them. Unlinked is visible in `/chain link-status`; wrong is not.

⚠️ **A manual link always beats an auto-match and survives re-matching.**
Otherwise the one correction leadership bothered to make is silently undone on
the next restart, which teaches them the command does not work.
"""

import logging
import re
from typing import Dict, Iterable, List, Optional, Tuple

import state

log = logging.getLogger("chain_identity")

STATE_KEY = "chain_links"

#: "Goosey [873341]", "Goosey (873341)", "[873341] Goosey" — the id is exact
#: wherever it sits, so this is the highest-confidence match and is tried first.
ID_IN_NAME = re.compile(r"[\[(](\d{4,9})[\])]")

SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"

#: Below this, a substring match is meaningless — three characters appear
#: inside half the guild, and the containment tier would start inventing links.
MIN_CONTAINS_LEN = 4


def _norm(name: Optional[str]) -> str:
    """
    Fold a display name for comparison.

    ⚠️ Case and the PUNCTUATION people put around their names — `~Goosey~`,
    `| Goosey |` — are noise. Alphanumeric padding like `xX_Goosey_Xx` survives
    this deliberately: stripping it here would mean `Goose` and `Goosey`
    normalise to something that matches, and a wrong link is the one outcome
    worse than no link. That case is handled by the separate, explicitly
    lower-confidence containment tier in `match`.
    """
    if not name:
        return ""
    return re.sub(r"[^a-z0-9_]", "", name.lower())


def _store() -> Dict[str, Dict]:
    return state.load_state().get(STATE_KEY, {}) or {}


def _save(links: Dict[str, Dict]) -> None:
    st = state.load_state()
    st[STATE_KEY] = links
    state.save_state(st)


def all_links() -> Dict[str, Dict]:
    """`{member_id: {discord_id, source, name}}` — every link, both sources."""
    return _store()


def discord_id_for(member_id: str) -> Optional[int]:
    entry = _store().get(str(member_id))
    return int(entry["discord_id"]) if entry else None


def link(member_id: str, discord_id: int, *, name: str = "",
         source: str = SOURCE_MANUAL) -> None:
    links = _store()
    links[str(member_id)] = {
        "discord_id": int(discord_id), "source": source, "name": name,
    }
    _save(links)
    log.info("linked torn=%s discord=%s (%s)", member_id, discord_id, source)


def unlink(member_id: str) -> bool:
    links = _store()
    if str(member_id) not in links:
        return False
    links.pop(str(member_id))
    _save(links)
    return True


def unlink_discord(discord_id: int) -> List[str]:
    """Drop every link pointing at a Discord user. Returns the member ids freed."""
    links = _store()
    gone = [mid for mid, e in links.items() if int(e["discord_id"]) == int(discord_id)]
    for mid in gone:
        links.pop(mid)
    if gone:
        _save(links)
    return gone


class GuildMember:
    """The two names Discord gives us, plus the id. Kept tiny so tests need no discord.py."""

    def __init__(self, id: int, display_name: str = "", name: str = ""):
        self.id = int(id)
        self.display_name = display_name or name
        self.name = name or display_name


def match(roster: Iterable[Dict], guild_members: Iterable[GuildMember],
          existing: Optional[Dict[str, Dict]] = None) -> Tuple[Dict[str, Dict], List[Dict]]:
    """
    Work out who is who.

    Returns `(links, ambiguous)`. `links` is the whole map afterwards;
    `ambiguous` lists the roster entries that had more than one plausible
    Discord user and were therefore left alone.

    Match order, highest confidence first:
      1. an id in the Discord name — `Goosey [873341]`. Exact; no guessing.
      2. the Torn name equal to the display name or the username, ignoring
         punctuation and case.
      3. the Torn name CONTAINED in one of them — `xX_Goosey_Xx`. ⚠️ Explicitly
         lower confidence and hedged twice: it needs a name of at least
         MIN_CONTAINS_LEN characters, and exactly one hit across the whole
         guild. Otherwise `Goose` would match `Goosey`.
      4. nothing — stays unlinked, and says so in `/chain link-status`.
    """
    links = dict(existing if existing is not None else _store())
    members = list(guild_members)

    # ⚠️ A Discord user already claimed by a MANUAL link is off the table for
    # auto-matching. Without this, a leader who corrects a bad match watches the
    # auto-matcher hand the same Discord user to somebody else on the next sync.
    claimed = {
        int(e["discord_id"]) for e in links.values() if e.get("source") == SOURCE_MANUAL
    }

    by_id: Dict[str, GuildMember] = {}
    for gm in members:
        for field in (gm.display_name, gm.name):
            found = ID_IN_NAME.search(field or "")
            if found:
                by_id.setdefault(found.group(1), gm)
                break

    # ⚠️ A Discord user whose own name carries a Torn id is SPOKEN FOR, and must
    # not also be offered to the name tiers. "Muttley [873341]" is 873341 — but
    # its normalised form still contains "muttley", so without this the
    # containment tier hands the same Discord user to Muttley as well, and one
    # person ends up holding two Torn identities.
    claimed |= {gm.id for gm in by_id.values()}

    by_name: Dict[str, List[GuildMember]] = {}
    for gm in members:
        for field in (gm.display_name, gm.name):
            key = _norm(field)
            if key:
                by_name.setdefault(key, [])
                if gm not in by_name[key]:
                    by_name[key].append(gm)

    ambiguous: List[Dict] = []
    for entry in roster:
        member_id = str(entry["member_id"])
        name = entry.get("name") or ""

        # ⚠️ Never overwrite a manual link. It is the correction a human made.
        if links.get(member_id, {}).get("source") == SOURCE_MANUAL:
            continue

        hit = by_id.get(member_id)
        if hit is None:
            # ⚠️ And a user already linked to a DIFFERENT member in this pass is
            # off the table too. One Discord account is one person.
            key = _norm(name)
            candidates = [gm for gm in by_name.get(key, []) if gm.id not in claimed]
            # ⚠️ Containment only when the exact tier found nobody, so a real
            # name match is never beaten by a coincidental substring.
            if not candidates and len(key) >= MIN_CONTAINS_LEN:
                candidates = [
                    gm for k, gms in by_name.items() if key in k
                    for gm in gms if gm.id not in claimed
                ]
                candidates = list(dict.fromkeys(candidates))
            if len(candidates) > 1:
                # ⚠️ Two people who could both be this member. Guessing here
                # pings the wrong person about somebody else's 3am shift, and
                # they have no way to know it was not for them.
                ambiguous.append({
                    "member_id": member_id, "name": name,
                    "candidates": [gm.id for gm in candidates],
                })
                links.pop(member_id, None)
                continue
            hit = candidates[0] if candidates else None

        if hit is None:
            # ⚠️ Drop a stale auto-link rather than keeping it: the member left
            # the server, or renamed past recognition, and a mention that
            # resolves to nobody is silently no notification at all.
            if links.get(member_id, {}).get("source") == SOURCE_AUTO:
                links.pop(member_id, None)
            continue

        if hit.id in claimed and by_id.get(member_id) is not hit:
            continue

        links[member_id] = {
            "discord_id": hit.id, "source": SOURCE_AUTO, "name": name,
        }
        claimed.add(hit.id)

    return links, ambiguous


def sync(roster: Iterable[Dict], guild_members: Iterable[GuildMember]) -> Dict:
    """Run `match` and persist. Returns a summary for `/chain link-status`."""
    roster = list(roster)
    links, ambiguous = match(roster, guild_members)
    _save(links)
    linked = [r for r in roster if str(r["member_id"]) in links]
    unlinked = [r for r in roster if str(r["member_id"]) not in links]
    log.info("identity sync: %d/%d linked, %d ambiguous",
             len(linked), len(roster), len(ambiguous))
    return {
        "total": len(roster),
        "linked": len(linked),
        "unlinked": unlinked,
        "ambiguous": ambiguous,
    }


def decorate(watchers: Iterable[Dict]) -> List[Dict]:
    """
    Attach `discord_id` to each watcher that has a link.

    ⚠️ The formatter falls back to `Name [ID]` for the rest, never a bare name:
    leadership has to be able to see exactly who to chase, and the id is what
    tells two members with similar display names apart.
    """
    links = _store()
    out = []
    for w in watchers:
        entry = links.get(str(w.get("member_id")))
        out.append({**w, "discord_id": entry["discord_id"]} if entry else dict(w))
    return out
