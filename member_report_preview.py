#!/usr/bin/env python3
"""
Post the post-war member report to a channel, from the command line.

    python3 member_report_preview.py                 # print to stdout
    python3 member_report_preview.py --post          # post both fixtures
    python3 member_report_preview.py --post --which sparse

⚠️ **It renders the MOCK, not live data.** Nothing here reaches a tenant's
dashboard — the point is to judge the embed before any of the three sources is
joined up. `chain_preview` exists for the same reason and carries the same
caveat.

⚠️ **The channel defaults to the test channel and nothing else.** A preview tool
that can post into a member-facing forum by a typo is a tool that eventually
does. `--channel` has to be given explicitly to go anywhere else.
"""

import argparse
import asyncio
import os
import sys

import member_report_format as fmt
import member_report_mock as mock

# MonChoon's nominated test channel (2026-10-10).
TEST_CHANNEL_ID = 1286415074835038331

FIXTURES = {"full": mock.MEMBER_REPORT, "sparse": mock.MEMBER_REPORT_SPARSE}


def render(which: str) -> dict:
    return fmt.build_member_report(FIXTURES[which])


def to_stdout(which: str) -> None:
    e = render(which)
    print("=" * 72)
    print(f"{which.upper()}  |  {e['title']}  |  colour {hex(e['colour'])}")
    print("-" * 72)
    print(e["description"])
    print("-" * 72)
    print("footer:", e["footer"])


async def to_discord(which_list, channel_id: int) -> None:
    # ⚠️ Imported HERE, not at module scope. Rendering to stdout is the common
    # use and must work on a laptop with neither discord.py nor python-dotenv
    # installed — a preview tool that needs the bot's whole dependency set to
    # print text is one nobody runs before pushing.
    try:
        import discord
        from dotenv import load_dotenv
    except ModuleNotFoundError as err:
        # ⚠️ Spelled out, because the common case is running this on a laptop
        # where the bot's dependencies were never installed — and
        # "ModuleNotFoundError: discord" does not tell anybody what to do next.
        raise SystemExit(
            f"{err.name} is not installed, so this cannot post.\n"
            "Run it on the VPS, where the bot's venv already has it:\n"
            "  ssh ubuntu@51.68.212.98\n"
            "  cd ~/torn/torn-discord-bot && .venv/bin/python member_report_preview.py --post\n"
            "Or locally: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
            "Rendering to stdout (no --post) needs none of this."
        ) from err

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN must be set in the bot's .env")

    # ⚠️ No privileged intents and no command tree: this connects, posts and
    # leaves. It must not look like a second instance of the bot — running two
    # processes on one token is how the OC watcher got double-posts before.
    client = discord.Client(intents=discord.Intents.none())

    @client.event
    async def on_ready() -> None:
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            for which in which_list:
                e = render(which)
                embed = discord.Embed(
                    title=e["title"], description=e["description"], colour=e["colour"]
                )
                embed.set_footer(text=e["footer"])
                await channel.send(embed=embed)
                print(f"posted {which} to #{getattr(channel, 'name', channel_id)}")
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--post", action="store_true", help="send to Discord instead of stdout")
    ap.add_argument("--which", choices=[*FIXTURES, "both"], default="both")
    ap.add_argument("--channel", type=int, default=TEST_CHANNEL_ID,
                    help=f"channel id (default: the test channel, {TEST_CHANNEL_ID})")
    args = ap.parse_args()

    which_list = list(FIXTURES) if args.which == "both" else [args.which]
    if not args.post:
        for which in which_list:
            to_stdout(which)
        return
    if args.channel != TEST_CHANNEL_ID:
        # Said out loud rather than assumed — this is the one flag that can
        # reach a channel real members read.
        print(f"⚠️  posting to {args.channel}, NOT the test channel", file=sys.stderr)
    asyncio.run(to_discord(which_list, args.channel))


if __name__ == "__main__":
    main()
