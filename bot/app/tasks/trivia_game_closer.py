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
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.app.redis.locks import redis_lock
from bot.app.redis.client import get_redis_client, initialize_redis, close_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.domain.trivia.answer_validator import validate_answer

logger = logging.getLogger("TriviaGameCloser")
logging.basicConfig(level=logging.INFO)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def sanitize_answer_for_display(answer: str, max_length: int = 100) -> str:
    """Sanitize and truncate user answer for display in embed.

    Args:
        answer: Raw answer text
        max_length: Maximum length before truncation

    Returns:
        Sanitized answer string safe for Discord embed
    """
    if not answer or not answer.strip():
        return "(no answer)"

    # Trim whitespace
    answer = answer.strip()

    # Escape markdown characters to prevent formatting issues
    # Discord markdown: * _ ~ ` | >
    markdown_chars = ['*', '_', '~', '`', '|', '>']
    for char in markdown_chars:
        answer = answer.replace(char, f'\\{char}')

    # Truncate if too long
    if len(answer) > max_length:
        answer = answer[:max_length] + "..."

    return answer

async def close_expired_games() -> None:
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

                        # Check if this is a batch game
                        is_batch_game = game_data.get("question_count") is not None

                        if is_batch_game:
                            # Handle batch game
                            logger.info("Game %s is a batch game, handling differently", game_id[:8])

                            # Get batch submissions and questions
                            submissions = await store.get_batch_submissions(guild_id, game_id)
                            questions = await store.get_batch_questions(guild_id, game_id)
                            logger.info("Batch game %s has %d submissions and %d questions",
                                       game_id[:8], len(submissions), len(questions))

                            thread_id = game_data.get("thread_id")
                            category = game_data.get("category", "Unknown")

                            # Post results to Discord
                            if thread_id:
                                try:
                                    thread = client.get_channel(thread_id)
                                    if thread is None:
                                        thread = await client.fetch_channel(thread_id)  # type: ignore[attr-defined]

                                    if isinstance(thread, discord.Thread):
                                        # Build results message for batch
                                        embed = discord.Embed(
                                            title="✅ Trivia Results",
                                            color=0x00FF00,
                                            timestamp=dt.datetime.now(dt.timezone.utc)
                                        )

                                        # Add summary stats
                                        total_players = len(submissions)
                                        embed.add_field(
                                            name="Participation",
                                            value=f"**{total_players}** player(s) answered",
                                            inline=False
                                        )

                                        # Show top scores
                                        if submissions:
                                            scores = []
                                            for user_id, sub_data in submissions.items():
                                                # Try new format first, fall back to old format
                                                points = sub_data.get("points")
                                                if points is not None:
                                                    # New format with points
                                                    correct = sub_data.get("correct_count", 0)
                                                    total = sub_data.get("total_count", 0)
                                                else:
                                                    # Backward compatibility: old format
                                                    score_parts = sub_data.get("score", "0/0").split("/")
                                                    correct = int(score_parts[0])
                                                    total = int(score_parts[1])
                                                    # Estimate points (assume medium difficulty)
                                                    points = (correct * 15) + ((total - correct) * 5)

                                                scores.append((user_id, points, correct, total))

                                            # Sort by points (descending), then by correct count
                                            scores.sort(key=lambda x: (x[1], x[2]), reverse=True)

                                            # Show top 10 or all if less
                                            top_scores = scores[:10]
                                            score_lines = []
                                            for user_id, points, correct, total in top_scores:
                                                try:
                                                    user = await client.fetch_user(int(user_id))
                                                    user_mention = user.mention
                                                except:
                                                    user_mention = f"<@{user_id}>"

                                                # Display: "• @User: **45 pts** (3/6 correct)"
                                                score_lines.append(f"• {user_mention}: **{points} pts** ({correct}/{total})")

                                            embed.add_field(
                                                name="🏆 Top Scores",
                                                value="\n".join(score_lines) if score_lines else "No scores",
                                                inline=False
                                            )

                                        # Add per-question correct answers
                                        question_lines = []
                                        for i in range(1, len(questions) + 1):
                                            q_data = questions[str(i)]
                                            correct_answer = q_data.get("correct_answer", "Unknown")
                                            source = q_data.get("source", "")
                                            difficulty = q_data.get("difficulty") or ""
                                            difficulty = difficulty.capitalize() if difficulty else ""

                                            # Determine type label
                                            if source == "ai":
                                                type_label = "AI"
                                            else:
                                                type_label = difficulty if difficulty else "Unknown"

                                            # Count how many got it right
                                            correct_count = sum(
                                                1 for sub in submissions.values()
                                                if sub.get("answers", {}).get(str(i), {}).get("is_correct", False)
                                            )

                                            question_lines.append(
                                                f"**Q{i} ({type_label}):** {correct_answer}\n✅ {correct_count} correct"
                                            )

                                        # Add to embed (split into multiple fields if needed for Discord's 1024 char limit)
                                        if question_lines:
                                            current_field = []
                                            for line in question_lines:
                                                # Check if adding this line would exceed field limit
                                                current_length = sum(len(l) + 2 for l in current_field)  # +2 for \n\n separator
                                                if current_length + len(line) > 1000:
                                                    # Start new field
                                                    field_name = "Correct Answers" if not any("Correct Answers" in str(f.name) for f in embed.fields) else "\u200b"
                                                    embed.add_field(
                                                        name=field_name,
                                                        value="\n\n".join(current_field),
                                                        inline=False
                                                    )
                                                    current_field = [line]
                                                else:
                                                    current_field.append(line)

                                            # Add final field
                                            if current_field:
                                                field_name = "Correct Answers" if not any("Correct Answers" in str(f.name) for f in embed.fields) else "\u200b"
                                                embed.add_field(
                                                    name=field_name,
                                                    value="\n\n".join(current_field),
                                                    inline=False
                                                )

                                        embed.set_footer(text=f"Category: {category} • Batch ID: {game_id[:8]}")

                                        logger.info("Posting batch results for game %s to thread %s", game_id[:8], thread_id)
                                        await thread.send(embed=embed)

                                        # Post AI explanation follow-up if there are AI questions
                                        from bot.app.commands.trivia.trivia_submission_handler import post_ai_explanation_followup
                                        channel = client.get_channel(game_data.get("channel_id"))
                                        if channel is None:
                                            channel = await client.fetch_channel(game_data.get("channel_id"))
                                        await post_ai_explanation_followup(channel, thread, questions, game_id)

                                        logger.info("Posted batch results to thread %s", thread_id)

                                except discord.Forbidden:
                                    logger.error("Missing permissions to post in thread %s", thread_id)
                                except discord.HTTPException as exc:
                                    logger.error("HTTP error posting to thread %s: %s", thread_id, exc)
                                except Exception as exc:
                                    logger.error(
                                        "Unexpected error posting to thread %s: %s",
                                        thread_id, exc, exc_info=True
                                    )

                            # Move batch game to history in Redis
                            await store.move_batch_to_history(guild_id, game_id, game_data, questions, submissions)

                            # Delete from active games
                            await store.delete_batch_game(guild_id, game_id)

                            logger.info("Moved batch game %s to history and removed from active games", game_id[:8])
                            continue

                        # Get submissions from Redis (single question game)
                        submissions = await store.get_submissions(guild_id, game_id)
                        logger.info("Game %s has %d submissions to process", game_id[:8], len(submissions))

                        # Extract game metadata
                        thread_id = game_data.get("thread_id")
                        question = game_data.get("question", "Unknown question")
                        correct_answer = game_data.get("correct_answer", "Unknown")
                        category = game_data.get("category", "Unknown")
                        explanation = game_data.get("explanation", "")
                        seed = game_data.get("seed", "")

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

                                    # Batch fetch users before building results
                                    user_cache = {}
                                    for user_id in validated_submissions.keys():
                                        try:
                                            user = await client.fetch_user(int(user_id))
                                            user_cache[user_id] = user
                                        except discord.NotFound:
                                            # User left server or deleted account
                                            user_cache[user_id] = None
                                            logger.warning(f"User {user_id} not found")
                                        except Exception as exc:
                                            logger.warning(f"Error fetching user {user_id}: {exc}")
                                            user_cache[user_id] = None

                                    # Group submissions by correctness
                                    correct_submissions = []
                                    incorrect_submissions = []

                                    for user_id, submission in validated_submissions.items():
                                        user = user_cache.get(user_id)
                                        if user is None:
                                            # Handle deleted/missing user
                                            user_mention = f"<@{user_id}>"  # Discord will show "Unknown User"
                                        else:
                                            user_mention = user.mention

                                        answer = submission["answer"]
                                        sanitized_answer = sanitize_answer_for_display(answer)

                                        if submission["is_correct"]:
                                            correct_submissions.append((user_mention, sanitized_answer))
                                        else:
                                            incorrect_submissions.append((user_mention, sanitized_answer))

                                    # Display correct answers
                                    if correct_submissions:
                                        correct_text = []
                                        for user_mention, answer in correct_submissions:
                                            correct_text.append(f"• {user_mention}: **{answer}**")

                                        embed.add_field(
                                            name=f"✅ Correct ({len(correct_submissions)})",
                                            value="\n".join(correct_text),
                                            inline=False
                                        )

                                    # Display incorrect answers
                                    if incorrect_submissions:
                                        incorrect_text = []
                                        for user_mention, answer in incorrect_submissions:
                                            incorrect_text.append(f"• {user_mention}: {answer}")

                                        # Discord embed field value limit is 1024 chars
                                        # If too many incorrect answers, split or truncate
                                        incorrect_value = "\n".join(incorrect_text)
                                        if len(incorrect_value) > 1024:
                                            # Truncate and add note
                                            incorrect_value = incorrect_value[:1000] + "\n... and more"

                                        embed.add_field(
                                            name=f"❌ Incorrect ({len(incorrect_submissions)})",
                                            value=incorrect_value,
                                            inline=False
                                        )

                                    # If nobody answered
                                    if not correct_submissions and not incorrect_submissions:
                                        embed.add_field(
                                            name="Results",
                                            value="No one answered this question.",
                                            inline=False
                                        )

                                    # Calculate and display points
                                    if validated_submissions:
                                        # Import the calculate_question_points function
                                        from bot.app.commands.trivia.trivia_submission_handler import calculate_question_points

                                        points_by_user = {}
                                        for user_id, sub_data in submissions.items():
                                            points = sub_data.get("points")
                                            if points is None:
                                                # Backward compatibility
                                                is_correct = validated_submissions.get(user_id, {}).get("is_correct", False)
                                                difficulty = game_data.get("difficulty", "medium")
                                                source = game_data.get("source", "opentdb")
                                                points = calculate_question_points(is_correct, difficulty, source)
                                            points_by_user[user_id] = points

                                        # Sort by points
                                        sorted_users = sorted(points_by_user.items(), key=lambda x: x[1], reverse=True)

                                        # Add points field to embed
                                        points_lines = []
                                        for user_id, points in sorted_users[:10]:
                                            user = user_cache.get(user_id)
                                            if user is None:
                                                user_mention = f"<@{user_id}>"
                                            else:
                                                user_mention = user.mention
                                            points_lines.append(f"• {user_mention}: **{points} pts**")

                                        if points_lines:
                                            embed.add_field(
                                                name="🏆 Points",
                                                value="\n".join(points_lines),
                                                inline=False
                                            )

                                    # Add participation stats
                                    embed.add_field(
                                        name="Participation",
                                        value=f"**{len(validated_submissions)}** player(s) answered",
                                        inline=False
                                    )

                                    footer_text = f"Category: {category} • Game ID: {game_id[:8]}"
                                    if seed:
                                        footer_text += f" • Seed: {seed}"
                                    embed.set_footer(text=footer_text)

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


if __name__ == "__main__":
    asyncio.run(close_expired_games())
