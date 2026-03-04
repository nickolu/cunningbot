"""Discord UI views and modals for trivia game interactions."""

import asyncio
import discord
from discord.ext import commands
from bot.app.utils.logger import get_logger
from bot.app.commands.trivia.trivia_submission_handler import (
    submit_trivia_answer,
    submit_batch_trivia_answer,
    submit_batch_question_button,
)

logger = get_logger()


class TriviaAnswerModal(discord.ui.Modal, title="Submit Trivia Answer"):
    """Modal dialog with text input for submitting trivia answers."""

    def __init__(
        self,
        game_id: str,
        guild_id: str,
        bot: commands.Bot,
        question: str = None,
        is_batch: bool = False,
        batch_question_num: int = None,
    ):
        super().__init__()
        self.game_id = game_id
        self.guild_id = guild_id
        self.bot = bot
        self.is_batch = is_batch
        self.batch_question_num = batch_question_num

        # Per-question batch modal (context menu on an individual question embed)
        if batch_question_num is not None:
            placeholder = "Type A, B, C, D or your full answer..."
            # Show a snippet of the question text in the label so the user has context.
            # The description contains the question + options; take only the first line.
            if question:
                first_line = question.split("\n")[0].strip()
                label = first_line if len(first_line) <= 45 else first_line[:42] + "..."
            else:
                label = f"Q{batch_question_num} — Your Answer"
                if len(label) > 45:
                    label = label[:45]
            max_length = 500
        elif is_batch:
            # Full batch modal (context menu on the overview embed)
            placeholder = (
                "1. your answer\n"
                "2. your answer\n"
                "3. your answer\n"
                "Or use semicolons: 1. a; 2. b; 3. c"
            )
            label = "Your Answers (Line Breaks or Semicolons)"
            max_length = 2000
        else:
            # Single AI question modal — show question text in both label and placeholder
            if question:
                label = question if len(question) <= 45 else question[:42] + "..."
                placeholder = question if len(question) <= 100 else question[:97] + "..."
            else:
                label = "Your Answer"
                placeholder = "Type your answer here..."
            max_length = 500

        self.answer = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=max_length,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        # CRITICAL: Defer IMMEDIATELY to prevent "This interaction failed"
        await interaction.response.defer(ephemeral=True)

        try:
            if self.batch_question_num is not None:
                # Per-question batch submission via modal (context menu fallback).
                # Modal uses defer(), so responses must use followup.send.
                await submit_batch_question_button(
                    interaction,
                    self.game_id,
                    self.guild_id,
                    self.batch_question_num,
                    self.answer.value,
                    use_followup=True,
                )
            elif self.is_batch:
                await submit_batch_trivia_answer(
                    self.bot, interaction, self.answer.value, self.guild_id, self.game_id
                )
            else:
                await submit_trivia_answer(
                    self.bot, interaction, self.answer.value, self.guild_id, self.game_id
                )
        except Exception as e:
            logger.error(f"Modal submission error: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    "❌ An error occurred processing your answer. Please try again.",
                    ephemeral=True,
                )
            except Exception:
                pass


class TriviaQuestionView(discord.ui.View):
    """Persistent A/B/C/D button view attached to each batch question message."""

    def __init__(
        self,
        batch_id: str,
        guild_id: str,
        question_num: int,
        option_labels: list[str],
        bot,
    ):
        super().__init__(timeout=None)  # persistent — survives bot restarts
        self.batch_id = batch_id
        self.guild_id = guild_id
        self.question_num = question_num
        self.bot = bot

        for label in option_labels:
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"trivia_q:{batch_id}:{question_num}:{label}",
            )
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, label: str):
        async def callback(interaction: discord.Interaction):
            # Respond IMMEDIATELY — Discord requires a response within 3 seconds.
            # We acknowledge first, then process in the background.
            try:
                await interaction.response.send_message(
                    "⏳ Got it! Checking your answer...",
                    ephemeral=True,
                )
            except Exception as e:
                logger.error(f"Failed to respond to trivia button: {e}")
                return

            async def process():
                try:
                    await submit_batch_question_button(
                        interaction,
                        self.batch_id,
                        self.guild_id,
                        self.question_num,
                        label,
                    )
                except Exception as e:
                    logger.error(f"Trivia button error for label={label}: {e}", exc_info=True)
                    try:
                        await interaction.edit_original_response(
                            content="❌ An error occurred processing your answer. Please try again."
                        )
                    except Exception:
                        pass

            asyncio.create_task(process())

        return callback


class ClearStatsConfirmView(discord.ui.View):
    """Confirmation view with buttons for clearing trivia stats."""

    def __init__(self, user_id: int):
        super().__init__(timeout=60.0)  # 60 second timeout
        self.user_id = user_id
        self.confirmed = False

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle confirmation button click."""
        # Only the person who triggered the command can confirm
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who started this reset can confirm it.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        self.confirmed = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle cancel button click."""
        await interaction.response.defer()
        self.confirmed = False
        self.stop()

    async def on_timeout(self):
        """Handle timeout."""
        self.confirmed = False


async def setup(bot: commands.Bot):
    """Empty setup function - this module is not a cog, just UI components."""
    pass
