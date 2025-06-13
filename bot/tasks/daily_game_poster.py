"""daily_game_poster.py
Script intended to be invoked every 10 minutes (e.g. via cron).
It reads the registered *daily_games* in each guild's app state and, if any game is
scheduled for the current Pacific time slot and enabled, sends its link to the
configured Discord channel using the bot token.

Usage (inside Docker container hosting the bot):
    python -m bot.tasks.daily_game_poster

You can wire this up with a crontab entry on the host, e.g.:
    */10 * * * * docker exec -t cunningbot python -m bot.tasks.daily_game_poster | cat

Ensure the container has the DISCORD_TOKEN environment variable set (same as the
main bot process) and that the bot has permission to send messages to the
registered channels.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import logging
from pathlib import Path
from typing import Any, Dict, List

import discord
from zoneinfo import ZoneInfo

logger = logging.getLogger("DailyGamePoster")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Helpers for state loading
# ---------------------------------------------------------------------------

# The app_state.json lives alongside bot/domain/app_state.py, but we resolve it
STATE_FILE_PATH = (
    Path(__file__).resolve().parent.parent / "core" / "app_state.json"
)

if not STATE_FILE_PATH.exists():
    logger.warning("State file %s not found – no games to post", STATE_FILE_PATH)


def _load_state() -> Dict[str, Any]:
    try:
        with STATE_FILE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to load state: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Discord posting logic
# ---------------------------------------------------------------------------

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


async def post_games() -> None:
    """Main entry point called once per invocation."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    state = _load_state()
    if not state:
        logger.info("App state empty – nothing to post.")
        return

    # Determine current time slot in Pacific time (HH:MM where MM in {0,10,20,...})
    now_pt = dt.datetime.now(PACIFIC_TZ)
    current_hour = now_pt.hour
    current_minute = now_pt.minute

    logger.info("Current Pacific time: %02d:%02d", current_hour, current_minute)

    # Build a mapping channel_id -> list[dict] of games to post so we can batch
    to_post: Dict[int, List[Dict[str, Any]]] = {}

    for guild_id_str, guild_state in state.items():
        if guild_id_str == "global":
            continue  # skip global state
        games = guild_state.get("daily_games", {})
        for game_name, game in games.items():
            if not game.get("enabled", True):
                continue
            if (
                game.get("hour") == current_hour
                and game.get("minute") == current_minute
            ):
                channel_id = int(game["channel_id"])
                to_post.setdefault(channel_id, []).append(game)

    if not to_post:
        logger.info("No games scheduled for this time slot.")
        return

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)
        for channel_id, games in to_post.items():
            try:
                channel = client.get_channel(channel_id)  # cached if possible
                if channel is None:
                    channel = await client.fetch_channel(channel_id)  # type: ignore[attr-defined]
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    logger.warning("Channel ID %s is not a text channel", channel_id)
                    continue
                for game in games:
                    msg = f"It's time for your daily **{game['name']}**! Play here: <{game['link']}>"
                    await channel.send(msg)
                    logger.info("Posted game '%s' to channel %s", game["name"], channel.id)
            except discord.Forbidden:
                logger.error("Missing permissions to post in channel %s", channel_id)
            except discord.HTTPException as exc:
                logger.error("HTTP error posting to channel %s: %s", channel_id, exc)
            except Exception as exc:
                logger.error("Unexpected error posting to channel %s: %s", channel_id, exc)
        await client.close()

    # Run the client – will close itself after on_ready completes
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(post_games()) 