"""
Phase 1 runner: post (or edit) the mock-data embed in the configured channel.

Usage:
    cd discord
    cp .env.example .env  # then fill in DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID
    python scratch/format_preview.py

On first run it sends a new message and saves the id to discord/state.json.
On subsequent runs it edits that message in place — re-run after each
formatter.py tweak to see the change without spamming the channel.
"""

import asyncio
import logging
import os
import sys
import time

# Allow `from config import ...` etc. when run as a script from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import discord
from dotenv import load_dotenv

import alerts as alerts_mod
import formatter as formatter_mod
import mock_data
import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("format_preview")


def main() -> None:
    load_dotenv(os.path.join(_PARENT, ".env"))

    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id_raw = os.environ.get("DISCORD_CHANNEL_ID")
    if not token or not channel_id_raw:
        raise SystemExit("DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID must be set in discord/.env")
    channel_id = int(channel_id_raw)

    now_ts = int(time.time())
    crimes = mock_data.mock_crimes(now_ts)
    statuses = mock_data.mock_member_status(now_ts)
    alerts_dict = alerts_mod.build_alerts(crimes, statuses, now_ts)
    embeds = formatter_mod.build_embeds(alerts_dict, now_ts=now_ts)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            existing_id = state.get_message_id()
            message = None
            if existing_id:
                try:
                    message = await channel.fetch_message(existing_id)
                except discord.NotFound:
                    log.warning("Stored message id %s not found — sending a new one", existing_id)

            if message:
                await message.edit(embeds=embeds)
                log.info("Edited message %s in #%s", message.id, channel)
            else:
                message = await channel.send(embeds=embeds)
                state.set_message_id(message.id)
                log.info("Sent new message %s in #%s (id saved to state.json)", message.id, channel)
        finally:
            await client.close()

    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
