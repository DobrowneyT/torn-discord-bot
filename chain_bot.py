"""
Chain Watch as its OWN bot process (#785).

⚠️ **Use this only with a Discord token nothing else is using.** Two processes
sharing one token open two gateway connections, and Discord routes each
interaction to only one of them — so a button belonging to the other process
silently stops working, with nothing logged. To put Chain Watch on a bot that
already exists, attach `chain_runtime.ChainRuntime` to that bot instead; `bot.py`
does exactly this for the OC watcher.
"""

import logging
import os

import discord

import chain_runtime

log = logging.getLogger("chain_bot")


class ChainBot(discord.Client):
    def __init__(self, lead_role_id: int = 0, guild_ids=None):
        intents = discord.Intents.default()
        # ⚠️ Required for identity matching (#784) — it is what populates
        # `guild.members`. Without it enabled in the developer portal the guild
        # looks empty and nobody is ever auto-linked, with no error to explain why.
        intents.members = True
        super().__init__(intents=intents)
        self.chain = chain_runtime.ChainRuntime(
            self, lead_role_id=lead_role_id, guild_ids=guild_ids)

    async def setup_hook(self) -> None:
        await self.chain.setup()

    async def on_ready(self) -> None:
        log.info("chain bot ready as %s, %d guild(s)", self.user, len(self.guilds))
        await self.chain.start()


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN must be set in .env")
    ChainBot(lead_role_id=int(os.environ.get("CHAIN_LEAD_ROLE_ID") or 0),
             guild_ids=chain_runtime.guild_ids_from_env()).run(token, log_handler=None)


if __name__ == "__main__":
    main()
