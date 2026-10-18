"""
Posting one member's report into their own forum thread (#943).

⚠️ **Everything written here is permanent and has one member's name on it**, in
a forum the faction's leadership reads. That shapes every rule below.

The thread lifecycle, and what each failure must do:

  no registry row     → look in the forum for a `[id]` thread and adopt it
                        (#962); an untagged one with their name fails loudly.
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
import io
import logging
import re
from typing import Any, Dict, Optional, Tuple

import discord

import member_note_format as notes
import member_report_format as fmt
import war_overview_chart as chart
import member_threads_api as api
import rw_settings

log = logging.getLogger("member_threads_poster")


#: The attachment name the embed binds to. ⚠️ `attachment://` binds an embed to
#: a file in the SAME message; a bare URL would not render, and a second message
#: would separate the picture from the numbers it belongs to.
CHART_FILENAME = "war-chart.png"


async def _chart_file(facts: Dict[str, Any], title: str) -> Optional[discord.File]:
    """
    The member's own attack/revive timeline, as a PNG.

    ⚠️ **Rendered off the event loop.** `chart.render` is matplotlib and
    blocking; drawing ~95 of these inline would stall the gateway heartbeat and
    get the bot disconnected mid-run — the same fault #44 fixed elsewhere.

    ⚠️ **A chart that fails to draw must not stop the report.** The numbers are
    the record; the picture is an aid. Returning None costs a graph, raising
    would cost somebody their report.
    """
    spec = facts.get("chart")
    if not spec:
        return None
    try:
        png = await asyncio.to_thread(chart.render, spec, title=title)
    except Exception as exc:                               # noqa: BLE001
        log.warning("chart render failed for %s: %s", title, exc)
        return None
    if not png:
        return None
    return discord.File(io.BytesIO(png), filename=CHART_FILENAME)


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

#: How many entries one tick will post.
#:
#: ⚠️ **Sized to drain a WHOLE war in one run** (#946). A Torn faction caps out
#: around 100 members, so 150 covers any roster with headroom. The previous 25
#: meant a war's reports trickled out over four poll cycles, which is not what
#: "drains in one run" means and makes an interrupted run harder to reason
#: about — four partial runs have four times the chances to be interrupted.
#:
#: Still a cap rather than "everything waiting": a runaway outbox should slow
#: down, not post for an hour.
MAX_PER_TICK = 150


#: ⚠️ A drain of a full roster takes ~2.4 minutes at POST_DELAY_SECONDS, which
#: is LONGER than the poll interval. Without this, the next tick starts while
#: the last is still going. The claim window stops the two posting the same
#: entry, but they would interleave against Discord's rate limits and double
#: the effective post rate the pacing exists to hold down.
_draining: set = set()


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


def _bare(text: str) -> str:
    """How a member's display name is compared with an untagged thread's title."""
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


async def _forum_threads(forum: discord.ForumChannel):
    """Every thread in the forum, active and archived."""
    for thread in list(getattr(forum, "threads", None) or []):
        yield thread
    async for thread in forum.archived_threads(limit=None):
        yield thread


async def _adopt_thread(
    forum: discord.ForumChannel, member_id: str, name: Optional[str],
) -> Optional[discord.Thread]:
    """
    A thread already in the forum for a member the registry does not know (#962).

    ⚠️ **Only ever called when the registry has no row.** The registry is the
    lookup; this is the fallback for a thread made by hand or before #942, and
    the `posted` write that follows records it so the scan happens once.

    ⚠️ **Matched by `[member_id]`, never by name.** Torn names change and two
    members have shared one; the id suffix is the same rule `_looks_like` uses.

    ⚠️ **An untagged thread carrying the member's name is NOT adopted.** That
    would make a thread named after somebody their permanent record on a
    guess, so it raises ThreadMismatch and a human decides — add the
    `[id]` to its title and the next tick adopts it.
    """
    tagged = []
    untagged = []
    want = _bare(name) if name else ""
    async for thread in _forum_threads(forum):
        if _looks_like(thread, member_id):
            tagged.append(thread)
        elif want and _bare(thread.name) == want:
            untagged.append(thread)

    if len(tagged) > 1:
        raise ThreadMismatch(
            f"{len(tagged)} threads carry [{member_id}] "
            f"({', '.join(str(t.id) for t in tagged)}) — refusing to pick one "
            f"or create another. Merge or retitle them.")
    if tagged:
        return await _existing_thread(forum, tagged[0].id, member_id)
    if untagged:
        raise ThreadMismatch(
            f"thread {untagged[0].id} is named {untagged[0].name!r} but has no "
            f"[{member_id}] — not adopting it on a name match and not creating "
            f"a second one. Add [{member_id}] to its title if it is theirs.")
    return None


#: How far back a thread is scanned for an existing post. A member's thread
#: holds one post per war plus the odd membership note, so a war from two years
#: ago is still well inside this.
HISTORY_SCAN = 50


def _footer_names_war(message: discord.Message, war_id: Any) -> bool:
    """
    Is this one of our posts for THIS war?

    ⚠️ Anchored with a word boundary, or `war 42` matches `war 420` and a
    re-run of war 42 edits war 420's post — a permanent record rewritten to say
    something about the wrong war.
    """
    for embed in message.embeds or []:
        text = (embed.footer.text if embed.footer else None) or ""
        if re.search(rf"\bwar {re.escape(str(war_id))}\b", text):
            return True
    return False


async def _find_existing_post(
    thread: discord.Thread, war_id: Any, me: Optional[int],
) -> Optional[discord.Message]:
    """
    Our existing post for this war in this thread, if there is one.

    ⚠️ **The window the recorded message id cannot cover** (#946): Discord
    accepts a post and the write back to the dashboard fails. The entry stays
    unposted with no message id, and a naive retry adds a SECOND embed to
    somebody's permanent thread. The footer carries the war id precisely so the
    thread itself can be asked.
    """
    try:
        async for message in thread.history(limit=HISTORY_SCAN):
            if me is not None and message.author.id != me:
                continue
            if _footer_names_war(message, war_id):
                return message
    except discord.HTTPException as exc:
        # ⚠️ Not fatal, and NOT treated as "no post exists" — that would be the
        # duplicate this function prevents. The caller is told nothing was
        # found; what it does about that is its decision, and it refuses.
        log.warning("could not scan thread %s for an existing war-%s post: %s",
                    thread.id, war_id, exc)
        raise
    return None


async def _post_note(
    client: discord.Client, slug: str, entry: Dict[str, Any],
    threads: Dict[str, Any], forum: discord.ForumChannel,
) -> Tuple[bool, str]:
    """
    One membership note — a join or a leave.

    ⚠️ **A join is what CREATES a member's thread**, so this is the path a brand
    new member takes. A leave appends to the thread they already have.

    ⚠️ **No footer scan and no edit.** Each note is unique on
    `(member, kind, stint)`, so there is nothing to correct — unlike a war
    report, where a re-run means "fix this one".
    """
    entry_id = int(entry["id"])
    member_id = str(entry["member_id"])
    payload = entry.get("payload") or {}

    line = notes.note_line(
        entry.get("kind"), payload, entry.get("queued_ms"), member_id)
    if line is None:
        return False, f"no renderer for outbox kind {entry.get('kind')!r}"

    registered = threads.get(member_id) or {}
    thread_id = registered.get("thread_id")

    thread = (await _existing_thread(forum, int(thread_id), member_id) if thread_id
              else await _adopt_thread(forum, member_id, payload.get("member_name")))
    if thread is not None:
        msg = await thread.send(content=line)
        api.mark_posted(slug, entry_id, message_id=msg.id,
                        thread_id=thread.id, channel_id=forum.id)
        return True, f"noted {entry.get('kind')} in thread {thread.id}"

    created = await forum.create_thread(
        name=notes.thread_title(payload, member_id), content=line)
    api.mark_posted(slug, entry_id, message_id=created.message.id,
                    thread_id=created.thread.id, channel_id=forum.id)
    return True, (f"created thread {created.thread.id} for a "
                  f"{entry.get('kind')} note")


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
    kind = entry.get("kind")

    # ⚠️ **Membership notes are not war reports and must not be run through the
    # report path.** They have no `war_id`, and this function used to refuse
    # anything without one — so every `joined` and `left` note failed on every
    # tick for ever, which is the whole of #942 silently not working.
    if kind in notes.KINDS:
        return await _post_note(client, slug, entry, threads, forum)

    war_id = payload.get("war_id")
    if war_id is None:
        # ⚠️ Now genuinely unexpected: a kind nobody taught this drain about.
        # Named so it is obvious which, rather than blaming the payload.
        return False, f"unknown outbox kind {kind!r} with no war_id"

    facts = await asyncio.to_thread(api.report, slug, war_id, member_id)
    if facts is None:
        # ⚠️ Could be a live war (the dashboard refuses those) or a transport
        # failure. Either way we have no facts, and inventing a report is the
        # one thing we must never do.
        return False, f"no report available for war {war_id} / member {member_id}"

    spec = fmt.build_member_report(facts)
    embed = _embed(spec, war_id)
    chart_file = await _chart_file(facts, spec["title"])
    if chart_file is not None:
        embed.set_image(url=f"attachment://{CHART_FILENAME}")
    registered = threads.get(member_id) or {}
    thread_id = registered.get("thread_id")
    name = payload.get("member_name") or facts.get("member", {}).get("name") or member_id

    thread = (await _existing_thread(forum, int(thread_id), member_id) if thread_id
              else await _adopt_thread(forum, member_id, name))
    if thread is not None:
        # ⚠️ **From the ENTRY, not the registry.** The dashboard keeps one
        # outbox row per (member, kind, war), and a re-queue re-opens that row
        # while keeping the message id it recorded. Its presence is the whole
        # signal that this is a correction rather than a first post.
        existing_message = entry.get("message_id")

        if existing_message:
            # ⚠️ EDIT, not a second post. A re-run of the same war is a
            # correction. MonChoon: *"on a re-run for the same war I would edit
            # the post. on a new war I would append."*
            try:
                msg = await thread.fetch_message(int(existing_message))
            except discord.NotFound:
                # ⚠️ **The recorded post was DELETED — re-post it.**
                #
                # This looks like it contradicts the never-recreate rule for
                # threads, and it is the opposite case. Deleting a THREAD
                # destroys a member's whole running log, and silently making a
                # fresh one hides that the history is gone. Deleting one
                # MESSAGE removes one war's report from a log that is otherwise
                # intact, and re-posting restores exactly what was lost.
                #
                # Without this the entry 404s on every tick for ever: the id
                # points at nothing, so the edit can never succeed and the
                # report can never come back. MonChoon deleted one expecting it
                # to be recreated, which is the reasonable expectation.
                log.info("%s: recorded message %s for member %s is gone — "
                         "re-posting it", slug, existing_message, member_id)
                existing_message = None
            else:
                # ⚠️ `attachments=` REPLACES the set. Omitting it on an edit
                # leaves the previous chart under the corrected numbers, which
                # is worse than no chart: a picture that contradicts the figures
                # beside it.
                await msg.edit(embed=embed, attachments=_files(chart_file))
                api.mark_posted(slug, entry_id, message_id=msg.id,
                                thread_id=thread.id, channel_id=forum.id)
                return True, f"edited {msg.id} in thread {thread.id}"

        # ⚠️ **Ask the THREAD before posting** (#946). Reached either because
        # nothing was ever recorded, or because what was recorded has been
        # deleted. The recorded id cannot cover Discord accepting a post while
        # the write back to the dashboard fails — then the entry has no id and
        # a naive retry adds a second embed to a permanent thread. The footer
        # carries the war id for exactly this.
        me = getattr(getattr(client, "user", None), "id", None)
        found = await _find_existing_post(thread, war_id, me)
        if found is not None:
            await found.edit(embed=embed, attachments=_files(chart_file))
            api.mark_posted(slug, entry_id, message_id=found.id,
                            thread_id=thread.id, channel_id=forum.id)
            return True, (f"recovered and edited {found.id} in thread "
                          f"{thread.id} (post existed, id was never recorded)")

        msg = await thread.send(embed=embed, **_kw(chart_file))
        api.mark_posted(slug, entry_id, message_id=msg.id,
                        thread_id=thread.id, channel_id=forum.id)
        return True, f"posted {msg.id} to thread {thread.id}"

    # ⚠️ No thread: create it. The NAME is the member's, and it is what somebody
    # searches the forum for, so it carries the id too — two members have shared
    # a display name before and Torn names change.
    title = f"{name} [{member_id}]"[:100]
    created = await forum.create_thread(name=title, embed=embed, **_kw(chart_file))
    thread = created.thread
    msg = created.message
    api.mark_posted(slug, entry_id, message_id=msg.id,
                    thread_id=thread.id, channel_id=forum.id)
    return True, f"created thread {thread.id} and posted {msg.id}"


def _kw(f: Optional[discord.File]) -> Dict[str, Any]:
    """`file=` only when there is one — discord.py rejects `file=None`."""
    return {"file": f} if f is not None else {}


def _files(f: Optional[discord.File]):
    """The replacement attachment set for an edit: the new chart, or none."""
    return [f] if f is not None else []


async def drain_once(client: discord.Client, slug: str) -> Dict[str, int]:
    """
    One tick for one faction. ⚠️ Claims only when there is a forum to post into.

    Returns counts, for the log line.
    """
    # ⚠️ **Never two drains for one faction at once** (#946). A full roster
    # takes ~2.4 minutes to post and the loop ticks every 2, so without this
    # the next tick starts mid-drain. The claim window stops them posting the
    # same entry, but they would interleave against Discord's rate limits and
    # double the post rate the pacing exists to hold down.
    if slug in _draining:
        log.debug("%s: a drain is already running, skipping this tick", slug)
        return {"claimed": 0, "posted": 0, "failed": 0, "skipped": 1}

    forum = await _resolve_forum(client, slug)
    if forum is None:
        # ⚠️ **Claim nothing.** Claiming and then failing every entry would burn
        # the ten-minute window every tick and fill `last_error` with a message
        # about our own configuration, on rows that are perfectly fine.
        return {"claimed": 0, "posted": 0, "failed": 0}

    _draining.add(slug)
    try:
        return await _drain(client, slug, forum)
    finally:
        # ⚠️ `finally`, so a crash mid-drain does not wedge this faction's
        # reports until the process restarts.
        _draining.discard(slug)


async def _drain(client: discord.Client, slug: str,
                 forum: discord.ForumChannel) -> Dict[str, int]:
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
