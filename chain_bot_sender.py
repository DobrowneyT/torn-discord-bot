"""
The Discord side of Chain Watch's I/O (#785).

⚠️ Its own module because two hosts share it: the standalone `chain_bot.py` and
`chain_runtime`, which bolts Chain Watch onto a bot that already exists (the OC
watcher). Two copies of "edit the board, or post a new one if it is gone" would
drift, and the drift would show up as a duplicated standing board.

Everything here is I/O; nothing here decides anything.
"""

import logging
from typing import Optional

import discord

import chain_watcher

log = logging.getLogger("chain_bot_sender")


class DiscordSender(chain_watcher.Sender):
    """The real thing. Everything here is I/O; nothing here decides anything."""

    def __init__(self, client: discord.Client):
        self.client = client

    async def board(self, channel_id: int, embeds, message_id: Optional[int]):
        channel = self.client.get_channel(channel_id)
        if channel is None:
            log.warning("board channel %s not visible", channel_id)
            return None
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
        sent = await channel.send(embeds=embeds)
        return sent.id

    async def say(self, channel_id: int, content: str) -> None:
        channel = self.client.get_channel(channel_id)
        if channel is None:
            log.warning("ping channel %s not visible", channel_id)
            return
        try:
            await channel.send(content)
        except discord.HTTPException as e:
            # ⚠️ Swallowed deliberately. A ping that cannot be delivered must
            # not raise into the tick and stop the other factions' boards.
            log.warning("could not send to %s: %s", channel_id, e)
