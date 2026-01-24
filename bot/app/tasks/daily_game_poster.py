"""daily_game_poster.py
Script intended to be invoked every 10 minutes (e.g. via cron).
It reads the registered *daily_games* in each guild's app state and, if any game is
scheduled for the current Pacific time slot and enabled, sends its link to the
configured Discord channel using the bot token.

Usage (inside Docker container hosting the bot):
    python -m bot.app.tasks.daily_game_poster

You can wire this up with a crontab entry on the host, e.g.:
    */10 * * * * docker exec -t cunningbot python -m bot.app.tasks.daily_game_poster | cat

Ensure the container has the DISCORD_TOKEN environment variable set (same as the
main bot process) and that the bot has permission to send messages to the
registered channels.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import logging
from pathlib import Path
from typing import Any, Dict, List

import discord
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()
from bot.app.app_state import get_all_guild_states

logger = logging.getLogger("DailyGamePoster")
logging.basicConfig(level=logging.INFO)

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

    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("App state empty – nothing to post.")
        return

    # Determine current time slot in Pacific time (round to nearest 10-minute interval)
    now_pt = dt.datetime.now(PACIFIC_TZ)
    current_hour = now_pt.hour
    # Round to the nearest 10-minute interval to handle slight cron timing variations
    current_minute = (now_pt.minute // 10) * 10

    logger.info("Current Pacific time: %02d:%02d (rounded to %02d:%02d)", 
                now_pt.hour, now_pt.minute, current_hour, current_minute)

    # Build a mapping channel_id -> list[dict] of games to post so we can batch
    to_post: Dict[int, List[Dict[str, Any]]] = {}

    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue  # skip global state

        if not isinstance(guild_state, dict):
            logger.warning("Guild state for %s is not a dict (got %s) – skipping", guild_id_str, type(guild_state))
            continue

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
                    # Send the message and capture the resulting Message object so we can
                    # immediately spin it off into its own thread (if the target is a
                    # standard text channel). Posting directly into an existing thread
                    # is also supported – in that case we skip thread creation.
                    message = await channel.send(msg)
                    logger.info("Posted game '%s' to channel %s", game["name"], channel.id)

                    # If we're in a normal text channel, create a public thread anchored
                    # to the message we just sent. This keeps daily games from cluttering
                    # the main chat while still notifying interested members.
                    if isinstance(channel, discord.TextChannel):
                        thread_name = f"{game['name']} – {now_pt:%Y-%m-%d}"  # e.g. Wordle – 2025-06-13
                        try:
                            await message.create_thread(
                                name=thread_name,
                                auto_archive_duration=1440,  # archive after 24 h of inactivity
                            )
                            logger.info(
                                "Created thread '%s' for game '%s' in channel %s",
                                thread_name,
                                game["name"],
                                channel.id,
                            )
                        except discord.HTTPException as exc:
                            logger.error(
                                "Failed to create thread for game '%s' in channel %s: %s",
                                game["name"],
                                channel.id,
                                exc,
                            )
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