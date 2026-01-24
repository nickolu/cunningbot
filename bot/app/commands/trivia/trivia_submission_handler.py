"""Shared trivia answer submission logic."""

import datetime as dt
import discord

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.domain.trivia.answer_validator import validate_answer
from bot.app.utils.logger import get_logger

logger = get_logger()

# Feature flag for Redis migration
USE_REDIS = True  # Will be set via environment variable later


async def submit_trivia_answer(
    bot: discord.ext.commands.Bot,
    interaction: discord.Interaction,
    answer_text: str,
    guild_id: str,
    game_id: str = None
) -> None:
    """
    Submit an answer to an active trivia game.

    Args:
        bot: The Discord bot instance
        interaction: The Discord interaction (from slash command or modal)
        answer_text: The user's answer
        guild_id: The guild ID as a string
        game_id: Optional game ID to submit to (if known)
    """
    if USE_REDIS:
        await _submit_with_redis(bot, interaction, answer_text, guild_id, game_id)
    else:
        await _submit_with_json(bot, interaction, answer_text, guild_id, game_id)


async def _submit_with_redis(
    bot: discord.ext.commands.Bot,
    interaction: discord.Interaction,
    answer_text: str,
    guild_id: str,
    game_id: str = None
) -> None:
    """Submit answer using Redis (atomic, no race conditions)."""
    store = TriviaRedisStore()

    # Find active game for this channel
    active_games = await store.get_active_games(guild_id)

    game_data = None

    # If game_id is provided, use it directly
    if game_id:
        game_data = active_games.get(game_id)
        if not game_data:
            await interaction.response.send_message(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
            return
    else:
        # Find game by thread_id or channel_id
        channel_id = interaction.channel.id

        for gid, gdata in active_games.items():
            if gdata.get("thread_id") == channel_id or gdata.get("channel_id") == channel_id:
                game_id = gid
                game_data = gdata
                break

    if not game_id or not game_data:
        await interaction.response.send_message(
            "❌ No active trivia game found for this channel.", ephemeral=True
        )
        return

    # Prepare submission data
    user_id_str = str(interaction.user.id)
    submission_data = {
        "answer": answer_text,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "is_correct": None,  # Will be set by validation
        "feedback": None,
        "validated_at": None
    }

    # Validate answer immediately (for user feedback)
    correct_answer = game_data.get("correct_answer", "")
    question = game_data.get("question", "")

    try:
        logger.info(f"Validating answer for user {user_id_str} in game {game_id[:8]}")
        validation_result = await validate_answer(answer_text, correct_answer, question)

        # Update submission with validation results
        submission_data["is_correct"] = validation_result["is_correct"]
        submission_data["feedback"] = validation_result.get("feedback", "")
        submission_data["validated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    except Exception as e:
        logger.warning(f"Failed to validate answer immediately: {e}")
        # Continue with unvalidated submission

    # Submit atomically using Lua script
    result = await store.submit_answer_atomic(
        guild_id, game_id, user_id_str, submission_data
    )

    # Handle result
    if result.get("err"):
        error_code = result["err"]

        if error_code == "GAME_NOT_FOUND":
            await interaction.response.send_message(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
        elif error_code == "GAME_CLOSED":
            await interaction.response.send_message(
                "❌ This game has already been closed.", ephemeral=True
            )
        elif error_code == "WINDOW_CLOSED":
            await interaction.response.send_message(
                "❌ The answer window has closed. Wait for results!", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Failed to submit answer. Please try again.", ephemeral=True
            )
        return

    # Success! Send feedback based on validation
    if submission_data.get("is_correct"):
        feedback_message = (
            "✅ **Correct!** Your answer has been recorded.\n\n"
            "You'll see the official results when the answer window closes."
        )
    elif submission_data.get("is_correct") is False:
        explanation = game_data.get("explanation", "")
        feedback_message = (
            f"❌ **Sorry, that's not correct.**\n\n"
            f"The correct answer is: **{correct_answer}**\n\n"
        )
        if explanation:
            feedback_message += f"{explanation}\n\n"
        feedback_message += "You can submit a different answer if you'd like to try again."
    else:
        # Validation failed, generic message
        feedback_message = (
            "✅ Your answer has been recorded!\n\n"
            "We'll validate it when the answer window closes."
        )

    await interaction.response.send_message(feedback_message, ephemeral=True)


async def _submit_with_json(
    bot: discord.ext.commands.Bot,
    interaction: discord.Interaction,
    answer_text: str,
    guild_id: str,
    game_id: str = None
) -> None:
    """Submit answer using JSON (legacy, has race conditions)."""
    # Find active game for this channel (thread or parent channel)
    active_games = get_state_value_from_interaction(
        "active_trivia_games", guild_id
    ) or {}

    game_data = None

    # If game_id is provided, use it directly
    if game_id:
        game_data = active_games.get(game_id)
        if not game_data:
            await interaction.response.send_message(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
            return
    else:
        # Fall back to finding game by thread_id or channel_id
        channel_id = interaction.channel.id

        for gid, gdata in active_games.items():
            # Match by thread_id if we're in a thread, or by channel_id if in the parent channel
            if gdata.get("thread_id") == channel_id or gdata.get("channel_id") == channel_id:
                game_id = gid
                game_data = gdata
                break

    if not game_id or not game_data:
        await interaction.response.send_message(
            "❌ No active trivia game found for this channel.", ephemeral=True
        )
        return

    # Check if game has ended
    ends_at_str = game_data.get("ends_at")
    if ends_at_str:
        try:
            ends_at = dt.datetime.fromisoformat(ends_at_str)
            if dt.datetime.now(dt.timezone.utc) > ends_at:
                await interaction.response.send_message(
                    "❌ The answer window has closed. Wait for results!", ephemeral=True
                )
                return
        except (ValueError, TypeError):
            pass

    # Store submission (allow updates)
    if "submissions" not in game_data:
        game_data["submissions"] = {}

    user_id_str = str(interaction.user.id)
    submission_data = {
        "answer": answer_text,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "is_correct": None,  # Will be set by validation
        "feedback": None,
        "validated_at": None
    }

    game_data["submissions"][user_id_str] = submission_data

    # Save initial submission state
    active_games[game_id] = game_data
    set_state_value_from_interaction(
        "active_trivia_games", active_games, guild_id
    )

    # Immediately validate the answer
    correct_answer = game_data.get("correct_answer", "")
    question = game_data.get("question", "")

    try:
        logger.info(f"Validating answer for user {user_id_str} in game {game_id[:8]}")
        logger.info(f"Question: {question[:50]}... | Correct answer: {correct_answer}")
        validation_result = await validate_answer(answer_text, correct_answer, question)

        # Update submission with validation results
        submission_data["is_correct"] = validation_result["is_correct"]
        submission_data["feedback"] = validation_result.get("feedback", "")
        submission_data["validated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

        game_data["submissions"][user_id_str] = submission_data
        active_games[game_id] = game_data
        set_state_value_from_interaction(
            "active_trivia_games", active_games, guild_id
        )

        logger.info(f"Validation result: {validation_result['is_correct']}")

        # Send contextual feedback based on validation result
        if validation_result["is_correct"]:
            feedback_message = (
                "✅ **Correct!** Your answer has been recorded.\n\n"
                "You'll see the official results when the answer window closes."
            )
        else:
            explanation = game_data.get("explanation", "")
            feedback_message = (
                f"❌ **Sorry, that's not correct.**\n\n"
                f"The correct answer is: **{correct_answer}**\n\n"
            )
            if explanation:
                feedback_message += f"{explanation}\n\n"
            feedback_message += "You can submit a different answer if you'd like to try again."

        await interaction.response.send_message(feedback_message, ephemeral=True)

    except Exception as e:
        logger.warning(f"Failed to validate answer immediately: {e}")
        # Fallback: send generic message, closer will validate later
        await interaction.response.send_message(
            "✅ Your answer has been recorded!\n\n"
            "We'll validate it when the answer window closes.",
            ephemeral=True
        )
