"""Shared trivia answer submission logic."""

import datetime as dt
import discord

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)
from bot.domain.trivia.answer_validator import validate_answer
from bot.app.utils.logger import get_logger

logger = get_logger()


async def submit_trivia_answer(
    bot: discord.ext.commands.Bot,
    interaction: discord.Interaction,
    answer_text: str,
    guild_id: str
) -> None:
    """
    Submit an answer to an active trivia game.

    Args:
        bot: The Discord bot instance
        interaction: The Discord interaction (from slash command or modal)
        answer_text: The user's answer
        guild_id: The guild ID as a string
    """
    # Find active game for this channel (thread or parent channel)
    active_games = get_state_value_from_interaction(
        "active_trivia_games", guild_id
    ) or {}

    # Find game by thread_id (if in thread) or channel_id (if in channel)
    game_id = None
    game_data = None
    channel_id = interaction.channel.id

    for gid, gdata in active_games.items():
        # Match by thread_id if we're in a thread, or by channel_id if in the parent channel
        if gdata.get("thread_id") == channel_id or gdata.get("channel_id") == channel_id:
            game_id = gid
            game_data = gdata
            break

    if not game_id:
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
