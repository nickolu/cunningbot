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
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.app.redis.client import initialize_redis, close_redis
from bot.domain.trivia.question_seeds import get_unused_seed
from bot.domain.trivia.question_generator import generate_trivia_question
from bot.app.commands.trivia.trivia_views import TriviaQuestionView

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
        value="Click the 'Submit Answer' button below or use `/trivia answer`",
        inline=False
    )

    embed.set_footer(text=f"Game ID: {game_id[:8]}")

    return embed


async def post_trivia_questions() -> None:
    """Post trivia questions using Redis for game storage."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    # Initialize Redis
    await initialize_redis()
    store = TriviaRedisStore()

    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("App state empty – nothing to post.")
        await close_redis()
        return

    # Determine current time in Pacific time
    now_pt = dt.datetime.now(PACIFIC_TZ)

    logger.info(
        "Current Pacific time: %02d:%02d (Redis mode)",
        now_pt.hour, now_pt.minute
    )

    # Build list of trivia games to post
    to_post: List[Dict[str, Any]] = []
    already_posted = set()

    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            logger.warning(
                "Guild state for %s is not a dict (got %s) – skipping",
                guild_id_str, type(guild_state)
            )
            continue

        # Get registrations from Redis
        registrations = await store.get_registrations(guild_id_str)
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

            # Check each scheduled time
            matched_time = None

            for scheduled_time_str in schedule_times:
                try:
                    hour, minute = scheduled_time_str.split(":")
                    scheduled_hour = int(hour)
                    scheduled_minute = int(minute)
                except (ValueError, AttributeError):
                    logger.warning("Invalid schedule time format: %s", scheduled_time_str)
                    continue

                scheduled_dt = now_pt.replace(
                    hour=scheduled_hour,
                    minute=scheduled_minute,
                    second=0,
                    microsecond=0
                )

                time_diff = (now_pt - scheduled_dt).total_seconds() / 60

                if 0 <= time_diff <= 20:
                    matched_time = scheduled_time_str
                    logger.info(
                        "Registration %s matches scheduled time %s (%.1f minutes ago)",
                        reg_id[:8], matched_time, time_diff
                    )
                    break

            if not matched_time:
                continue

            # Check if already posted today from Redis
            active_games = await store.get_active_games(guild_id_str)
            already_posted_today = False

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

            # Track games we're queuing in this run
            game_key = f"{guild_id_str}:{reg_id}:{matched_time}"
            if game_key in already_posted:
                continue
            already_posted.add(game_key)

            # Get used seeds from guild state (still in JSON for now)
            used_seeds = guild_state.get("trivia_seeds_used", [])
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
        await close_redis()
        return

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s (Redis mode)", client.user)

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

                # Create view with button
                view = TriviaQuestionView(game_id, guild_id, client)

                # Post message with view
                message = await channel.send(embed=embed, view=view)
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

                # Store game in Redis
                game_data = {
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
                }

                await store.create_game(guild_id, game_id, game_data)

                # Add seed to used_seeds (still in JSON for now)
                guild_state = all_guild_states[guild_id]
                if "trivia_seeds_used" not in guild_state:
                    guild_state["trivia_seeds_used"] = []
                guild_state["trivia_seeds_used"].append(seed)
                set_state_value("trivia_seeds_used", guild_state["trivia_seeds_used"], guild_id)

                logger.info("Saved game state for game_id %s in Redis", game_id[:8])

            except discord.Forbidden:
                logger.error("Missing permissions to post in channel %s", channel_id)
            except discord.HTTPException as exc:
                logger.error("HTTP error posting to channel %s: %s", channel_id, exc)
            except Exception as exc:
                logger.error("Unexpected error posting to channel %s: %s", channel_id, exc, exc_info=True)

        await client.close()
        await close_redis()

    # Run the client
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(post_trivia_questions())
