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


def _explain_forbidden(channel, what: str) -> str:
    """Name the actual missing permissions rather than re-printing a traceback."""
    name = getattr(channel, "mention", None) or getattr(channel, "name", "?")
    try:
        perms = channel.permissions_for(channel.guild.me)
        value = perms.value
    except Exception:                                      # noqa: BLE001
        return (f"Cannot {what} in {name} — 403 from Discord. Check the channel's "
                f"permission overrides for this bot.")
    missing = [p for p, bit in REQUIRED_PERMS if not value & bit]
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

    async def _wake(self, channel) -> None:
        """
        Re-open an archived thread before writing to it.

        ⚠️ **Threads auto-archive, and an archived thread refuses writes.** The
        board is EDITED rather than re-posted, so a board living in a quiet
        thread silently stops updating — no error a reader would see, just a
        board frozen at whatever it last said. The OC watcher learned this the
        hard way and carries three strategies for it; this needs only the
        cheapest, because the bot holds Manage Threads.

        ⚠️ Failures are swallowed. If we cannot re-open it the write below will
        fail anyway, and it reports the real reason — an exception here would
        replace a useful message with a confusing one.
        """
        if not isinstance(channel, discord.Thread) or not channel.archived:
            return
        try:
            await channel.edit(archived=False)
            log.info("re-opened archived thread %s", channel.id)
        except discord.HTTPException as e:
            log.warning("could not re-open thread %s: %s", channel.id, e)

    async def board(self, channel_id: int, embeds, message_id: Optional[int]):
        channel = self.client.get_channel(channel_id)
        if channel is None:
            log.warning("board channel %s not visible", channel_id)
            return None
        await self._wake(channel)
        if message_id:
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
        channel = self.client.get_channel(channel_id)
        if channel is None:
            log.warning("ping channel %s not visible", channel_id)
            return None
        await self._wake(channel)
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
        channel = self.client.get_channel(channel_id)
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
        channel = self.client.get_channel(channel_id)
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
        channel = self.client.get_channel(channel_id)
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
