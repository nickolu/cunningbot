"""Shared trivia answer submission logic."""

import datetime as dt
import discord

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)


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
    # Check if we're in a thread
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "❌ You can only submit answers in trivia game threads.", ephemeral=True
        )
        return

    # Find active game for this thread
    active_games = get_state_value_from_interaction(
        "active_trivia_games", guild_id
    ) or {}

    # Find game by thread_id
    game_id = None
    game_data = None
    for gid, gdata in active_games.items():
        if gdata.get("thread_id") == interaction.channel.id:
            game_id = gid
            game_data = gdata
            break

    if not game_id:
        await interaction.response.send_message(
            "❌ No active trivia game found in this thread.", ephemeral=True
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

    game_data["submissions"][str(interaction.user.id)] = {
        "answer": answer_text,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "is_correct": None  # Will be evaluated when game closes
    }

    # Save updated game state
    active_games[game_id] = game_data
    set_state_value_from_interaction(
        "active_trivia_games", active_games, guild_id
    )

    await interaction.response.send_message(
        "✅ Your answer has been recorded!", ephemeral=True
    )
