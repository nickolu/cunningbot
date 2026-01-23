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
from bot.domain.trivia.answer_validator import validate_answer

logger = logging.getLogger("TriviaGameCloser")
logging.basicConfig(level=logging.INFO)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


async def close_expired_games() -> None:
    """Main entry point for closing expired trivia games."""
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
        for game_id, game_data in active_games.items():
            ends_at_str = game_data.get("ends_at")
            if not ends_at_str:
                continue

            try:
                ends_at = dt.datetime.fromisoformat(ends_at_str)
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
                # Validate all submissions
                validated_submissions = {}
                for user_id, submission in submissions.items():
                    user_answer = submission.get("answer", "")

                    # Validate answer using LLM
                    validation_result = await validate_answer(
                        user_answer, correct_answer, question
                    )

                    validated_submissions[user_id] = {
                        "answer": user_answer,
                        "is_correct": validation_result["is_correct"]
                    }

                    logger.info(
                        "Validated answer for user %s: %s -> %s",
                        user_id, user_answer, validation_result["is_correct"]
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
