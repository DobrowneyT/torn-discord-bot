"""
Phase 1 runner for Chain Watch: post (or edit) the mock board in a channel, and
optionally fire the two shift pings, so leaders can argue about the wording
before any of it is wired to live data.

Usage:
    python scratch/chain_preview.py              # board only, edited in place
    python scratch/chain_preview.py --pings      # also post the two ping shapes

⚠️ Edits in place on every run after the first, exactly like format_preview.py.
Re-run after each wording tweak: the point is to iterate in front of people
without filling the channel with drafts.
"""

import asyncio
import logging
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import discord
from dotenv import load_dotenv

import chain_formatter
import chain_mock
import chain_settings
import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chain_preview")

STATE_KEY = "chain_preview_message_id"


def main() -> None:
    load_dotenv(os.path.join(_PARENT, ".env"))
    token = os.environ.get("DISCORD_BOT_TOKEN")
    # The board channel setting wins when set, so a leader can move the preview
    # from Discord rather than by editing .env.
    channel_id = chain_settings.get("board_channel_id") or int(
        os.environ.get("DISCORD_CHANNEL_ID") or 0)
    if not token or not channel_id:
        raise SystemExit("Set DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID in .env "
                         "(or /chain set board_channel_id).")

    want_pings = "--pings" in sys.argv
    now = int(time.time())
    now_ms = now * 1000

    watch = chain_mock.mock_watch(now)
    embeds = chain_formatter.build_board(watch, now_ms=now_ms)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            channel = await client.fetch_channel(channel_id)
            st = state.load_state()
            message_id = st.get(STATE_KEY)

            if message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embeds=embeds)
                    log.info("edited board %s", message_id)
                except discord.NotFound:
                    # Somebody deleted it. Post a fresh one rather than dying.
                    message_id = None

            if not message_id:
                msg = await channel.send(embeds=embeds)
                st[STATE_KEY] = msg.id
                state.save_state(st)
                log.info("posted board %s", msg.id)

            if want_pings:
                lead = chain_settings.get("shift_lead_minutes")
                await channel.send(chain_formatter.build_shift_ping(
                    chain_mock.mock_shift_ping(now), lead_in_minutes=lead))
                await channel.send(chain_formatter.build_shift_ping(
                    chain_mock.mock_shift_ping_flying(now), lead_in_minutes=lead))
                log.info("posted both ping shapes")
        finally:
            await client.close()

    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
