"""
The Discord side of Chain Watch's I/O (#785).

⚠️ Its own module because two hosts share it: the standalone `chain_bot.py` and
`chain_runtime`, which bolts Chain Watch onto a bot that already exists (the OC
watcher). Two copies of "edit the board, or post a new one if it is gone" would
drift, and the drift would show up as a duplicated standing board.

Everything here is I/O; nothing here decides anything.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import discord

import chain_watcher

log = logging.getLogger("chain_bot_sender")

#: The permissions this bot needs in a board or ping channel, and the bit each
#: one occupies. Used to turn Discord's opaque 403 into an instruction.
#:
#: ⚠️ `Missing Access` (50001) means it cannot SEE the channel; `Missing
#: Permissions` (50013) means it can see it but cannot act. Both arrive as a
#: bare 403, and guessing between them is most of the time spent fixing this.
REQUIRED_PERMS = [
    ("View Channel", 1 << 10),
    ("Send Messages", 1 << 11),
    ("Embed Links", 1 << 14),
    ("Read Message History", 1 << 16),
]

#: The same list for a THREAD.
#:
#: ⚠️ **A thread does not need `Send Messages`.** Discord checks
#: `Send Messages in Threads` instead, and a role can hold one without the
#: other — so demanding both would refuse a thread the bot can post in
#: perfectly well. Getting this wrong in the safe-looking direction is still a
#: bug: it blocks a working setup and sends somebody editing permissions that
#: were never the problem.
THREAD_PERMS = [
    ("View Channel", 1 << 10),
    ("Send Messages in Threads", 1 << 38),
    ("Embed Links", 1 << 14),
    ("Read Message History", 1 << 16),
]


def missing_permissions(value: int, *, thread: bool = False) -> list:
    """
    Which of the permissions this bot needs are absent from `value`.

    ⚠️ Pure, and shared by the 403 explainer and `/chain channel`'s preflight,
    so the command refuses for exactly the reasons the poller would later fail
    for. Two lists would drift, and the drift shows up as a command that
    accepts a channel the bot cannot write to — which is the whole failure this
    exists to stop.
    """
    needed = THREAD_PERMS if thread else REQUIRED_PERMS
    return [name for name, bit in needed if not value & bit]


def _explain_forbidden(channel, what: str) -> str:
    """Name the actual missing permissions rather than re-printing a traceback."""
    name = getattr(channel, "mention", None) or getattr(channel, "name", "?")
    try:
        perms = channel.permissions_for(channel.guild.me)
        value = perms.value
    except Exception:                                      # noqa: BLE001
        return (f"Cannot {what} in {name} — 403 from Discord. Check the channel's "
                f"permission overrides for this bot.")
    missing = missing_permissions(value, thread=isinstance(channel, discord.Thread))
    if not missing:
        return (f"Cannot {what} in {name} despite holding every required "
                f"permission — check whether the channel is in a category that "
                f"denies the bot, or is an announcement/forum channel.")
    return (f"Cannot {what} in {name}: missing {', '.join(missing)}. "
            f"Edit Channel → Permissions → add this bot's role and allow them.")


class DiscordSender(chain_watcher.Sender):
    """The real thing. Everything here is I/O; nothing here decides anything."""

    def __init__(self, client: discord.Client):
        self.client = client

    async def _channel(self, channel_id: int, what: str):
        """
        The channel, from cache or fetched. None when it is genuinely unreachable.

        ⚠️ **`get_channel` reads the CACHE ONLY, and threads are routinely not
        in it.** Discord hands over a guild's active threads at connect time,
        but only the ones the bot could already see — a thread it is pointed at
        afterwards, or one that archived and came back, is simply absent. The
        old code read that miss as "not visible" and gave up.

        That is exactly what happened on 2026-09-27: `/chain channel` was run
        in two threads, answered "will post both here" both times, and the
        board never appeared in either. The journal held one line per tick —
        `board channel … not visible` — and nothing reached Discord at all.

        ⚠️ The fetch is also what tells the two causes apart. A cache miss is
        silent; a fetch comes back `Forbidden` (50001) when the bot cannot see
        the channel and `NotFound` when it is gone, so the log can say which
        one it is instead of guessing.

        ⚠️ Deliberately not cached on our side. A `Thread` object goes stale —
        `archived` in particular — and a stale one is what would skip the wake
        and leave the board frozen. One fetch per tick for a channel that is
        not in the cache is a price worth paying.
        """
        channel = self.client.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.client.fetch_channel(channel_id)
        except discord.Forbidden:
            log.error(
                "Cannot %s in channel %s: the bot cannot see it at all (Missing "
                "Access). Give its role View Channel there — for a thread, on the "
                "parent channel — or move the board with /chain channel.",
                what, channel_id)
        except discord.NotFound:
            log.error(
                "Cannot %s in channel %s: no such channel. It was probably "
                "deleted — re-point it with /chain channel.", what, channel_id)
        except discord.HTTPException as e:
            # Transient. The next tick tries again.
            log.warning("could not reach channel %s: %s", channel_id, e)
        return None

    async def _wake(self, channel) -> None:
        """
        Re-open an archived thread before writing to it.

        ⚠️ **Send-then-delete, NOT `thread.edit(archived=False)`.** The OC
        watcher tried the edit first — twice, as its strategies A and B — and
        both are commented out in `bot.py` to this day because they did not
        reliably work; strategy C, posting a single character and deleting it,
        is the one that ships and the one its journal records succeeding.
        Discord un-archives a thread on a new MESSAGE, and an edit to an
        existing message is not that.

        ⚠️ It also bumps the thread in the sidebar, which an edit does not. A
        board that is edited inside a collapsed thread is a board nobody sees
        change — which is the same failure as not writing at all.

        ⚠️ **Only before an EDIT.** A new message un-archives a thread by
        itself, so every ping and every first board post already wakes it —
        that is the whole difference between the two jobs this bot does. The
        board is the one surface that is edited rather than re-posted, so it is
        the only one that can go silent inside a sleeping thread. Nudging
        before a send would be a wasted API call and a visible flash for
        nothing.

        ⚠️ Failures are swallowed. If we cannot re-open it the write below
        fails anyway and reports the real reason; raising here would replace a
        useful message with a confusing one.
        """
        if not isinstance(channel, discord.Thread) or not channel.archived:
            return
        try:
            nudge = await channel.send(content="\u00b7")
            await nudge.delete()
            log.info("re-opened archived thread %s (send-then-delete)", channel.id)
        except discord.HTTPException as e:
            log.warning("could not re-open thread %s: %s", channel.id, e)

    async def board(self, channel_id: int, embeds, message_id: Optional[int]):
        channel = await self._channel(channel_id, "draw the board")
        if channel is None:
            return None
        if message_id:
            # ⚠️ Here and nowhere else: this is the only write that is an edit.
            await self._wake(channel)
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embeds=embeds)
                return message_id
            except discord.NotFound:
                # ⚠️ Somebody deleted it. Post a fresh one rather than going
                # dark — a board that vanishes is indistinguishable from a bot
                # that died.
                log.info("board message %s is gone — posting a new one", message_id)
            except discord.HTTPException as e:
                # ⚠️ Keep the id. A transient edit failure must not make the
                # next tick post a SECOND standing board.
                log.warning("could not edit board %s: %s", message_id, e)
                return message_id
        try:
            sent = await channel.send(embeds=embeds)
        except discord.Forbidden:
            # ⚠️ Logged as an instruction, once per tick, without a traceback.
            # This fires every cycle until somebody fixes the channel, and a
            # repeating stack trace buries the one line that says what to do.
            log.error("%s", _explain_forbidden(channel, "post the board"))
            return None
        except discord.HTTPException as e:
            log.warning("could not post the board in %s: %s", channel_id, e)
            return None
        return sent.id

    async def say(self, channel_id: int, content: str) -> Optional[int]:
        channel = await self._channel(channel_id, "send a ping")
        if channel is None:
            return None
        # ⚠️ No wake needed: posting IS what un-archives a thread. Pings are
        # always new messages, so they keep their own channel awake.
        try:
            sent = await channel.send(content)
            return sent.id
        except discord.Forbidden:
            log.error("%s", _explain_forbidden(channel, "send a ping"))
        except discord.HTTPException as e:
            # ⚠️ Swallowed deliberately. A ping that cannot be delivered must
            # not raise into the tick and stop the other factions' boards.
            log.warning("could not send to %s: %s", channel_id, e)
        return None

    async def edit(self, channel_id: int, message_id: int, content: str) -> bool:
        """
        Revise a ping in place. False means it is gone and should be forgotten.

        ⚠️ Editing never notifies, so revising "2 slots open" down to "1 slot"
        as people sign up costs the channel nothing — which is what makes this
        preferable to posting a correction.
        """
        channel = await self._channel(channel_id, "edit a ping")
        if channel is None:
            return False
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(content=content)
            return True
        except discord.NotFound:
            # Somebody deleted it by hand. Not an error; stop tracking it.
            return False
        except discord.HTTPException as e:
            # ⚠️ True, not False: a transient failure must not make us forget a
            # message that is still there, or it would be orphaned forever.
            log.warning("could not edit %s: %s", message_id, e)
            return True

    async def delete(self, channel_id: int, message_id: int) -> None:
        channel = await self._channel(channel_id, "delete a message")
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            pass                      # already gone; nothing to do
        except discord.HTTPException as e:
            # ⚠️ Deleting our OWN message needs no Manage Messages, so a
            # failure here is transient rather than a permission problem.
            log.warning("could not delete %s: %s", message_id, e)


    async def purge_own(self, channel_id: int, *, before_ms: int,
                        keep_ids: set, limit: int = 500) -> int:
        """
        Delete the bot's OWN messages in a channel, older than `before_ms`.

        ⚠️ **Only messages this bot authored.** Deleting anybody else's would be
        a different and much worse tool, and the permission to do it (Manage
        Messages) is one this bot deliberately does not hold — so a bug here
        fails with a 403 rather than eating a channel.

        ⚠️ **`keep_ids` is not optional.** The standing board lives in the same
        channel as the pings when the operator has not split them, and it is
        old by definition — it is edited in place, never re-posted. Without
        this, the first tidy-up would delete the board.

        ⚠️ **Bounded scan.** `limit` caps how far back it looks, so a stray
        command cannot walk the entire history of a busy channel.
        """
        channel = await self._channel(channel_id, "tidy up")
        if channel is None:
            return 0
        me = self.client.user
        cutoff = datetime.fromtimestamp(before_ms / 1000, tz=timezone.utc)
        removed = 0
        try:
            async for message in channel.history(limit=limit, before=cutoff):
                if me is None or message.author.id != me.id:
                    continue
                if message.id in keep_ids:
                    continue
                try:
                    await message.delete()
                    removed += 1
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    log.warning("could not delete %s: %s", message.id, e)
        except discord.Forbidden:
            log.error("%s", _explain_forbidden(channel, "read history to tidy up"))
        except discord.HTTPException as e:
            log.warning("tidy scan failed in %s: %s", channel_id, e)
        return removed
