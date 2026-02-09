"""Discord UI views and modals for trivia game interactions."""

import discord
from discord.ext import commands
from bot.app.utils.logger import get_logger
from bot.app.commands.trivia.trivia_submission_handler import (
    submit_trivia_answer,
    submit_batch_trivia_answer
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
        is_batch: bool = False
    ):
        super().__init__()
        self.game_id = game_id
        self.guild_id = guild_id
        self.bot = bot
        self.is_batch = is_batch

        # Create the answer input with different configurations for batch vs single
        if is_batch:
            placeholder = (
                "1. your answer\n"
                "2. your answer\n"
                "3. your answer\n"
                "Or use semicolons: 1. a; 2. b; 3. c"
            )
            label = "Your Answers (Line Breaks or Semicolons)"
            max_length = 2000  # More space for multiple answers
        else:
            # Create the answer input with the question as placeholder if provided
            placeholder = question if question else "Type your answer here..."
            # Discord has a 100 character limit for placeholders
            if len(placeholder) > 100:
                placeholder = placeholder[:97] + "..."
            label = "Your Answer"
            max_length = 500

        self.answer = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=max_length
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        # CRITICAL: Defer IMMEDIATELY to prevent "This interaction failed"
        # Modal submissions have a strict 3-second window
        await interaction.response.defer(ephemeral=True)

        # Now process the submission (validation can take several seconds)
        if self.is_batch:
            await submit_batch_trivia_answer(
                self.bot, interaction, self.answer.value, self.guild_id, self.game_id
            )
        else:
            await submit_trivia_answer(
                self.bot, interaction, self.answer.value, self.guild_id, self.game_id
            )


class ClearStatsConfirmView(discord.ui.View):
    """Confirmation view with buttons for clearing trivia stats."""

    def __init__(self, guild_id: str):
        super().__init__(timeout=60.0)  # 60 second timeout
        self.guild_id = guild_id
        self.confirmed = False

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle confirmation button click."""
        # Only the person who triggered the command can confirm
        if str(interaction.user.id) != str(self.guild_id).split(':')[0]:
            await interaction.response.send_message(
                "Only an administrator can confirm this action.",
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
