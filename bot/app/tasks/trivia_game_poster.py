"""trivia_game_poster.py

Script intended to be invoked every 10 minutes to post trivia questions.
It reads registered trivia_registrations in each guild's app state and posts questions
for any schedules matching the current Pacific time slot.

Usage (inside Docker container):
    python -m bot.app.tasks.trivia_game_poster

Runs in a loop with 10-minute intervals via Docker compose.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import logging
import uuid
from typing import Any, Dict, List

import discord
from zoneinfo import ZoneInfo

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.domain.trivia.question_seeds import get_unused_seed
from bot.domain.trivia.question_generator import generate_trivia_question

logger = logging.getLogger("TriviaGamePoster")
logging.basicConfig(level=logging.INFO)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def create_question_embed(question_data: dict, game_id: str, ends_at: dt.datetime) -> discord.Embed:
    """Create rich embed for trivia question."""
    # Map categories to colors
    category_colors = {
        "History": 0x8B4513,
        "Science": 0x4169E1,
        "Sports": 0xFF4500,
        "Entertainment": 0xFF1493,
        "Arts & Literature": 0x9370DB,
        "Geography": 0x228B22
    }

    color = category_colors.get(question_data["category"], 0x0099FF)

    embed = discord.Embed(
        title="🎯 Trivia Question",
        description=question_data["question"],
        color=color,
        timestamp=dt.datetime.now(dt.timezone.utc)
    )

    embed.add_field(name="Category", value=question_data["category"], inline=True)
    embed.add_field(name="Ends At", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
    embed.add_field(
        name="How to Answer",
        value="Use `/trivia answer message:\"your answer\"` in this thread",
        inline=False
    )

    embed.set_footer(text=f"Game ID: {game_id[:8]}")

    return embed


async def post_trivia_questions() -> None:
    """Main entry point for posting trivia questions."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("App state empty – nothing to post.")
        return

    # Determine current time in Pacific time
    now_pt = dt.datetime.now(PACIFIC_TZ)

    logger.info(
        "Current Pacific time: %02d:%02d",
        now_pt.hour, now_pt.minute
    )

    # Build list of trivia games to post
    to_post: List[Dict[str, Any]] = []
    already_posted = set()  # Track games we've already queued to avoid duplicates

    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            logger.warning(
                "Guild state for %s is not a dict (got %s) – skipping",
                guild_id_str, type(guild_state)
            )
            continue

        registrations = guild_state.get("trivia_registrations", {})
        if registrations:
            logger.info(
                "Guild %s has %d trivia registration(s)",
                guild_id_str, len(registrations)
            )

        for reg_id, registration in registrations.items():
            schedule_times = registration.get("schedule_times", [])
            logger.info(
                "Checking registration %s with schedule times: %s",
                reg_id[:8], schedule_times
            )
            if not registration.get("enabled", True):
                continue

            # Check each scheduled time to see if it should have been posted
            schedule_times = registration.get("schedule_times", [])
            matched_time = None

            for scheduled_time_str in schedule_times:
                # Parse the scheduled time (e.g., "8:00" or "16:00")
                try:
                    hour, minute = scheduled_time_str.split(":")
                    scheduled_hour = int(hour)
                    scheduled_minute = int(minute)
                except (ValueError, AttributeError):
                    logger.warning("Invalid schedule time format: %s", scheduled_time_str)
                    continue

                # Create a datetime for today at the scheduled time
                scheduled_dt = now_pt.replace(
                    hour=scheduled_hour,
                    minute=scheduled_minute,
                    second=0,
                    microsecond=0
                )

                # Calculate how many minutes past the scheduled time we are
                time_diff = (now_pt - scheduled_dt).total_seconds() / 60

                # If we're within 0-20 minutes past the scheduled time, this game should be posted
                # This gives us a 20-minute window to catch any missed games
                if 0 <= time_diff <= 20:
                    matched_time = scheduled_time_str
                    logger.info(
                        "Registration %s matches scheduled time %s (%.1f minutes ago)",
                        reg_id[:8], matched_time, time_diff
                    )
                    break
                elif time_diff < 0:
                    logger.debug(
                        "Registration %s scheduled time %s is in the future (%.1f minutes from now)",
                        reg_id[:8], scheduled_time_str, abs(time_diff)
                    )
                else:
                    logger.debug(
                        "Registration %s scheduled time %s is too far in the past (%.1f minutes ago)",
                        reg_id[:8], scheduled_time_str, time_diff
                    )

            if not matched_time:
                continue

            # Check if we've already posted a game for this registration today after the scheduled time
            # This prevents duplicate posts for the same scheduled time slot
            active_games = guild_state.get("active_trivia_games", {})
            already_posted_today = False

            # Parse the matched scheduled time to get the scheduled datetime
            scheduled_hour = int(matched_time.split(":")[0])
            scheduled_minute = int(matched_time.split(":")[1])
            scheduled_dt = now_pt.replace(
                hour=scheduled_hour,
                minute=scheduled_minute,
                second=0,
                microsecond=0
            )

            for game in active_games.values():
                if game.get("registration_id") == reg_id:
                    started_at = dt.datetime.fromisoformat(game["started_at"].replace("Z", "+00:00"))
                    started_at_pt = started_at.astimezone(PACIFIC_TZ)

                    # Check if this game was posted today after the scheduled time
                    # This means we already posted for this scheduled time slot
                    if (started_at_pt.date() == now_pt.date() and
                        started_at_pt >= scheduled_dt):
                        already_posted_today = True
                        logger.info(
                            "Skipping game %s for scheduled time %s (already posted today at %s)",
                            reg_id[:8], matched_time, started_at_pt.strftime("%H:%M")
                        )
                        break

            if already_posted_today:
                continue

            # Track games we're queuing in this run to avoid duplicates
            game_key = f"{guild_id_str}:{reg_id}:{matched_time}"
            if game_key in already_posted:
                continue
            already_posted.add(game_key)

            # Get used seeds for this guild
            used_seeds = guild_state.get("trivia_seeds_used", [])

            # Generate new seed
            seed = get_unused_seed(used_seeds)

            to_post.append({
                "guild_id": guild_id_str,
                "registration_id": reg_id,
                "registration": registration,
                "seed": seed,
                "used_seeds": used_seeds
            })

    if not to_post:
        logger.info("No trivia games scheduled for this time slot.")
        return

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)

        for game_info in to_post:
            guild_id = game_info["guild_id"]
            reg_id = game_info["registration_id"]
            registration = game_info["registration"]
            seed = game_info["seed"]
            used_seeds = game_info["used_seeds"]

            channel_id = registration["channel_id"]
            answer_window_minutes = registration["answer_window_minutes"]

            try:
                # Generate question
                logger.info("Generating question with seed: %s", seed)
                question_data = await generate_trivia_question(seed)

                # Get channel
                channel = client.get_channel(channel_id)
                if channel is None:
                    channel = await client.fetch_channel(channel_id)  # type: ignore[attr-defined]

                if not isinstance(channel, discord.TextChannel):
                    logger.warning("Channel ID %s is not a text channel", channel_id)
                    continue

                # Calculate end time
                now_utc = dt.datetime.now(dt.timezone.utc)
                ends_at = now_utc + dt.timedelta(minutes=answer_window_minutes)

                # Generate game ID
                game_id = str(uuid.uuid4())

                # Create embed
                embed = create_question_embed(question_data, game_id, ends_at)

                # Post message
                message = await channel.send(embed=embed)
                logger.info("Posted trivia question to channel %s", channel.id)

                # Create thread
                thread_name = f"Trivia – {question_data['category']} – {now_pt:%Y-%m-%d %H:%M}"
                thread = None
                try:
                    thread = await message.create_thread(
                        name=thread_name,
                        auto_archive_duration=1440  # 24 hours
                    )
                    logger.info("Created thread '%s' for trivia game", thread_name)
                except discord.HTTPException as exc:
                    logger.error("Failed to create thread: %s", exc)

                # Store in active_trivia_games
                guild_state = all_guild_states[guild_id]
                if "active_trivia_games" not in guild_state:
                    guild_state["active_trivia_games"] = {}

                guild_state["active_trivia_games"][game_id] = {
                    "registration_id": reg_id,
                    "channel_id": channel_id,
                    "thread_id": thread.id if thread else None,
                    "question": question_data["question"],
                    "correct_answer": question_data["correct_answer"],
                    "category": question_data["category"],
                    "explanation": question_data["explanation"],
                    "seed": seed,
                    "started_at": now_utc.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "message_id": message.id,
                    "submissions": {}
                }

                # Add seed to used_seeds
                if "trivia_seeds_used" not in guild_state:
                    guild_state["trivia_seeds_used"] = []
                guild_state["trivia_seeds_used"].append(seed)

                # Save state
                set_state_value("active_trivia_games", guild_state["active_trivia_games"], guild_id)
                set_state_value("trivia_seeds_used", guild_state["trivia_seeds_used"], guild_id)

                logger.info("Saved game state for game_id %s", game_id[:8])

            except discord.Forbidden:
                logger.error("Missing permissions to post in channel %s", channel_id)
            except discord.HTTPException as exc:
                logger.error("HTTP error posting to channel %s: %s", channel_id, exc)
            except Exception as exc:
                logger.error("Unexpected error posting to channel %s: %s", channel_id, exc, exc_info=True)

        await client.close()

    # Run the client
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(post_trivia_questions())
