"""trivia_game_closer.py

Script intended to be invoked every 1 minute to close trivia games and validate answers.
It reads active_trivia_games from each guild's app state and processes any games where
the answer window has closed.

Usage (inside Docker container):
    python -m bot.app.tasks.trivia_game_closer

Runs in a loop with 1-minute intervals via Docker compose.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import logging
from typing import Any, Dict, List

import discord
from zoneinfo import ZoneInfo

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.app.redis.locks import redis_lock
from bot.app.redis.client import get_redis_client, initialize_redis, close_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.domain.trivia.answer_validator import validate_answer

logger = logging.getLogger("TriviaGameCloser")
logging.basicConfig(level=logging.INFO)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

# Feature flag for Redis migration
USE_REDIS = True


async def close_expired_games() -> None:
    """Main entry point for closing expired trivia games."""
    if USE_REDIS:
        await _close_with_redis()
    else:
        await _close_with_json()


async def _close_with_redis() -> None:
    """Close expired games using Redis (atomic, with distributed locks)."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    # Initialize Redis
    await initialize_redis()
    store = TriviaRedisStore()
    redis_client = get_redis_client()

    now_utc = dt.datetime.now(dt.timezone.utc)
    logger.info("Checking for expired trivia games at %s (Redis mode)", now_utc.isoformat())

    # Get all guilds with active games
    all_guild_states = get_all_guild_states()
    to_close: List[Dict[str, Any]] = []

    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            logger.warning(
                "Guild state for %s is not a dict (got %s) – skipping",
                guild_id_str, type(guild_state)
            )
            continue

        # Get active games from Redis
        active_games = await store.get_active_games(guild_id_str)
        logger.info("Guild %s has %d active games", guild_id_str, len(active_games))

        for game_id, game_data in active_games.items():
            # Skip if already closed
            if game_data.get("closed_at"):
                logger.info("Skipping game %s (already closed at %s)", game_id[:8], game_data.get("closed_at"))
                continue

            ends_at_str = game_data.get("ends_at")
            if not ends_at_str:
                logger.warning("Game %s has no ends_at field", game_id[:8])
                continue

            try:
                ends_at = dt.datetime.fromisoformat(ends_at_str)
                logger.info(
                    "Game %s: ends_at=%s (tz=%s), now_utc=%s (tz=%s), expired=%s",
                    game_id[:8],
                    ends_at.isoformat(),
                    ends_at.tzinfo,
                    now_utc.isoformat(),
                    now_utc.tzinfo,
                    now_utc >= ends_at
                )
                if now_utc >= ends_at:
                    to_close.append({
                        "guild_id": guild_id_str,
                        "game_id": game_id,
                        "game_data": game_data
                    })
            except (ValueError, TypeError) as e:
                logger.error("Invalid ends_at format for game %s: %s", game_id, e)

    if not to_close:
        logger.info("No expired trivia games found.")
        await close_redis()
        return

    logger.info("Found %d expired games to close", len(to_close))

    # Process each game with distributed lock
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s (Redis mode)", client.user)

        for game_info in to_close:
            guild_id = game_info["guild_id"]
            game_id = game_info["game_id"]

            try:
                # Acquire distributed lock for this game
                lock_resource = f"trivia:{guild_id}:game:{game_id}"

                try:
                    async with redis_lock(redis_client, lock_resource, timeout=60):
                        logger.info("Acquired lock for game %s", game_id[:8])

                        # Double-check game still exists and isn't closed
                        game_data = await store.get_game(guild_id, game_id)
                        if not game_data:
                            logger.info("Game %s no longer exists (already processed)", game_id[:8])
                            continue

                        if game_data.get("closed_at"):
                            logger.info("Game %s already closed by another closer", game_id[:8])
                            continue

                        # Mark as closed immediately to prevent other closers
                        game_data["closed_at"] = now_utc.isoformat()
                        await store.update_game(guild_id, game_id, game_data)
                        logger.info("Marked game %s as closed at %s", game_id[:8], now_utc.isoformat())

                        # Get submissions from Redis
                        submissions = await store.get_submissions(guild_id, game_id)
                        logger.info("Game %s has %d submissions to process", game_id[:8], len(submissions))

                        # Extract game metadata
                        thread_id = game_data.get("thread_id")
                        question = game_data.get("question", "Unknown question")
                        correct_answer = game_data.get("correct_answer", "Unknown")
                        category = game_data.get("category", "Unknown")
                        explanation = game_data.get("explanation", "")

                        # Validate submissions (reuse cached validations)
                        validated_submissions = {}
                        cached_count = 0
                        new_validation_count = 0

                        for user_id, submission in submissions.items():
                            user_answer = submission.get("answer", "")
                            is_correct = submission.get("is_correct")

                            # Reuse cached validation if available
                            if is_correct is not None:
                                logger.info(f"Reusing cached validation for user {user_id}: {is_correct}")
                                validated_submissions[user_id] = {
                                    "answer": user_answer,
                                    "is_correct": is_correct
                                }
                                cached_count += 1
                            else:
                                # Validate answers that weren't validated immediately
                                logger.info(f"Validating unvalidated answer for user {user_id}")
                                validation_result = await validate_answer(
                                    user_answer, correct_answer, question
                                )

                                validated_submissions[user_id] = {
                                    "answer": user_answer,
                                    "is_correct": validation_result["is_correct"]
                                }
                                new_validation_count += 1

                                logger.info(
                                    "Validated answer for user %s: %s -> %s",
                                    user_id, user_answer, validation_result["is_correct"]
                                )

                        logger.info(
                            "Processing game %s: %d cached validations, %d new validations",
                            game_id[:8], cached_count, new_validation_count
                        )

                        # Post results to Discord
                        if thread_id:
                            try:
                                thread = client.get_channel(thread_id)
                                if thread is None:
                                    thread = await client.fetch_channel(thread_id)  # type: ignore[attr-defined]

                                if isinstance(thread, discord.Thread):
                                    # Build results message
                                    correct_users = [
                                        uid for uid, sub in validated_submissions.items()
                                        if sub["is_correct"]
                                    ]

                                    embed = discord.Embed(
                                        title="✅ Trivia Results",
                                        color=0x00FF00,
                                        timestamp=dt.datetime.now(dt.timezone.utc)
                                    )

                                    embed.add_field(
                                        name="Question",
                                        value=question,
                                        inline=False
                                    )

                                    embed.add_field(
                                        name="Correct Answer",
                                        value=f"**{correct_answer}**",
                                        inline=False
                                    )

                                    if explanation:
                                        embed.add_field(
                                            name="Explanation",
                                            value=explanation,
                                            inline=False
                                        )

                                    # List correct users
                                    if correct_users:
                                        user_mentions = []
                                        for uid in correct_users:
                                            try:
                                                user = await client.fetch_user(int(uid))
                                                user_mentions.append(user.mention)
                                            except Exception:
                                                user_mentions.append(f"<@{uid}>")

                                        embed.add_field(
                                            name=f"🎉 Correct ({len(correct_users)})",
                                            value="\n".join(user_mentions),
                                            inline=False
                                        )
                                    else:
                                        embed.add_field(
                                            name="Results",
                                            value="No one got it correct this time!",
                                            inline=False
                                        )

                                    # Add participation stats
                                    embed.add_field(
                                        name="Participation",
                                        value=f"**{len(validated_submissions)}** player(s) answered",
                                        inline=False
                                    )

                                    embed.set_footer(text=f"Category: {category} • Game ID: {game_id[:8]}")

                                    logger.info("Posting results for game %s to thread %s", game_id[:8], thread_id)
                                    await thread.send(embed=embed)
                                    logger.info("Posted results to thread %s", thread_id)

                            except discord.Forbidden:
                                logger.error("Missing permissions to post in thread %s", thread_id)
                            except discord.HTTPException as exc:
                                logger.error("HTTP error posting to thread %s: %s", thread_id, exc)
                            except Exception as exc:
                                logger.error(
                                    "Unexpected error posting to thread %s: %s",
                                    thread_id, exc, exc_info=True
                                )

                        # Move game to history in Redis
                        await store.move_to_history(guild_id, game_id, game_data, validated_submissions)

                        # Delete from active games
                        await store.delete_game(guild_id, game_id)

                        logger.info("Moved game %s to history and removed from active games", game_id[:8])

                except LockAcquisitionError:
                    logger.info("Could not acquire lock for game %s (another closer is processing it)", game_id[:8])
                    continue

            except Exception as exc:
                logger.error(
                    "Unexpected error closing game %s: %s",
                    game_id, exc, exc_info=True
                )

        await client.close()
        await close_redis()

    # Run the client
    await client.start(token)


async def _close_with_json() -> None:
    """Close expired games using JSON files (legacy, has race conditions)."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("App state empty – nothing to process.")
        return

    now_utc = dt.datetime.now(dt.timezone.utc)
    logger.info("Checking for expired trivia games at %s", now_utc.isoformat())

    # Find expired games
    to_close: List[Dict[str, Any]] = []

    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            logger.warning(
                "Guild state for %s is not a dict (got %s) – skipping",
                guild_id_str, type(guild_state)
            )
            continue

        active_games = guild_state.get("active_trivia_games", {})
        logger.info("Guild %s has %d active games", guild_id_str, len(active_games))

        for game_id, game_data in active_games.items():
            # Skip if already closed (prevents duplicate processing)
            if game_data.get("closed_at"):
                logger.info("Skipping game %s (already closed at %s)", game_id[:8], game_data.get("closed_at"))
                continue

            ends_at_str = game_data.get("ends_at")
            if not ends_at_str:
                logger.warning("Game %s has no ends_at field", game_id[:8])
                continue

            try:
                ends_at = dt.datetime.fromisoformat(ends_at_str)
                logger.info(
                    "Game %s: ends_at=%s (tz=%s), now_utc=%s (tz=%s), expired=%s",
                    game_id[:8],
                    ends_at.isoformat(),
                    ends_at.tzinfo,
                    now_utc.isoformat(),
                    now_utc.tzinfo,
                    now_utc >= ends_at
                )
                if now_utc >= ends_at:
                    to_close.append({
                        "guild_id": guild_id_str,
                        "game_id": game_id,
                        "game_data": game_data
                    })
            except (ValueError, TypeError) as e:
                logger.error("Invalid ends_at format for game %s: %s", game_id, e)

    if not to_close:
        logger.info("No expired trivia games found.")
        return

    logger.info("Found %d expired games to close", len(to_close))

    # Reload state from disk to capture any submissions that happened
    # after initial load but before we start processing
    logger.info("Reloading state to capture latest submissions...")
    all_guild_states = get_all_guild_states()

    # Update game_data in to_close with latest state
    for game_info in to_close:
        guild_id = game_info["guild_id"]
        game_id = game_info["game_id"]

        # Get latest version of this game
        latest_guild_state = all_guild_states.get(guild_id, {})
        latest_active_games = latest_guild_state.get("active_trivia_games", {})
        latest_game_data = latest_active_games.get(game_id)

        if latest_game_data:
            game_info["game_data"] = latest_game_data
            logger.info(
                "Updated game %s with latest state (submissions: %d)",
                game_id[:8],
                len(latest_game_data.get("submissions", {}))
            )

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)

        for game_info in to_close:
            guild_id = game_info["guild_id"]
            game_id = game_info["game_id"]
            game_data = game_info["game_data"]

            thread_id = game_data.get("thread_id")
            question = game_data.get("question", "Unknown question")
            correct_answer = game_data.get("correct_answer", "Unknown")
            category = game_data.get("category", "Unknown")
            explanation = game_data.get("explanation", "")
            submissions = game_data.get("submissions", {})

            try:
                # Validate submissions (reuse cached validations when available)
                logger.info("Game %s has %d submissions to process", game_id[:8], len(submissions))
                validated_submissions = {}
                cached_count = 0
                new_validation_count = 0

                for user_id, submission in submissions.items():
                    user_answer = submission.get("answer", "")
                    is_correct = submission.get("is_correct")

                    # Reuse cached validation if available
                    if is_correct is not None:
                        logger.info(f"Reusing cached validation for user {user_id}: {is_correct}")
                        validated_submissions[user_id] = {
                            "answer": user_answer,
                            "is_correct": is_correct
                        }
                        cached_count += 1
                    else:
                        # Validate answers that weren't validated immediately (fallback cases)
                        logger.info(f"Validating unvalidated answer for user {user_id}")
                        validation_result = await validate_answer(
                            user_answer, correct_answer, question
                        )

                        validated_submissions[user_id] = {
                            "answer": user_answer,
                            "is_correct": validation_result["is_correct"]
                        }
                        new_validation_count += 1

                        logger.info(
                            "Validated answer for user %s: %s -> %s",
                            user_id, user_answer, validation_result["is_correct"]
                        )

                logger.info(
                    "Processing game %s: %d cached validations, %d new validations",
                    game_id[:8], cached_count, new_validation_count
                )

                # Get thread
                if thread_id:
                    try:
                        thread = client.get_channel(thread_id)
                        if thread is None:
                            thread = await client.fetch_channel(thread_id)  # type: ignore[attr-defined]

                        if isinstance(thread, discord.Thread):
                            # Build results message
                            correct_users = [
                                uid for uid, sub in validated_submissions.items()
                                if sub["is_correct"]
                            ]

                            embed = discord.Embed(
                                title="✅ Trivia Results",
                                color=0x00FF00,
                                timestamp=dt.datetime.now(dt.timezone.utc)
                            )

                            embed.add_field(
                                name="Question",
                                value=question,
                                inline=False
                            )

                            embed.add_field(
                                name="Correct Answer",
                                value=f"**{correct_answer}**",
                                inline=False
                            )

                            if explanation:
                                embed.add_field(
                                    name="Explanation",
                                    value=explanation,
                                    inline=False
                                )

                            # List correct users
                            if correct_users:
                                user_mentions = []
                                for uid in correct_users:
                                    try:
                                        user = await client.fetch_user(int(uid))
                                        user_mentions.append(user.mention)
                                    except Exception:
                                        user_mentions.append(f"<@{uid}>")

                                embed.add_field(
                                    name=f"🎉 Correct ({len(correct_users)})",
                                    value="\n".join(user_mentions),
                                    inline=False
                                )
                            else:
                                embed.add_field(
                                    name="Results",
                                    value="No one got it correct this time!",
                                    inline=False
                                )

                            # Add participation stats
                            embed.add_field(
                                name="Participation",
                                value=f"**{len(validated_submissions)}** player(s) answered",
                                inline=False
                            )

                            embed.set_footer(text=f"Category: {category} • Game ID: {game_id[:8]}")

                            # Mark game as closed BEFORE posting results to prevent duplicate processing
                            logger.info("Marking game %s as closed at %s", game_id[:8], now_utc.isoformat())
                            game_data["closed_at"] = now_utc.isoformat()

                            # Update the game in state immediately
                            guild_state = all_guild_states[guild_id]
                            guild_state["active_trivia_games"][game_id] = game_data
                            set_state_value("active_trivia_games", guild_state["active_trivia_games"], guild_id)

                            logger.info("Posting results for game %s to thread %s", game_id[:8], thread_id)
                            await thread.send(embed=embed)
                            logger.info("Posted results to thread %s", thread_id)

                    except discord.Forbidden:
                        logger.error("Missing permissions to post in thread %s", thread_id)
                    except discord.HTTPException as exc:
                        logger.error("HTTP error posting to thread %s: %s", thread_id, exc)
                    except Exception as exc:
                        logger.error(
                            "Unexpected error posting to thread %s: %s",
                            thread_id, exc, exc_info=True
                        )

                # Move game to history
                guild_state = all_guild_states[guild_id]

                if "trivia_history" not in guild_state:
                    guild_state["trivia_history"] = {}

                guild_state["trivia_history"][game_id] = {
                    "question": question,
                    "correct_answer": correct_answer,
                    "category": category,
                    "started_at": game_data.get("started_at"),
                    "ended_at": now_utc.isoformat(),
                    "submissions": validated_submissions
                }

                # Remove from active games
                if game_id in guild_state["active_trivia_games"]:
                    del guild_state["active_trivia_games"][game_id]

                # Save state
                set_state_value("trivia_history", guild_state["trivia_history"], guild_id)
                set_state_value("active_trivia_games", guild_state["active_trivia_games"], guild_id)

                logger.info("Moved game %s to history", game_id[:8])

            except Exception as exc:
                logger.error(
                    "Unexpected error closing game %s: %s",
                    game_id, exc, exc_info=True
                )

        await client.close()

    # Run the client
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(close_expired_games())
