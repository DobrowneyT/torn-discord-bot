"""
Posting one member's report into their own forum thread (#943).

⚠️ **Everything written here is permanent and has one member's name on it**, in
a forum the faction's leadership reads. That shapes every rule below.

The thread lifecycle, and what each failure must do:

  no thread yet       → create it in the forum, then post. The dashboard's
                        registry learns the thread id from `posted`.
  thread archived     → unarchive and post. Forums archive quiet threads, and
                        we post at least every war, so this is routine.
  thread gone         → ⚠️ FAIL LOUDLY. Never silently recreate: a member's
                        running log is the feature, and a fresh empty thread
                        claiming to be it destroys the history while looking
                        like success. MonChoon: *"fail loudly if something gets
                        deleted, renamed."*
  forum unset         → do nothing at all, for every member. There is no
                        fallback channel; see `member_thread_forum_id`.
  already posted      → EDIT that message. A re-run of the same war is a
                        correction, not a second entry.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import discord

import member_report_format as fmt
import member_threads_api as api
import rw_settings

log = logging.getLogger("member_threads_poster")


def _embed(spec: Dict[str, Any], war_id: Any) -> discord.Embed:
    """
    The formatter's dict as a real embed.

    ⚠️ **The war id goes in the footer.** It is how a human reading the thread
    can tell which war a post is about, and it is the fallback for identifying
    an existing post if the recorded message id is ever lost — the edit path
    uses the id, but a footer costs nothing and a permanent log with ambiguous
    entries is worth avoiding.
    """
    embed = discord.Embed(
        title=spec["title"],
        description=spec["description"],
        colour=spec["colour"],
    )
    embed.set_footer(text=f"{spec['footer']} · war {war_id}")
    return embed

#: ⚠️ Between posts, because a roster-wide run is ~94 of them. Discord's own
#: limit is far higher, but a burst of 94 thread creations plus 94 posts is
#: exactly the shape that earns a global 429 and penalises every other feature
#: this bot runs. MonChoon: *"if we are posting 100 embeds, we might need to put
#: time delays in there."*
POST_DELAY_SECONDS = 1.5

#: How many entries one tick will post. A cap rather than "everything waiting",
#: so one tenant's backlog cannot monopolise the loop.
MAX_PER_TICK = 25


class ThreadGone(Exception):
    """The registered thread no longer exists, or we cannot see it."""


class ThreadMismatch(Exception):
    """
    The registered thread is no longer recognisably this member's.

    ⚠️ A rename does NOT break the id lookup, so a renamed thread keeps working
    and that is fine — a Torn name change should not stop somebody's reports.
    What is NOT fine is a thread REPURPOSED for a different member: posting
    member A's performance into a thread titled for member B writes a permanent,
    named record about the wrong person, in a channel leadership reads.

    The `[<member_id>]` suffix the thread is created with is what makes this
    checkable, and tolerating any rename that KEEPS it is what keeps the check
    from being a nuisance.
    """


def forum_id(slug: str) -> int:
    """
    Where this faction's member threads live, or 0.

    ⚠️ From `rw_settings`, not `chain_settings` (#944). It is set with
    `/rw config forum:…` — a ranked-war setting, not a Chain Watch tunable.
    """
    rw_settings.adopt_from_chain(slug)
    return int(rw_settings.get(slug, "member_thread_forum_id") or 0)


async def _resolve_forum(client: discord.Client, slug: str) -> Optional[discord.ForumChannel]:
    fid = forum_id(slug)
    if not fid:
        # ⚠️ Not a warning per tick. An unset forum is the normal state of a
        # faction that has not opted in, and logging it every poll would bury
        # the real failures.
        log.debug("%s has no member_thread_forum_id — member reports are off", slug)
        return None
    ch = client.get_channel(fid)
    if ch is None:
        try:
            ch = await client.fetch_channel(fid)
        except (discord.NotFound, discord.Forbidden) as exc:
            log.error("%s: member_thread_forum_id %s is unreachable (%s). Member "
                      "reports cannot post until this is fixed.", slug, fid, exc)
            return None
    if not isinstance(ch, discord.ForumChannel):
        # ⚠️ Said loudly, once per tick, because it is a misconfiguration that
        # looks like a working setting. A text channel would accept posts and
        # produce no threads, so every member's "log" would be one flat stream.
        log.error("%s: member_thread_forum_id %s is a %s, not a forum channel.",
                  slug, fid, type(ch).__name__)
        return None
    return ch


def _looks_like(thread: discord.Thread, member_id: str) -> bool:
    """
    Is this still recognisably that member's thread?

    ⚠️ Tolerant of renames that keep the id, because a Torn name change is a
    legitimate reason to rename and must not stop reports. Intolerant of one
    that drops or changes it — see ThreadMismatch.
    """
    return f"[{member_id}]" in (thread.name or "")


async def _existing_thread(
    forum: discord.ForumChannel, thread_id: int, member_id: str,
) -> discord.Thread:
    """
    The registered thread, unarchived if need be.

    ⚠️ Raises rather than returning None, so a caller cannot treat a deleted or
    repurposed thread as "nothing to do".
    """
    thread = forum.guild.get_thread(thread_id)
    if thread is None:
        try:
            fetched = await forum.guild.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden) as exc:
            raise ThreadGone(f"thread {thread_id} is gone or invisible: {exc}") from exc
        if not isinstance(fetched, discord.Thread):
            raise ThreadGone(f"channel {thread_id} is no longer a thread")
        thread = fetched
    if thread.archived:
        # ⚠️ Routine, not exceptional: forums archive quiet threads and we post
        # at least once a war. Unarchiving needs Manage Threads.
        try:
            await thread.edit(archived=False)
        except discord.Forbidden as exc:
            raise ThreadGone(
                f"thread {thread_id} is archived and I cannot unarchive it "
                f"(needs Manage Threads): {exc}") from exc

    # ⚠️ Checked AFTER unarchiving, so an archived-and-fine thread is not
    # reported as a mismatch, and BEFORE posting, which is the only point at
    # which it still matters.
    if not _looks_like(thread, member_id):
        raise ThreadMismatch(
            f"thread {thread_id} is named {thread.name!r}, which no longer "
            f"carries [{member_id}] — refusing to post member {member_id}'s "
            f"report into it")

    # ⚠️ A thread that has been MOVED to another channel is also a mismatch.
    # Its id still resolves, so posting would succeed — into a forum nobody
    # chose, publishing roll-call and dibs data to an unintended audience.
    parent_id = getattr(thread, "parent_id", None)
    if parent_id is not None and int(parent_id) != int(forum.id):
        raise ThreadMismatch(
            f"thread {thread_id} now lives in channel {parent_id}, not the "
            f"configured forum {forum.id} — refusing to post there")

    return thread


async def post_one(
    client: discord.Client, slug: str, entry: Dict[str, Any],
    threads: Dict[str, Any], forum: discord.ForumChannel,
) -> Tuple[bool, str]:
    """
    Render and post one outbox entry. Returns (ok, detail).

    ⚠️ `mark_posted` is called the instant Discord confirms, before anything
    else. A message Discord accepted but we never recorded is the one failure
    that duplicates a permanent record.
    """
    entry_id = int(entry["id"])
    member_id = str(entry["member_id"])
    payload = entry.get("payload") or {}
    war_id = payload.get("war_id")

    if war_id is None:
        return False, "entry has no war_id in its payload"

    facts = await asyncio.to_thread(api.report, slug, war_id, member_id)
    if facts is None:
        # ⚠️ Could be a live war (the dashboard refuses those) or a transport
        # failure. Either way we have no facts, and inventing a report is the
        # one thing we must never do.
        return False, f"no report available for war {war_id} / member {member_id}"

    embed = _embed(fmt.build_member_report(facts), war_id)
    registered = threads.get(member_id) or {}
    thread_id = registered.get("thread_id")

    if thread_id:
        thread = await _existing_thread(forum, int(thread_id), member_id)
        # ⚠️ **From the ENTRY, not the registry.** The dashboard keeps one
        # outbox row per (member, kind, war), and a re-queue re-opens that row
        # while keeping the message id it recorded. Its presence is the whole
        # signal that this is a correction rather than a first post.
        existing_message = entry.get("message_id")
        if existing_message:
            # ⚠️ EDIT, not a second post. A re-run of the same war is a
            # correction. MonChoon: *"on a re-run for the same war I would edit
            # the post. on a new war I would append."*
            msg = await thread.fetch_message(int(existing_message))
            await msg.edit(embed=embed)
            api.mark_posted(slug, entry_id, message_id=msg.id,
                            thread_id=thread.id, channel_id=forum.id)
            return True, f"edited {msg.id} in thread {thread.id}"
        msg = await thread.send(embed=embed)
        api.mark_posted(slug, entry_id, message_id=msg.id,
                        thread_id=thread.id, channel_id=forum.id)
        return True, f"posted {msg.id} to thread {thread.id}"

    # ⚠️ No thread: create it. The NAME is the member's, and it is what somebody
    # searches the forum for, so it carries the id too — two members have shared
    # a display name before and Torn names change.
    name = payload.get("member_name") or facts.get("member", {}).get("name") or member_id
    title = f"{name} [{member_id}]"[:100]
    created = await forum.create_thread(name=title, embed=embed)
    thread = created.thread
    msg = created.message
    api.mark_posted(slug, entry_id, message_id=msg.id,
                    thread_id=thread.id, channel_id=forum.id)
    return True, f"created thread {thread.id} and posted {msg.id}"


async def drain_once(client: discord.Client, slug: str) -> Dict[str, int]:
    """
    One tick for one faction. ⚠️ Claims only when there is a forum to post into.

    Returns counts, for the log line.
    """
    forum = await _resolve_forum(client, slug)
    if forum is None:
        # ⚠️ **Claim nothing.** Claiming and then failing every entry would burn
        # the ten-minute window every tick and fill `last_error` with a message
        # about our own configuration, on rows that are perfectly fine.
        return {"claimed": 0, "posted": 0, "failed": 0}

    data = await asyncio.to_thread(api.claim, slug)
    if data is None:
        return {"claimed": 0, "posted": 0, "failed": 0}

    pending = (data.get("pending") or [])[:MAX_PER_TICK]
    threads = data.get("threads") or {}
    counts = {"claimed": len(pending), "posted": 0, "failed": 0}

    for i, entry in enumerate(pending):
        entry_id = int(entry["id"])
        try:
            ok, detail = await post_one(client, slug, entry, threads, forum)
        except ThreadGone as exc:
            # ⚠️ LOUD, and released rather than retried quietly. Never recreate:
            # a fresh empty thread claiming to be somebody's running log
            # destroys the history while looking like success.
            log.error("%s: member %s's thread is gone — NOT recreating it. %s",
                      slug, entry.get("member_id"), exc)
            api.mark_failed(slug, entry_id, f"thread gone: {exc}")
            counts["failed"] += 1
        except ThreadMismatch as exc:
            # ⚠️ Also loud, also never recreated, and NAMED. This one means
            # somebody has edited the forum by hand, so the person who needs to
            # see it is a human, not a retry.
            log.error("%s: member %s's registered thread is not theirs any "
                      "more — NOT posting and NOT recreating. %s",
                      slug, entry.get("member_id"), exc)
            api.mark_failed(slug, entry_id, f"thread mismatch: {exc}")
            counts["failed"] += 1
        except discord.HTTPException as exc:
            log.warning("%s: posting entry %s failed: %s", slug, entry_id, exc)
            api.mark_failed(slug, entry_id, str(exc))
            counts["failed"] += 1
        else:
            if ok:
                counts["posted"] += 1
                log.info("%s: %s", slug, detail)
            else:
                api.mark_failed(slug, entry_id, detail)
                counts["failed"] += 1

        # ⚠️ Paced. A roster-wide run is ~94 of these, and a burst earns a
        # global 429 that penalises every other feature this bot runs.
        if i + 1 < len(pending):
            await asyncio.sleep(POST_DELAY_SECONDS)

    return counts
